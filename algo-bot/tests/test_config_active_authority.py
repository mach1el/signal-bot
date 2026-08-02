"""Process-isolated startup authority, failure, logging, and rollback tests."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.no_database

_ALGO_ROOT = Path(__file__).parents[1]
_SAFE = {
  "PYTHONPATH": str(_ALGO_ROOT),
  "TELEGRAM_BOT_TOKEN": "phase-2d2-process-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2d2-process-postgres",
}
_PROBE = r"""
import hashlib, json
from app.configuration.generated.legacy_access import DERIVED_LEGACY_PROPERTIES, DIRECT_LEGACY_PATHS, SECRET_LEGACY_FIELDS
from app.core.config import active_configuration_authority, active_configuration_startup_message, settings
rows=[]
for name in (*DIRECT_LEGACY_PATHS, *DERIVED_LEGACY_PROPERTIES):
  value=getattr(settings,name)
  rows.append((name,type(value).__name__,"<redacted>" if name in SECRET_LEGACY_FIELDS else repr(value)))
print(json.dumps({
  "authority": active_configuration_authority().value,
  "type": type(settings).__name__,
  "identity": id(settings),
  "parity": hashlib.sha256(repr(rows).encode()).hexdigest(),
  "count": len(rows),
  "startup": active_configuration_startup_message(),
}, sort_keys=True))
"""


def _environment(authority="__omitted__", **updates):
  environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}}
  environment.update(_SAFE)
  if authority != "__omitted__":
    environment["APEXVOID_CONFIG_AUTHORITY"] = authority
  environment.update(updates)
  return environment


def _run(authority="__omitted__", *, probe=_PROBE, drop=(), **updates):
  environment = _environment(authority, **updates)
  for name in drop:
    environment.pop(name, None)
  return subprocess.run(
    [sys.executable, "-c", probe],
    cwd=_ALGO_ROOT,
    env=environment,
    capture_output=True,
    text=True,
  )


def _success(authority="__omitted__"):
  process = _run(authority)
  assert process.returncode == 0, process.stderr
  return json.loads(process.stdout)


def test_core_config_defaults_to_legacy_settings():
  assert _success()["type"] == "Settings"


def test_core_config_selects_canonical_facade():
  assert _success("canonical")["type"] == "CanonicalSettingsFacade"


def test_core_config_canonical_is_authoritative():
  assert _success("canonical")["authority"] == "canonical"


def test_core_config_legacy_is_authoritative():
  assert _success("legacy")["authority"] == "legacy"


def test_canonical_selection_does_not_construct_legacy_fallback():
  probe = r"""
import os
os.environ['APEXVOID_CONFIG_AUTHORITY']='canonical'
import app.core.config as config
original=config.Settings
class Forbidden:
  model_config=original.model_config
  def __new__(cls,*args,**kwargs): raise AssertionError('legacy constructed')
config.Settings=Forbidden
print(type(config.build_active_settings()).__name__)
"""
  process = _run("canonical", probe=probe)
  assert process.returncode == 0, process.stderr
  assert process.stdout.strip() == "CanonicalSettingsFacade"


def test_invalid_canonical_configuration_fails_startup():
  process = _run("canonical", drop=("POSTGRES_PASSWORD",))
  assert process.returncode != 0
  assert "missing_required_input" in process.stderr


def test_invalid_authority_fails_startup():
  process = _run("fallback")
  assert process.returncode != 0
  assert "must be legacy or canonical" in process.stderr


def test_canonical_failure_does_not_fallback_to_legacy():
  process = _run("canonical", drop=("POSTGRES_PASSWORD",))
  assert process.returncode != 0
  assert "CanonicalSettingsFacade" not in process.stdout
  assert "rollback_action" in process.stderr


def test_restart_rollback_canonical_to_legacy():
  canonical = _success("canonical")
  legacy = _success("legacy")
  assert canonical["type"] == "CanonicalSettingsFacade"
  assert legacy["type"] == "Settings"


def test_restart_rollback_does_not_persist_canonical_state():
  _success("canonical")
  restarted = _success("legacy")
  assert restarted["authority"] == "legacy"


def test_restart_rollback_preserves_values_and_types():
  canonical = _success("canonical")
  legacy = _success("legacy")
  assert canonical["count"] == legacy["count"] == 320
  assert canonical["parity"] == legacy["parity"]


def test_startup_log_reports_legacy_authority():
  assert _success("legacy")["startup"] == "configuration_authority=legacy"


def test_startup_log_reports_canonical_authority():
  message = _success("canonical")["startup"]
  assert "configuration_authority=canonical" in message
  assert "configuration_facade_fields=316" in message
  assert "configuration_derived_fields=4" in message


def test_startup_log_contains_no_secret_values():
  messages = _success("canonical")["startup"] + _success("legacy")["startup"]
  assert _SAFE["TELEGRAM_BOT_TOKEN"] not in messages
  assert _SAFE["POSTGRES_PASSWORD"] not in messages
