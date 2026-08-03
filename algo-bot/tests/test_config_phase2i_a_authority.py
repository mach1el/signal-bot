"""Phase 2I-A process-isolated authority, diagnostic, and parity tests.

Exercises the composition-root diagnostics under real, separate interpreter
starts: explicit canonical/legacy, implicit legacy (missing env), the legacy
deprecation diagnostic, the implicit-authority warning, canonical fail-closed
behavior, restart rollback, and legacy/canonical leaf value+type parity under a
reduced bot environment. Uses ``env``-style clean dicts with safe tokens and
avoids the SIGNAL_VIP_CHANNEL_ID / TELEGRAM_CHAT_ID conflict.
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
_SAFE = {
  "PYTHONPATH": str(_ALGO_ROOT),
  "TELEGRAM_BOT_TOKEN": "phase-2i-a-process-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2i-a-process-postgres",
  "DATABASE_URL": "postgresql://u:p@localhost/db",
}
# Reduced bot environment mirroring the Compose bot service pins.
_REDUCED_BOT = {
  "AUTO_TRADE_PROFILE": "demo_eval",
  "AUTO_TRADE_MAPPED_ZONE_ENABLED": "false",
  "AUTO_TRADE_MARKET_MAP_GUARD_ENABLED": "false",
}

_DIAGNOSTICS_PROBE = r"""
import json
from app.core.config import (
  runtime_config,
  active_configuration_authority,
  active_configuration_authority_explicit,
  active_configuration_startup_message,
  active_configuration_deprecation_message,
  active_configuration_implicit_authority_warning,
)
print(json.dumps({
  "runtime_type": type(runtime_config).__name__,
  "authority": active_configuration_authority().value,
  "explicit": active_configuration_authority_explicit(),
  "startup": active_configuration_startup_message(),
  "deprecation": active_configuration_deprecation_message(),
  "implicit": active_configuration_implicit_authority_warning(),
}))
"""

_PARITY_PROBE = r"""
import hashlib, json
from app.configuration.generated.legacy_access import (
  DERIVED_LEGACY_PROPERTIES, DIRECT_LEGACY_PATHS, SECRET_LEGACY_FIELDS,
)
from app.core.config import active_configuration_authority, settings
rows = []
for name in (*DIRECT_LEGACY_PATHS, *DERIVED_LEGACY_PROPERTIES):
  value = getattr(settings, name)
  rows.append((
    name,
    type(value).__name__,
    "<redacted>" if name in SECRET_LEGACY_FIELDS else repr(value),
  ))
print(json.dumps({
  "authority": active_configuration_authority().value,
  "count": len(rows),
  "parity": hashlib.sha256(repr(rows).encode()).hexdigest(),
}))
"""


def _environment(authority="__omitted__", *, reduced=False, **updates):
  environment = {
    key: os.environ.get(key, "")
    for key in ("PATH", "HOME")
  }
  environment.update(_SAFE)
  if reduced:
    environment.update(_REDUCED_BOT)
  if authority != "__omitted__":
    environment["APEXVOID_CONFIG_AUTHORITY"] = authority
  environment.update(updates)
  return environment


def _run(authority="__omitted__", *, probe=_DIAGNOSTICS_PROBE, drop=(),
         reduced=False, **updates):
  environment = _environment(authority, reduced=reduced, **updates)
  for name in drop:
    environment.pop(name, None)
  return subprocess.run(
    [sys.executable, "-c", probe],
    cwd=_ALGO_ROOT,
    env=environment,
    capture_output=True,
    text=True,
  )


def _diagnostics(authority="__omitted__", **kwargs):
  process = _run(authority, **kwargs)
  assert process.returncode == 0, process.stderr
  return json.loads(process.stdout)


# --- explicit canonical -----------------------------------------------------

def test_explicit_canonical_selects_canonical_and_is_silent():
  result = _diagnostics("canonical")
  assert result["runtime_type"] == "PythonRuntimeConfig"
  assert result["authority"] == "canonical"
  assert result["explicit"] is True
  assert result["deprecation"] is None
  assert result["implicit"] is None
  assert result["startup"].startswith("configuration_authority=canonical")


# --- explicit legacy --------------------------------------------------------

def test_explicit_legacy_emits_deprecation_not_implicit():
  result = _diagnostics("legacy")
  assert result["runtime_type"] == "LegacyCanonicalConfigView"
  assert result["authority"] == "legacy"
  assert result["explicit"] is True
  assert result["deprecation"] == (
    "configuration_authority=legacy configuration_authority_deprecated=true "
    "rollback_mode=true planned_removal_phase=2I-B"
  )
  assert result["implicit"] is None
  # Legacy startup message content is unchanged.
  assert result["startup"] == "configuration_authority=legacy"


# --- implicit legacy (missing env) ------------------------------------------

def test_missing_authority_selects_legacy_with_implicit_warning():
  result = _diagnostics()
  assert result["authority"] == "legacy"
  assert result["explicit"] is False
  # Implicit selection warns, but is not the explicit-legacy deprecation.
  assert result["deprecation"] is None
  assert result["implicit"] == (
    "configuration_authority_implicit=true "
    "selected_authority=legacy recommended_authority=canonical"
  )


# --- canonical fail-closed --------------------------------------------------

def test_canonical_failure_does_not_construct_legacy():
  process = _run("canonical", drop=("POSTGRES_PASSWORD",))
  assert process.returncode != 0
  assert "LegacyCanonicalConfigView" not in process.stdout
  assert "PythonRuntimeConfig" not in process.stdout


# --- restart rollback -------------------------------------------------------

def test_restart_rollback_canonical_to_legacy():
  canonical = _diagnostics("canonical")
  legacy = _diagnostics("legacy")
  assert canonical["authority"] == "canonical"
  assert legacy["authority"] == "legacy"
  assert legacy["runtime_type"] == "LegacyCanonicalConfigView"
  # A second legacy start reproduces the rollback deterministically.
  again = _diagnostics("legacy")
  assert again["authority"] == "legacy"


# --- leaf value/type parity under reduced bot env ---------------------------

def test_legacy_canonical_leaf_parity_reduced_bot_env():
  legacy = _diagnostics("legacy", probe=_PARITY_PROBE, reduced=True)
  canonical = _diagnostics("canonical", probe=_PARITY_PROBE, reduced=True)
  assert legacy["count"] == canonical["count"]
  assert legacy["count"] > 0
  assert legacy["parity"] == canonical["parity"]
