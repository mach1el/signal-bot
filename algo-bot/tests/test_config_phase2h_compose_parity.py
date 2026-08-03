"""Phase 2H Compose / deployment effective-value parity.

Proves the Python bot's reduced Compose environment yields the same
``PythonRuntimeConfig`` leaf values (and exact types) as the pre-cleanup
full auto-trade anchor inheritance, while the cTrader engine environment
remains byte-identical.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.no_database

_ALGO_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _ALGO_ROOT.parent
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "phase2h"

_SAFE = {
  "TELEGRAM_BOT_TOKEN": "phase-2h-parity-token",
  "SIGNAL_VIP_CHANNEL_ID": "-1001999",
  "POSTGRES_PASSWORD": "phase-2h-postgres",
  "DATABASE_URL": "postgresql://u:p@localhost/db",
  "REDIS_URL": "redis://localhost:6379/0",
}

_LEAF_PROBE = r"""
import json
from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.core.config import runtime_config

def resolve(path):
  value = runtime_config
  for part in path.split("."):
    value = getattr(value, part)
  return value

leaves = {}
for entry in iter_catalog_entries(PythonRuntimeConfig):
  if entry.deprecated:
    continue
  try:
    value = resolve(entry.path)
  except AttributeError:
    continue
  leaves[entry.path] = {
    "type": f"{type(value).__module__}.{type(value).__qualname__}",
    "repr": repr(value),
  }
print(json.dumps({
  "runtime_type": type(runtime_config).__name__,
  "profile": runtime_config.runtime.profile,
  "leaves": leaves,
}, sort_keys=True))
"""


def _compose_config() -> dict:
  completed = subprocess.run(
    ["docker", "compose", "--env-file", str(_REPO_ROOT / ".env"),
     "config", "--format", "json"],
    cwd=_REPO_ROOT,
    check=True,
    capture_output=True,
    text=True,
  )
  return json.loads(completed.stdout)


def _probe(environment: dict[str, str], authority: str) -> dict:
  env = {
    "PATH": os.environ.get("PATH", ""),
    "HOME": os.environ.get("HOME", ""),
    "PYTHONPATH": str(_ALGO_ROOT),
  }
  env.update({str(key): str(value) for key, value in environment.items()})
  env.update(_SAFE)
  env["APEXVOID_CONFIG_AUTHORITY"] = authority
  completed = subprocess.run(
    [sys.executable, "-c", _LEAF_PROBE],
    cwd=_ALGO_ROOT,
    env=env,
    check=True,
    capture_output=True,
    text=True,
  )
  return json.loads(completed.stdout)


def _compare_leaves(left: dict, right: dict) -> list[str]:
  mismatches: list[str] = []
  for path in sorted(set(left) | set(right)):
    if left.get(path) != right.get(path):
      mismatches.append(path)
  return mismatches


@pytest.mark.parametrize("authority", ["legacy", "canonical"])
def test_bot_compose_effective_runtime_values_unchanged(authority: str):
  cfg = _compose_config()
  engine = dict(cfg["services"]["ctrader-engine"]["environment"])
  bot = dict(cfg["services"]["bot"]["environment"])
  # Pre-cleanup bot ≈ full cTrader auto-trade anchor + bot LOG overrides.
  full = dict(engine)
  full.update({
    key: bot[key]
    for key in (
      "APEXVOID_CONFIG_AUTHORITY",
      "LOG_DIR",
      "LOG_RETENTION_DAYS",
      "LOG_FILE_ENABLED",
    )
    if key in bot
  })
  full_probe = _probe(full, authority)
  mini_probe = _probe(bot, authority)
  assert full_probe["profile"] == mini_probe["profile"] == "demo_eval"
  mismatches = _compare_leaves(full_probe["leaves"], mini_probe["leaves"])
  assert mismatches == [], mismatches[:20]
  assert full_probe["leaves"]["strategies.mapped_zone.enabled"]["repr"] == "False"
  assert (
    full_probe["leaves"]["actionability.gates.market_map_guard_enabled"]["repr"]
    == "False"
  )


def _compose_config_from_file(compose_file: Path) -> dict:
  completed = subprocess.run(
    [
      "docker", "compose",
      "--env-file", str(_REPO_ROOT / ".env"),
      "-f", str(compose_file),
      "--project-directory", str(_REPO_ROOT),
      "config", "--format", "json",
    ],
    cwd=_REPO_ROOT,
    check=True,
    capture_output=True,
    text=True,
  )
  return json.loads(completed.stdout)


def test_ctrader_compose_environment_unchanged_vs_pre_cleanup(tmp_path: Path):
  """cTrader effective environment must match master/pre-cleanup compose."""
  pre_file = tmp_path / "docker-compose.pre.yml"
  pre_file.write_text(
    subprocess.check_output(
      ["git", "show", "HEAD:docker-compose.yml"],
      cwd=_REPO_ROOT,
      text=True,
    ),
    encoding="utf-8",
  )
  # If HEAD already contains Phase 2H changes (same branch), fall back to the
  # committed pre-cleanup fixture captured before the bot anchor was removed.
  pre_cfg = _compose_config_from_file(pre_file)
  post_cfg = _compose_config()
  pre_engine = {
    k: str(v)
    for k, v in pre_cfg["services"]["ctrader-engine"]["environment"].items()
  }
  post_engine = {
    k: str(v)
    for k, v in post_cfg["services"]["ctrader-engine"]["environment"].items()
  }
  if "<<: *auto-trade-environment" in pre_file.read_text(encoding="utf-8"):
    assert post_engine == pre_engine
  else:
    expected = {
      k: str(v)
      for k, v in json.loads(
        (_FIXTURES / "ctrader_compose_env_pre.json").read_text(encoding="utf-8")
      ).items()
    }
    assert post_engine == expected



def test_bot_compose_env_is_minimal():
  cfg = _compose_config()
  bot = cfg["services"]["bot"]["environment"]
  assert set(bot) == {
    "APEXVOID_CONFIG_AUTHORITY",
    "AUTO_TRADE_PROFILE",
    "AUTO_TRADE_MAPPED_ZONE_ENABLED",
    "AUTO_TRADE_MARKET_MAP_GUARD_ENABLED",
    "LOG_DIR",
    "LOG_RETENTION_DAYS",
    "LOG_FILE_ENABLED",
  }
  assert bot["APEXVOID_CONFIG_AUTHORITY"] == "legacy"
  assert bot["AUTO_TRADE_PROFILE"] == "demo_eval"
  assert str(bot["AUTO_TRADE_MAPPED_ZONE_ENABLED"]).lower() == "false"
  assert str(bot["AUTO_TRADE_MARKET_MAP_GUARD_ENABLED"]).lower() == "false"


def test_compose_demo_known_differences_preserved():
  """Direct demo_eval enables mapped-zone; root Compose keeps it off."""
  demo = _probe({"AUTO_TRADE_PROFILE": "demo_eval"}, "canonical")
  compose = _probe(dict(_compose_config()["services"]["bot"]["environment"]), "canonical")
  assert demo["leaves"]["strategies.mapped_zone.enabled"]["repr"] == "True"
  assert compose["leaves"]["strategies.mapped_zone.enabled"]["repr"] == "False"
  assert demo["leaves"]["actionability.gates.market_map_guard_enabled"]["repr"] == "True"
  assert (
    compose["leaves"]["actionability.gates.market_map_guard_enabled"]["repr"]
    == "False"
  )
