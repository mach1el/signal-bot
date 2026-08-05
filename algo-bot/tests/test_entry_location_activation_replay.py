"""Differential replay: legacy execute vs location + activation gates."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.analysis.entry_location import build_entry_location_context, evaluate_entry_location
from app.analysis.m1_trigger import M1TriggerResult
from app.autotrade.entry_activation import evaluate_entry_activation


pytestmark = pytest.mark.no_database

RANGE_LOW = 4000.0
RANGE_HIGH = 4100.0
NOW = 1_700_000_000


def _cfg():
  return SimpleNamespace(
    actionability=SimpleNamespace(
      entry_location=SimpleNamespace(
        mode="enforce",
        missing_context_policy="block",
        reversal=SimpleNamespace(
          buy_maximum_position=0.50,
          sell_minimum_position=0.50,
          extreme_buy_block_position=0.65,
          extreme_sell_block_position=0.35,
        ),
        range_reversion=SimpleNamespace(
          buy_maximum_position=0.40,
          sell_minimum_position=0.60,
          equilibrium_exclusion_width=0.20,
        ),
        trend_pullback=SimpleNamespace(
          buy_maximum_position=0.70,
          sell_minimum_position=0.30,
        ),
        breakout_retest=SimpleNamespace(allow_directional_expansion=True),
      ),
    ),
    execution=SimpleNamespace(
      activation=SimpleNamespace(
        mode="enforce",
        reaction_trigger_maximum_age_bars=2,
      ),
    ),
  )


@dataclass(frozen=True)
class Observation:
  name: str
  strategy: str
  direction: str
  price: float
  zone_entered_at: int
  trigger: M1TriggerResult | None = None
  breakout_evidence: dict | None = None
  quote_inside: bool = True
  decisive_break: bool = False
  now: int = NOW


@dataclass(frozen=True)
class ReplayOutcome:
  legacy_would_execute: bool
  new_location_would_block: bool
  new_activation_would_wait: bool
  new_would_execute: bool
  location_reason: str
  activation_reason: str


def classify(obs: Observation, *, cfg) -> ReplayOutcome:
  """Legacy path had no location/activation gates — always would execute."""
  legacy_would_execute = True

  ctx = build_entry_location_context(
    execution_price=obs.price,
    direction=obs.direction,
    m15_range_low=RANGE_LOW,
    m15_range_high=RANGE_HIGH,
  )
  location = evaluate_entry_location(
    strategy=obs.strategy,
    direction=obs.direction,
    context=ctx,
    cfg=cfg,
    breakout_evidence=obs.breakout_evidence,
  )
  activation = evaluate_entry_activation(
    strategy=obs.strategy,
    direction=obs.direction,
    zone_entered_at=obs.zone_entered_at,
    quote_inside=obs.quote_inside,
    decisive_break=obs.decisive_break,
    trigger=obs.trigger,
    location_decision=location,
    now=obs.now,
    cfg=cfg,
    breakout_evidence=obs.breakout_evidence,
  )

  new_location_would_block = not location.allowed or location.would_block
  new_activation_would_wait = not activation.allowed or activation.would_block
  new_would_execute = location.allowed and activation.allowed

  return ReplayOutcome(
    legacy_would_execute=legacy_would_execute,
    new_location_would_block=new_location_would_block,
    new_activation_would_wait=new_activation_would_wait,
    new_would_execute=new_would_execute,
    location_reason=location.reason_code,
    activation_reason=activation.reason_code,
  )


def _trigger(direction: str, *, bar_ts: int) -> M1TriggerResult:
  return M1TriggerResult("wick_rejection", direction, 4080.0, bar_ts, "replay")


OBSERVATIONS = [
  Observation(
    name="buy_above_extreme",
    strategy="Zone Reaction",
    direction="BUY",
    price=4070.0,
    zone_entered_at=NOW - 120,
    trigger=_trigger("BUY", bar_ts=NOW - 60),
  ),
  Observation(
    name="sell_below_extreme",
    strategy="Zone Reaction",
    direction="SELL",
    price=4020.0,
    zone_entered_at=NOW - 120,
    trigger=_trigger("SELL", bar_ts=NOW - 60),
  ),
  Observation(
    name="reaction_without_m1",
    strategy="Zone Reaction",
    direction="BUY",
    price=4030.0,
    zone_entered_at=NOW - 120,
    trigger=None,
  ),
  Observation(
    name="stale_trigger",
    strategy="Zone Reaction",
    direction="BUY",
    price=4030.0,
    zone_entered_at=NOW - 600,
    trigger=_trigger("BUY", bar_ts=NOW - 300),
  ),
  Observation(
    name="valid_breakout_retest",
    strategy="Break & Retest",
    direction="BUY",
    price=4060.0,
    zone_entered_at=NOW - 120,
    trigger=_trigger("BUY", bar_ts=NOW - 60),
    breakout_evidence={
      "accepted_break": True,
      "correct_key_level_role": True,
      "retest_of_broken_level": True,
      "directionally_valid_close": True,
      "target_room_beyond_breakout": True,
    },
  ),
]


EXPECTED = {
  "buy_above_extreme": {
    "legacy_would_execute": True,
    "new_location_would_block": True,
    "new_activation_would_wait": True,
    "new_would_execute": False,
    "location_reason": "buy_at_range_extreme",
    "activation_reason": "buy_at_range_extreme",
  },
  "sell_below_extreme": {
    "legacy_would_execute": True,
    "new_location_would_block": True,
    "new_activation_would_wait": True,
    "new_would_execute": False,
    "location_reason": "sell_at_range_extreme",
    "activation_reason": "sell_at_range_extreme",
  },
  "reaction_without_m1": {
    "legacy_would_execute": True,
    "new_location_would_block": False,
    "new_activation_would_wait": True,
    "new_would_execute": False,
    "location_reason": "entry_location_allowed",
    "activation_reason": "reaction_trigger_missing",
  },
  "stale_trigger": {
    "legacy_would_execute": True,
    "new_location_would_block": False,
    "new_activation_would_wait": True,
    "new_would_execute": False,
    "location_reason": "entry_location_allowed",
    "activation_reason": "reaction_trigger_stale",
  },
  "valid_breakout_retest": {
    "legacy_would_execute": True,
    "new_location_would_block": False,
    "new_activation_would_wait": False,
    "new_would_execute": True,
    "location_reason": "location_override_accepted_breakout_retest",
    "activation_reason": "entry_activation_allowed",
  },
}


@pytest.mark.parametrize("obs", OBSERVATIONS, ids=[o.name for o in OBSERVATIONS])
def test_replay_differential_classification(obs: Observation):
  cfg = _cfg()
  outcome = classify(obs, cfg=cfg)
  expected = EXPECTED[obs.name]

  assert outcome.legacy_would_execute is expected["legacy_would_execute"]
  assert outcome.new_location_would_block is expected["new_location_would_block"]
  assert outcome.new_activation_would_wait is expected["new_activation_would_wait"]
  assert outcome.new_would_execute is expected["new_would_execute"]
  assert outcome.location_reason == expected["location_reason"]
  assert outcome.activation_reason == expected["activation_reason"]


def test_replay_report_snapshot():
  """Utility: print differential table for manual inspection (not profitability)."""
  cfg = _cfg()
  rows = []
  for obs in OBSERVATIONS:
    outcome = classify(obs, cfg=cfg)
    rows.append({
      "scenario": obs.name,
      "legacy": outcome.legacy_would_execute,
      "loc_block": outcome.new_location_would_block,
      "act_wait": outcome.new_activation_would_wait,
      "new_exec": outcome.new_would_execute,
      "loc_reason": outcome.location_reason,
      "act_reason": outcome.activation_reason,
    })
  assert len(rows) == len(OBSERVATIONS)
