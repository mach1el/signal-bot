"""Process-isolated dual-authority parity for Phase 2G production access points."""

from __future__ import annotations

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
  "TELEGRAM_BOT_TOKEN": "phase-2g-main-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2g-postgres",
  "AUTO_TRADE_ENABLED": "true",
  "AUTO_TRADE_DRY_RUN": "true",
  "AUTO_TRADE_PROFILE": "demo_eval",
  "AUTO_TRADE_OPPOSING_ACTIVE_MIN_PRICE": "15",
  "AUTO_TRADE_SAME_DIRECTION_STACK_SIZE_FRACTION": "0.60",
  "AUTO_TRADE_XAU_PIP_SIZE": "0.1",
  "AUTO_TRADE_XAU_PRICE_DIGITS": "2",
  "AUTO_TRADE_CANDIDATE_CONTRACT_VERSION": "6",
}

_PROBE = r"""
import json
from pathlib import Path
from app.core.config import runtime_config

manifest = json.loads(
  (Path.cwd().parent / "contracts/configuration/consumer-migration-phase-2g.generated.json")
  .read_text(encoding="utf-8")
)

def resolve(path):
  value = runtime_config
  for part in path.split("."):
    value = getattr(value, part)
  return value

def exact(value):
  return {
    "type": f"{type(value).__module__}.{type(value).__qualname__}",
    "repr": repr(value),
    "truthy": bool(value),
  }

accesses = []
for row in manifest["reads"]:
  if row["migration_status"] != "migrated":
    continue
  if row["migration_classification"] != "PHASE_2G_MIGRATE":
    continue
  path = row["canonical_path"]
  accesses.append({
    "path": path,
    "legacy_attribute": row["legacy_attribute"],
    "root": row["root_domain"],
    "exact": exact(resolve(path)),
  })

print(json.dumps({
  "runtime_type": type(runtime_config).__name__,
  "access_count": len(accesses),
  "accesses": accesses,
}, sort_keys=True))
"""


def _probe(authority: str) -> dict:
  env = os.environ.copy()
  env.update(_SAFE)
  env["APEXVOID_CONFIG_AUTHORITY"] = authority
  completed = subprocess.run(
    [sys.executable, "-c", _PROBE],
    cwd=_ALGO_ROOT,
    env=env,
    check=True,
    capture_output=True,
    text=True,
  )
  return json.loads(completed.stdout)


@pytest.mark.parametrize("root", sorted({
  "runtime", "contract", "execution", "risk", "manual_algo",
}))
def test_phase2g_root_parity_under_both_authorities(root: str):
  legacy = _probe("legacy")
  canonical = _probe("canonical")
  assert legacy["runtime_type"] == "LegacyCanonicalConfigView"
  assert canonical["runtime_type"] == "PythonRuntimeConfig"
  legacy_rows = [row for row in legacy["accesses"] if row["root"] == root]
  canonical_rows = [row for row in canonical["accesses"] if row["root"] == root]
  assert legacy_rows
  assert len(legacy_rows) == len(canonical_rows)
  for left, right in zip(legacy_rows, canonical_rows, strict=True):
    assert left["path"] == right["path"]
    assert left["exact"] == right["exact"], (root, left["path"], left, right)


def test_runtime_config_parity_under_both_authorities():
  test_phase2g_root_parity_under_both_authorities("runtime")


def test_contract_config_parity_under_both_authorities():
  test_phase2g_root_parity_under_both_authorities("contract")


def test_execution_config_parity_under_both_authorities():
  test_phase2g_root_parity_under_both_authorities("execution")


def test_risk_config_parity_under_both_authorities():
  test_phase2g_root_parity_under_both_authorities("risk")


def test_manual_algo_config_parity_under_both_authorities():
  test_phase2g_root_parity_under_both_authorities("manual_algo")
