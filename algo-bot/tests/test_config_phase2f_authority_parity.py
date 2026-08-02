"""Process-isolated parity for all Phase 2F production access points."""

from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.no_database

_ALGO_ROOT = Path(__file__).parents[1]
_ROOT_COUNTS = {
  "analysis": 15,
  "strategies": 37,
  "actionability": 47,
  "lifecycle": 41,
}
_SAFE = {
  "PYTHONPATH": str(_ALGO_ROOT),
  "TELEGRAM_BOT_TOKEN": "phase-2f-main-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2f-postgres",
  "AUTO_TRADE_STRUCTURAL_GUARD_MODE": "observe",
  "AUTO_TRADE_CANDIDATE_TTL": "731",
  "AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS": "419",
  "AUTO_TRADE_MAP_REACTION_REARM_ATR": "0.61",
  "AUTO_TRADE_MAP_REACTION_REARM_BARS": "4",
  "XAU_ZONE_MIN_WIDTH_PRICE": "3.11",
  "XAU_ZONE_PREFERRED_MIN_WIDTH_PRICE": "3.21",
  "XAU_ZONE_PREFERRED_MAX_WIDTH_PRICE": "6.41",
  "XAU_MAJOR_ZONE_MAX_WIDTH_PRICE": "10.71",
}

_PROBE = r"""
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from app.analysis.confluence_zone import validate_zone_width
from app.autotrade import worker
from app.autotrade.gate import AutoScalpBox, AutoScalpRail
from app.autotrade.range_context import is_range_context_current
from app.core.config import runtime_config

manifest = json.loads(
  (Path.cwd().parent / "contracts/configuration/consumer-migration-phase-2f.generated.json")
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

accesses = {root: [] for root in ("analysis", "strategies", "actionability", "lifecycle")}
for row in manifest["reads"]:
  if row["migration_status"] != "migrated":
    continue
  path = row["canonical_path"]
  accesses[row["root_domain"]].append({
    "path": path,
    "legacy_attribute": row["legacy_attribute"],
    "exact": exact(resolve(path)),
  })

analysis = asdict(validate_zone_width(
  raw_width=0.29,
  merged_width=0.29,
  merge_sources=("M5", "M15"),
))

strategy_cases = (
  ("Range Edge Scalp", "range", True),
  ("Mapped Zone Reaction", "mapped_zone", False),
  ("Key Level Reaction", "key_level", False),
  ("Demand Zone Reaction", "supply_demand", False),
  ("Session Level Reaction", "session_level", False),
  ("Trendline Reaction", "trendline", False),
  ("Liquidity Reversal", "liquidity_reversal", False),
  ("Breakout Retest", "breakout_retest", False),
)
strategy = {
  name: worker._strategy_mode_enabled(SimpleNamespace(
    strategy=name, family=family, is_range_edge=is_range_edge,
  ))
  for name, family, is_range_edge in strategy_cases
}

lower = AutoScalpRail("lower", 100.0, 100.2, 100.1, 4, 3.0, ("M1",), ("level",))
upper = AutoScalpRail("upper", 100.8, 101.0, 100.9, 4, 3.0, ("M1",), ("level",))
box = AutoScalpBox("phase2f", lower, upper, 80.0)
actionability = {
  "eq_reason": worker._eq_exclusion_reason(
    box,
    100.5,
    runtime_config.actionability.gates.eq_exclusion_fraction,
  ),
  "edge_reason": worker._edge_proximity_reason(
    lower,
    100.7,
    1.0,
    runtime_config.actionability.gates.edge_proximity_atr,
  ),
}

maximum_age = runtime_config.lifecycle.strategy_match.maximum_age_seconds
context = SimpleNamespace(
  valid=True,
  generated_at=1000,
  expires_at=1000 + maximum_age,
)
lifecycle = {
  "storage_ttl": max(
    86400, runtime_config.lifecycle.candidate.storage_ttl_seconds,
  ),
  "before": is_range_context_current(
    context, now=1000 + maximum_age - 1, max_age_seconds=maximum_age,
  ),
  "at": is_range_context_current(
    context, now=1000 + maximum_age, max_age_seconds=maximum_age,
  ),
  "after": is_range_context_current(
    context, now=1000 + maximum_age + 1, max_age_seconds=maximum_age,
  ),
}

print(json.dumps({
  "runtime_type": type(runtime_config).__name__,
  "accesses": accesses,
  "snapshots": {
    "analysis": analysis,
    "strategies": strategy,
    "actionability": actionability,
    "lifecycle": lifecycle,
  },
}, sort_keys=True))
"""


@lru_cache(maxsize=2)
def _probe(authority: str) -> dict:
  environment = {
    key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}
  }
  environment.update(_SAFE)
  environment["APEXVOID_CONFIG_AUTHORITY"] = authority
  process = subprocess.run(
    [sys.executable, "-c", _PROBE],
    cwd=_ALGO_ROOT,
    env=environment,
    capture_output=True,
    text=True,
  )
  assert process.returncode == 0, process.stderr
  return json.loads(process.stdout)


def _assert_root_parity(root: str) -> None:
  legacy = _probe("legacy")
  canonical = _probe("canonical")
  assert len(legacy["accesses"][root]) == _ROOT_COUNTS[root]
  assert legacy["accesses"][root] == canonical["accesses"][root]
  assert legacy["snapshots"][root] == canonical["snapshots"][root]


def test_analysis_config_parity_under_both_authorities():
  _assert_root_parity("analysis")


def test_strategy_config_parity_under_both_authorities():
  _assert_root_parity("strategies")


def test_actionability_config_parity_under_both_authorities():
  _assert_root_parity("actionability")


def test_lifecycle_config_parity_under_both_authorities():
  _assert_root_parity("lifecycle")


def test_phase2f_parity_covers_every_production_access_point():
  assert sum(len(rows) for rows in _probe("legacy")["accesses"].values()) == 140


def test_phase2f_authority_types_are_distinct_and_expected():
  assert _probe("legacy")["runtime_type"] == "LegacyCanonicalConfigView"
  assert _probe("canonical")["runtime_type"] == "PythonRuntimeConfig"
