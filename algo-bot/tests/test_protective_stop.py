"""Tests for final protective stop planning and opposing-zone push."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.protective_stop import (
  FinalProtectiveStopPlan,
  OpposingZoneStopContext,
  ProtectiveStopError,
  STOP_PLAN_VERSION,
  plan_protective_stop,
)


pytestmark = pytest.mark.no_database


@pytest.mark.parametrize(
  (
    "direction", "entry", "swing", "sweep", "minimum", "maximum",
    "expected_stop", "expected_pips", "expected_raw", "clamped", "source",
  ),
  [
    ("BUY", "4100", "4097", None, 20, 60, "4096.70", "33.0", "4096.7", False, "structure"),
    ("SELL", "4100", "4103", None, 20, 60, "4103.30", "33.0", "4103.3", False, "structure"),
    ("BUY", "4100", "4097", "4096", 20, 60, "4095.85", "41.5", "4095.85", False, "wick"),
    ("SELL", "4100", "4103", "4104", 20, 60, "4104.15", "41.5", "4104.15", False, "wick"),
    ("BUY", "4100", "4099.9", None, 20, 60, "4098.00", "20.0", "4099.6", True, "structure"),
    ("BUY", "4100", "4090", None, 20, 60, "4094.00", "60.0", "4089.7", True, "structure"),
    ("BUY", "4100", "4100.29", None, 20, 60, "4098.00", "20.0", "4099.99", True, "structure"),
    ("BUY", "4100.005", "4097.302", None, 20, 60, "4097.30", "27.05", "4097.302", True, "structure"),
  ],
)
def test_protective_stop_table_matches_executor_sequence(
  direction,
  entry,
  swing,
  sweep,
  minimum,
  maximum,
  expected_stop,
  expected_pips,
  expected_raw,
  clamped,
  source,
):
  plan = plan_protective_stop(
    direction=direction,
    entry_price=entry,
    structure_swing=swing,
    atr="1",
    structure_buffer_atr="0.3" if swing != "4097.302" else "0",
    sweep_extreme=sweep,
    wick_buffer_atr="0.15",
    minimum_stop_pips=minimum,
    maximum_stop_pips=maximum,
    pip_size="0.1",
    digits=2,
  )

  assert plan.final_stop_price == Decimal(expected_stop)
  assert plan.final_stop_pips == Decimal(expected_pips)
  assert plan.raw_stop_price == Decimal(expected_raw)
  assert plan.clamped is clamped
  assert plan.source == source
  assert plan.adjustment == "none"
  assert plan.base_stop_price == plan.final_stop_price


def test_wick_beyond_envelope_rejects():
  with pytest.raises(
    ProtectiveStopError, match="stop_exceeds_envelope_after_wick",
  ):
    plan_protective_stop(
      direction="BUY",
      entry_price="4100",
      structure_swing="4097",
      atr="1",
      structure_buffer_atr="0.3",
      sweep_extreme="4090",
      wick_buffer_atr="0.15",
      minimum_stop_pips=20,
      maximum_stop_pips=60,
      pip_size="0.1",
      digits=2,
    )


def test_stop_inside_opposing_zone_rejects_when_push_disabled():
  with pytest.raises(ProtectiveStopError, match="stop_inside_opposing_zone"):
    plan_protective_stop(
      direction="BUY",
      entry_price="4000",
      structure_swing="3998",
      atr="1",
      structure_buffer_atr="0.3",
      sweep_extreme=None,
      wick_buffer_atr="0.15",
      minimum_stop_pips=30,
      maximum_stop_pips=65,
      pip_size="0.1",
      digits=2,
      opposing_zone=OpposingZoneStopContext(
        zone_id="demand-1",
        low=Decimal("3997"),
        high=Decimal("3998.5"),
        execution_grade=True,
        push_beyond_zone=False,
        buffer_atr=Decimal("0.3"),
      ),
    )


def test_stop_inside_execution_grade_zone_is_pushed_beyond_it():
  plan = plan_protective_stop(
    direction="BUY",
    entry_price="4000.2",
    structure_swing="3998",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=30,
    maximum_stop_pips=65,
    pip_size="0.1",
    digits=2,
    opposing_zone=OpposingZoneStopContext(
      zone_id="demand-1",
      low=Decimal("3997"),
      high=Decimal("3998.5"),
      execution_grade=True,
      push_beyond_zone=True,
      buffer_atr=Decimal("0.3"),
    ),
  )
  assert plan.base_stop_price == Decimal("3997.20")
  assert plan.final_stop_price == Decimal("3996.70")
  assert plan.adjustment == "opposing_zone_push"
  assert plan.adjustment_zone_low == Decimal("3997")
  assert plan.adjustment_zone_high == Decimal("3998.5")


def test_context_only_opposing_zone_leaves_base_stop():
  plan = plan_protective_stop(
    direction="BUY",
    entry_price="4000",
    structure_swing="3998",
    atr="1",
    structure_buffer_atr="0.3",
    sweep_extreme=None,
    wick_buffer_atr="0.15",
    minimum_stop_pips=30,
    maximum_stop_pips=65,
    pip_size="0.1",
    digits=2,
    opposing_zone=OpposingZoneStopContext(
      zone_id="demand-wide",
      low=Decimal("3990"),
      high=Decimal("4025"),
      execution_grade=False,
      push_beyond_zone=True,
      buffer_atr=Decimal("0.3"),
    ),
  )
  assert plan.final_stop_price == plan.base_stop_price
  assert plan.adjustment == "none"


def test_candidate_fields_emit_v2_contract():
  plan = FinalProtectiveStopPlan(
    entry_price=Decimal("4000.2"),
    base_stop_price=Decimal("3997.70"),
    base_stop_pips=Decimal("25"),
    final_stop_price=Decimal("3996.70"),
    final_stop_distance=Decimal("3.5"),
    final_stop_pips=Decimal("35"),
    raw_stop_price=Decimal("3997.7"),
    clamped=False,
    source="structure",
    adjustment="opposing_zone_push",
    adjustment_zone_id="demand-1",
    adjustment_zone_low=Decimal("3997"),
    adjustment_zone_high=Decimal("3998.5"),
    version=STOP_PLAN_VERSION,
  )
  fields = plan.candidate_fields(entry_price=Decimal("4000.2"))
  assert fields["stop_plan_version"] == 2
  assert fields["planned_stop_price"] == "3996.70"
  assert fields["planned_final_stop_price"] == "3996.70"
  assert fields["planned_base_stop_price"] == "3997.70"
  assert fields["stop_adjustment"] == "opposing_zone_push"


def _policy_subject(**overrides):
  values = {
    "strategy": "Mapped Zone Reaction",
    "direction": "BUY",
    "entry_low": 4100.0,
    "entry_high": 4100.2,
    "current_price": 4100.1,
    "confluence": 3,
    "atr": 1.0,
    "structure_swing": 4099.9,
    "targets_pips": (30,),
    "target_model": "fill_relative",
    "risk_multiplier": 1.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def test_reward_risk_uses_clamped_stop_and_rejects_old_apparent_30r():
  result = evaluate_execution_policy(
    _policy_subject(),
    spot_price=4100.0,
    regime="trend",
    pip_size=0.1,
  )

  assert not result.allowed
  assert result.reason_code == "policy_reward_risk_insufficient"
  assert result.measured["planned_stop_pips"] == "40.0"
  assert result.measured["planned_final_stop_pips"] == "40.0"
  assert result.measured["reward_risk"] == 0.75


def test_reward_risk_accepts_near_entry_structure_after_minimum_clamp():
  result = evaluate_execution_policy(
    _policy_subject(
      structure_swing=4100.29,
      targets_pips=(60,),
    ),
    spot_price=4100.0,
    regime="trend",
    pip_size=0.1,
  )

  assert result.allowed
  assert result.measured["planned_stop_pips"] == "40.0"
  assert result.measured["reward_risk"] == 1.5


def test_reaction_family_stop_floor_is_tighter_than_trend_families():
  # Owner's trading style: a narrow (~3 point) entry zone with a stop just
  # beyond it - a genuinely tight, structure-computed stop. The 4 zone-scale
  # reaction families (Key Level/Demand/Supply/Session Level/Trendline
  # Reaction) previously fell through to the trend-family floor
  # (auto_trade_trend_stop_min_pips=40) regardless of how tight their own
  # structure actually was, dragging a perfectly good reward:risk down to
  # "insufficient" for no structural reason. They now get their own,
  # tighter floor (auto_trade_reaction_stop_min_pips=20) instead.
  reaction = evaluate_execution_policy(
    _policy_subject(strategy="Key Level Reaction", targets_pips=(30,)),
    spot_price=4100.0,
    regime="range",
    pip_size=0.1,
  )
  trend = evaluate_execution_policy(
    _policy_subject(strategy="Mapped Zone Reaction", targets_pips=(30,)),
    spot_price=4100.0,
    regime="trend",
    pip_size=0.1,
  )

  assert reaction.measured["planned_stop_pips"] == "20.0"
  assert reaction.measured["reward_risk"] == 1.5
  assert reaction.allowed

  assert trend.measured["planned_stop_pips"] == "40.0"
  assert trend.reason_code == "policy_reward_risk_insufficient"
