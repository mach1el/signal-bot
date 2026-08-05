"""Unit tests for archetype-aware entry activation gating."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis.entry_location import EntryLocationDecision
from app.analysis.m1_trigger import M1TriggerResult
from app.autotrade.entry_activation import (
  evaluate_entry_activation,
  apply_trigger_to_match,
  activation_archetype,
)


pytestmark = pytest.mark.no_database

NOW = 1_700_000_000
ZONE_ENTERED = NOW - 120


def _activation_cfg(mode: str = "enforce", max_age: int = 2):
  return SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(
        mode=mode,
        reaction_trigger_maximum_age_bars=max_age,
      ),
    ),
  )


def _location_allowed() -> EntryLocationDecision:
  return EntryLocationDecision(
    allowed=True,
    reason_code="entry_location_allowed",
    hard_block=False,
    archetype="reversal",
    would_block=False,
    measured={},
  )


def _location_blocked(reason: str = "buy_at_range_extreme") -> EntryLocationDecision:
  return EntryLocationDecision(
    allowed=False,
    reason_code=reason,
    hard_block=True,
    archetype="reversal",
    would_block=True,
    measured={},
  )


def _trigger(
  *,
  pattern: str = "wick_rejection",
  direction: str = "BUY",
  bar_ts: int | None = None,
) -> M1TriggerResult:
  return M1TriggerResult(
    pattern,
    direction,
    4080.0,
    bar_ts if bar_ts is not None else NOW - 60,
    "test trigger",
  )


def _activate(
  *,
  strategy: str = "Zone Reaction",
  direction: str = "BUY",
  trigger: M1TriggerResult | None = None,
  location: EntryLocationDecision | None = None,
  zone_entered_at: int | None = ZONE_ENTERED,
  quote_inside: bool = True,
  decisive_break: bool = False,
  now: int = NOW,
  mode: str = "enforce",
  breakout_evidence: dict | None = None,
  continuation_evidence: dict | None = None,
):
  return evaluate_entry_activation(
    strategy=strategy,
    direction=direction,
    zone_entered_at=zone_entered_at,
    quote_inside=quote_inside,
    decisive_break=decisive_break,
    trigger=trigger,
    location_decision=location or _location_allowed(),
    now=now,
    cfg=_activation_cfg(mode),
    breakout_evidence=breakout_evidence,
    continuation_evidence=continuation_evidence,
  )


def test_case_01_reaction_trigger_missing_enforce():
  decision = _activate(trigger=None)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"
  assert decision.hard_block is True
  assert decision.would_block is True


def test_case_02_reaction_trigger_stale():
  stale = _trigger(bar_ts=NOW - 300)
  decision = _activate(trigger=stale, zone_entered_at=NOW - 400)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_stale"


def test_case_03_reaction_trigger_before_zone_touch():
  early = _trigger(bar_ts=ZONE_ENTERED - 60)
  decision = _activate(trigger=early)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_before_zone_touch"


def test_case_04_wick_rejection_buy_allowed():
  trigger = _trigger(pattern="wick_rejection", direction="BUY")
  decision = _activate(strategy="Zone Reaction", direction="BUY", trigger=trigger)
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"
  assert decision.trigger_type == "wick_rejection"


def test_case_05_wick_rejection_sell_allowed():
  trigger = _trigger(pattern="wick_rejection", direction="SELL")
  decision = _activate(strategy="Zone Reaction", direction="SELL", trigger=trigger)
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"


def test_case_06_reaction_trigger_wrong_direction():
  trigger = _trigger(direction="SELL")
  decision = _activate(direction="BUY", trigger=trigger)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_wrong_direction"


def test_case_07_decisive_break_blocks_activation():
  decision = _activate(
    trigger=_trigger(),
    decisive_break=True,
  )
  assert decision.allowed is False
  assert decision.reason_code == "zone_decisively_broken"


def test_case_08_previous_episode_trigger_consumed():
  """Trigger from before zone touch is rejected (same as case 3)."""
  prior_episode = _trigger(bar_ts=ZONE_ENTERED - 180)
  decision = _activate(trigger=prior_episode)
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_before_zone_touch"


@pytest.mark.parametrize(
  "strategy",
  ["Key Level Reaction", "Range Edge Scalp"],
)
def test_case_09_grade_a_and_b_require_trigger_when_enforce(strategy):
  decision = _activate(strategy=strategy, trigger=None)
  assert activation_archetype(strategy) == "reaction_reversal"
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"


def test_case_10_breakout_retest_requires_evidence():
  decision = _activate(
    strategy="Break & Retest",
    direction="BUY",
    trigger=_trigger(),
    breakout_evidence=None,
  )
  assert decision.allowed is False
  assert decision.reason_code == "breakout_retest_evidence_missing"


def test_case_10b_breakout_retest_with_evidence_allowed():
  decision = _activate(
    strategy="Break & Retest",
    direction="BUY",
    trigger=_trigger(),
    breakout_evidence={
      "accepted_break": True,
      "retest_of_broken_level": True,
      "directionally_valid_close": True,
    },
  )
  assert decision.allowed is True
  assert decision.reason_code == "entry_activation_allowed"


def test_case_11_trend_pullback_allowed_without_reversal_trigger():
  decision = _activate(
    strategy="Trend Pullback",
    direction="BUY",
    trigger=None,
  )
  assert activation_archetype("Trend Pullback") == "trend_pullback"
  assert decision.allowed is True
  assert decision.requires_trigger is False


def test_case_12_momentum_allowed_without_reversal_trigger():
  decision = _activate(
    strategy="Momentum Ride",
    direction="BUY",
    trigger=None,
  )
  assert activation_archetype("Momentum Ride") == "momentum_continuation"
  assert decision.allowed is True


def test_case_12b_momentum_rejects_reversal_trigger_reuse():
  decision = _activate(
    strategy="Momentum Ride",
    direction="BUY",
    trigger=_trigger(pattern="wick_rejection"),
  )
  assert decision.allowed is False
  assert decision.reason_code == "momentum_cannot_reuse_reversal_trigger"


def test_case_13_location_block_with_valid_trigger():
  decision = _activate(
    trigger=_trigger(),
    location=_location_blocked("buy_at_range_extreme"),
  )
  assert decision.allowed is False
  assert decision.reason_code == "buy_at_range_extreme"


def test_case_14_trigger_block_with_valid_location():
  decision = _activate(trigger=None, location=_location_allowed())
  assert decision.allowed is False
  assert decision.reason_code == "reaction_trigger_missing"


def test_case_15_shadow_allows_with_would_block():
  decision = _activate(trigger=None, mode="shadow")
  assert decision.allowed is True
  assert decision.would_block is True
  assert decision.reason_code == "reaction_trigger_missing"


# Cases 16-18 (persist/publish on cutover) belong in integration tests:
# - test_zone_execution_cutover.py should assert matches are not persisted
#   when evaluate_entry_activation returns allowed=False.
# Unit-level contract: callers must not persist when blocked.
def test_case_16_blocked_activation_implies_no_persist_contract():
  decision = _activate(trigger=None, mode="enforce")
  assert decision.allowed is False
  assert decision.hard_block is True


def test_apply_trigger_to_match_stamps_fields():
  from dataclasses import dataclass

  @dataclass
  class _Match:
    touch_bar_ts: str | None = None
    confirmation_bar_ts: str | None = None
    reaction_type: str | None = None
    entry_activation_trigger: str | None = None
    entry_activation_trigger_ts: str | None = None

  trigger = _trigger(bar_ts=NOW - 30)
  updated = apply_trigger_to_match(_Match(), trigger)
  assert updated.entry_activation_trigger == "wick_rejection"
  assert updated.entry_activation_trigger_ts == str(NOW - 30)
  assert updated.confirmation_bar_ts == str(NOW - 30)
