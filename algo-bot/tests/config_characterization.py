"""Deterministic, secret-safe snapshots of the active legacy config loader."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterator
from unittest.mock import patch

from pydantic import AliasChoices
from pydantic_core import PydanticUndefined

from app.core.config import Settings


REPO_ROOT = Path(__file__).resolve().parents[2]
PHASE1_CATALOG = (
  REPO_ROOT / "docs/configuration/config-catalog-phase-1.json"
)
SNAPSHOT_PATH = (
  Path(__file__).parent / "fixtures/config-phase-2a-characterization.json"
)
REQUIRED_ENV = {
  "TELEGRAM_BOT_TOKEN": "phase-2a-characterization-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
}


def _catalog() -> dict[str, Any]:
  return json.loads(PHASE1_CATALOG.read_text())


def _secret_legacy_attrs() -> set[str]:
  return {
    item["legacy_attr"]
    for item in _catalog()["fields"]
    if item["secret"] and item["legacy_attr"]
  }


def _json_value(value: Any) -> Any:
  if isinstance(value, tuple):
    return [_json_value(item) for item in value]
  if isinstance(value, list):
    return [_json_value(item) for item in value]
  if isinstance(value, dict):
    return {
      str(key): _json_value(item)
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  if isinstance(value, (str, int, float, bool)) or value is None:
    return value
  return repr(value)


def _aliases(field: Any) -> list[str]:
  alias = field.validation_alias
  if isinstance(alias, AliasChoices):
    return [str(choice) for choice in alias.choices]
  if alias is None:
    return []
  return [str(alias)]


def legacy_inventory() -> list[dict[str, Any]]:
  catalog_by_attr = {
    item["legacy_attr"]: item
    for item in _catalog()["fields"]
    if item["legacy_attr"]
  }
  secrets = _secret_legacy_attrs()
  result = []
  for name, field in Settings.model_fields.items():
    default = (
      "<required>" if field.default is PydanticUndefined
      else _json_value(field.default)
    )
    if name in secrets:
      default = "<redacted>"
    result.append({
      "name": name,
      "type": str(field.annotation),
      "default": default,
      "validation_alias_order": _aliases(field),
      "required": field.is_required(),
      "profile_behavior": catalog_by_attr[name]["profile_behavior"],
    })
  return result


@contextmanager
def _isolated_environment(values: dict[str, str]) -> Iterator[None]:
  preserved = {
    key: os.environ[key]
    for key in ("PATH", "HOME", "DOCKER_HOST")
    if key in os.environ
  }
  with patch.dict(os.environ, {**preserved, **values}, clear=True):
    yield


def _settings_values(environment: dict[str, str]) -> dict[str, Any]:
  with _isolated_environment(environment):
    settings = Settings(_env_file=None)
  values = settings.model_dump(mode="json")
  for name in _secret_legacy_attrs():
    if name in values:
      values[name] = "<redacted>"
  return {name: values[name] for name in Settings.model_fields}


def direct_conservative_fixture() -> dict[str, Any]:
  return _settings_values(dict(REQUIRED_ENV))


def direct_demo_eval_fixture() -> dict[str, Any]:
  return _settings_values({
    **REQUIRED_ENV,
    "AUTO_TRADE_PROFILE": "demo_eval",
  })


@lru_cache(maxsize=1)
def root_compose_environment() -> dict[str, str]:
  environment = {
    key: value
    for key, value in os.environ.items()
    if key in {"PATH", "HOME", "DOCKER_HOST"}
  }
  result = subprocess.run(
    ["docker", "compose", "config", "--format", "json"],
    cwd=REPO_ROOT,
    env=environment,
    check=True,
    capture_output=True,
    text=True,
  )
  compose = json.loads(result.stdout)
  values = compose["services"]["bot"]["environment"]
  return {
    str(key): str(value).lower() if isinstance(value, bool) else str(value)
    for key, value in values.items()
    if value is not None
  }


def root_compose_demo_fixture() -> dict[str, Any]:
  return _settings_values({**root_compose_environment(), **REQUIRED_ENV})


def test_conftest_fixture() -> dict[str, Any]:
  return _settings_values({
    "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    "TELEGRAM_CHAT_ID": "-100123456789",
    "DATABASE_URL": (
      "postgresql://apexvoid:apexvoid@localhost:55432/signals"
    ),
  })


def _method_body(source: str, signature: str) -> str:
  start = source.index(signature)
  brace = source.index("{", start)
  depth = 0
  for index in range(brace, len(source)):
    if source[index] == "{":
      depth += 1
    elif source[index] == "}":
      depth -= 1
      if depth == 0:
        return source[start:index + 1]
  raise ValueError(f"unclosed C# method {signature}")


def _normalized_source_hash(source: str) -> str:
  normalized = re.sub(r"\s+", " ", source).strip()
  return hashlib.sha256(normalized.encode()).hexdigest()


def csharp_inventory() -> dict[str, Any]:
  auto_path = REPO_ROOT / "ctrader-engine/src/AutoTradeOptions.cs"
  feed_path = REPO_ROOT / "ctrader-engine/src/FeedOptions.cs"
  engine_path = REPO_ROOT / "ctrader-engine/src/AutoTradeEngine.cs"
  auto_source = auto_path.read_text()
  feed_source = feed_path.read_text()
  engine_source = engine_path.read_text()
  auto_method = _method_body(
    auto_source, "public static AutoTradeOptions FromEnvironment()",
  )
  feed_method = _method_body(
    feed_source, "public static FeedOptions FromEnvironment()",
  )
  phase1 = _catalog()
  ctrader_rows = [
    {
      "item_id": item["item_id"],
      "canonical_env": item["canonical_env"],
      "default_ctrader": item["default_ctrader"],
      "owner": item["owner"],
    }
    for item in phase1["fields"]
    if item["owner"] in {"ctrader", "shared"}
    and item["canonical_env"]
  ]
  return {
    "ctrader_catalog_rows": ctrader_rows,
    "auto_trade_env_bindings": sorted(set(re.findall(
      r'resolver\.(?:Bool|Int|String|Decimal|IntList)\(\s*"([A-Z][A-Z0-9_]+)"',
      auto_method,
    ))),
    "feed_env_bindings": sorted(set(re.findall(
      r'Env\(\s*"([A-Z][A-Z0-9_]+)"', feed_method,
    ))),
    "auto_trade_from_environment_sha256": _normalized_source_hash(auto_method),
    "feed_from_environment_sha256": _normalized_source_hash(feed_method),
    "known_constructor_vs_environment_default": {
      "AUTO_TRADE_CONTRACT_MODE": {
        "constructor": "legacy_v6",
        "from_environment": "v7_only",
      },
    },
    "direct_hardcoded_streams": {
      "AutoTradeEngine.ManualCommandStream": (
        "manual_trade:commands" if "manual_trade:commands" in engine_source
        else "<missing>"
      ),
      "Python bars channel": "bars:new",
    },
    "protocol_constants": [
      {
        "item_id": item["item_id"],
        "path": item["proposed_path"],
        "python": item["default_python"],
        "ctrader": item["default_ctrader"],
      }
      for item in phase1["fields"]
      if item["item_id"].startswith("hardcoded.contract.")
    ],
  }


def characterization() -> dict[str, Any]:
  phase1 = _catalog()
  return {
    "generated_from_commit": phase1["generated_from_commit"],
    "phase1_counts": phase1["counts"],
    "legacy_inventory": legacy_inventory(),
    "fixtures": {
      "direct_conservative": direct_conservative_fixture(),
      "direct_demo_eval": direct_demo_eval_fixture(),
      "root_compose_demo_eval": root_compose_demo_fixture(),
      "test_conftest": test_conftest_fixture(),
    },
    "known_conflict_item_ids": [
      item["item_id"]
      for item in phase1["fields"]
      if any(
        issue in {
          "Compose default differs from Python schema default",
          "C# conservative default differs from Python effective conservative value",
          "Python/C# deprecated alias set differs",
          "duplicated Python defaults disagree",
          ".env.example value differs from C# default",
        }
        for issue in item["issues"]
      )
    ],
    "csharp": csharp_inventory(),
  }


def load_snapshot() -> dict[str, Any]:
  return json.loads(SNAPSHOT_PATH.read_text())
