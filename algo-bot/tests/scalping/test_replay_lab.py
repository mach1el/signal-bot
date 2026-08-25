"""Replay laboratory + range_sweep math annotate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.scalping.models import (
  ARCHETYPE_RANGE_SWEEP,
  OPPORTUNITY_VERSION,
  ScalpOpportunity,
  deterministic_id,
)
from app.scalping.replay import split_dataset
from app.scalping.replay_lab import (
  LabEvent,
  parameter_sweep,
  replay_lab_event,
  replay_lab_fixture,
)
from app.scalping.rollout import annotate_range_sweep_math_gate


pytestmark = pytest.mark.no_database

FIXTURE = Path(__file__).parent / "fixtures" / "lab_events.jsonl"


def test_replay_lab_fixture_blocks_and_trades():
  report = replay_lab_fixture(FIXTURE)
  assert report["blocked_count"] >= 1
  assert report["allowed_count"] >= 1
  cal = report["calibration_traded"]
  assert "development" in cal
  assert "holdout" in cal
  assert cal["discipline"]["rule"] == "never_tune_thresholds_on_holdout"


def test_parameter_sweep_uses_replace_not_holdout():
  events = [
    LabEvent.from_dict({
      "timestamp": 1,
      "direction": "BUY",
      "price": 4050.5,
      "atr": 5.0,
      "range_low": 4048.0,
      "range_high": 4060.0,
      "strategy": "liquidity_sweep_reversal",
      "liquidity_level": 4050.0,
      "barrier": 4058.0,
      "bar": {"open": 4050.2, "high": 4051.0, "low": 4049.5, "close": 4050.6},
      "target_min_price": 1.0,
      "stop_price": 4048.0,
      "target_price": 4054.0,
      "bars_after": [
        {"open": 4050.6, "high": 4054.2, "low": 4050.4, "close": 4054.0},
      ],
    }),
  ]
  sweep = parameter_sweep(events, param="max_location_buy", values=[0.30, 0.40, 0.50])
  assert len(sweep) == 3
  assert all(row["param"] == "max_location_buy" for row in sweep)


def test_lab_event_impulse_path():
  event = LabEvent.from_dict({
    "timestamp": 10,
    "direction": "BUY",
    "price": 4057.0,
    "atr": 5.0,
    "range_low": 4048.0,
    "range_high": 4070.0,
    "strategy": "impulse_pullback_continuation",
    "impulse_origin": 4050.0,
    "impulse_extreme": 4060.0,
    "barrier": 4070.0,
    "bar": {"open": 4056.5, "high": 4057.5, "low": 4056.0, "close": 4057.0},
    "continuation_trigger": True,
    "target_min_price": 1.0,
    "stop_price": 4054.0,
    "target_price": 4062.0,
    "bars_after": [
      {"open": 4057.0, "high": 4062.5, "low": 4056.8, "close": 4062.0},
    ],
  })
  row = replay_lab_event(event)
  assert row["gate_allowed"] is True
  assert row["outcome"] == "target"


def test_annotate_range_sweep_math_gate_stamps_measured():
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id=deterministic_id("t", 1),
    context_id="ctx",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=1,
    source_bar_ts=1,
    zone_low=4048.0,
    zone_high=4051.0,
    key_level=4050.0,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=1,
    trigger_price=4050.5,
    invalidation_price=4048.0,
    expected_target_price=4054.0,
    expected_target_pips=35.0,
    expected_stop_pips=25.0,
    expected_reward_risk=1.4,
    location_position=0.2,
    score=1.0,
    reasons=("test",),
    expires_at=100,
  )
  stamped = annotate_range_sweep_math_gate(
    opp,
    atr=5.0,
    range_low=4048.0,
    range_high=4060.0,
    barrier=4058.0,
    bar_open=4050.2,
    bar_high=4051.0,
    bar_low=4049.5,
    bar_close=4050.6,
    spread=0.2,
    target_min_price=1.0,
  )
  gate = stamped.measured["math_liquidity_sweep"]
  assert gate["allowed"] is True
  assert gate["strategy"] == "liquidity_sweep_reversal"
  assert "math_score_inputs" in stamped.measured


def test_split_discipline_chronological():
  rows = [{"timestamp": i, "outcome": "target", "net_r": 0.1} for i in range(10)]
  splits = split_dataset(rows)
  assert len(splits["development"]) == 6
  assert len(splits["validation"]) == 2
  assert len(splits["holdout"]) == 2
  assert splits["holdout"][0]["timestamp"] == 8
