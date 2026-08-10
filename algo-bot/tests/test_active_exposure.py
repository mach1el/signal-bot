"""Active exposure gates: opposing distance + V7 PascalCase Redis payloads."""

from __future__ import annotations

import json

import pytest

from app.autotrade.active_exposure import (
  ActiveExposure,
  evaluate_entry_against_exposure,
  load_active_exposures,
)

pytestmark = pytest.mark.no_database


def test_opposing_blocks_when_absolute_distance_too_near():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4051.93,
    exposures=[
      ActiveExposure(
        direction="SELL",
        entry_price=4054.20,
        source="v7_plan",
        plan_id="sell-near",
      )
    ],
    min_price_separation=15.0,
  )
  assert decision.block is True
  assert decision.reason_code == "opposing_active_too_close"
  assert decision.measured is not None
  assert decision.measured["price_distance"] == pytest.approx(2.27)


def test_scalp_ignores_opposing_active_when_fitted_room():
  """Owner: active opposite position does not block scalp with min room."""
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4051.93,
    exposures=[
      ActiveExposure(
        direction="SELL",
        entry_price=4054.20,
        source="v7_plan",
        plan_id="sell-active",
      )
    ],
    min_price_separation=15.0,
    ignore_opposing_active=True,
  )
  assert decision.block is False
  assert decision.reason_code == "opposing_active_too_close_ignored_scalp"
  assert decision.measured is not None
  assert decision.measured["ignore_opposing_active"] is True
  assert decision.measured["preference_telemetry"] is True


def test_opposing_allows_when_far_enough():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4051.93,
    exposures=[
      ActiveExposure(
        direction="SELL",
        entry_price=4096.0,
        source="v7_plan",
        plan_id="sell-far",
      )
    ],
    min_price_separation=15.0,
  )
  assert decision.block is False
  assert decision.same_direction_stack is False


def test_same_direction_blocks_non_scalp_before_tp2_booked():
  """Owner: same-dir add waits until first position books TP2."""
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v7_plan",
        plan_id="buy-open",
        highest_booked_target_index=0,  # TP1 only
      )
    ],
  )
  assert decision.block is True
  assert decision.same_direction_stack is False
  assert decision.reason_code == "same_direction_active_before_tp2"


def test_same_direction_blocks_when_booked_index_unknown():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v7_plan",
        plan_id="buy-open",
        highest_booked_target_index=None,
      )
    ],
  )
  assert decision.block is True
  assert decision.reason_code == "same_direction_active_before_tp2"


def test_same_direction_stacks_at_60_after_tp2_booked():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v7_plan",
        plan_id="buy-open",
        highest_booked_target_index=1,  # TP2 booked
      )
    ],
  )
  assert decision.block is False
  assert decision.same_direction_stack is True
  assert decision.reason_code == "same_direction_stack"
  assert decision.measured is not None
  assert decision.measured["same_direction_tp2_booked"] is True
  assert "unlocked after booked TP2" in decision.message


def test_same_direction_stack_flag_when_allowed_for_scalp():
  decision = evaluate_entry_against_exposure(
    direction="BUY",
    entry_price=4050.0,
    exposures=[
      ActiveExposure(
        direction="BUY",
        entry_price=4048.0,
        source="v7_plan",
        plan_id="buy-open",
        highest_booked_target_index=None,
      )
    ],
    allow_same_direction_stack=True,
  )
  assert decision.block is False
  assert decision.same_direction_stack is True
  assert decision.reason_code == "same_direction_stack"


@pytest.mark.asyncio
async def test_load_v7_exposures_reads_pascal_case_runtime_json():
  class FakeRedis:
    async def get(self, key: str):
      if key == "execution:trade_plan_runtime_ids":
        return b"plan-sell-1"
      if key == "execution:plan_runtime:plan-sell-1":
        return json.dumps({
          "PlanId": "plan-sell-1",
          "SetupId": "setup-1",
          "Direction": "SELL",
          "Stage": "FullyOpen",
          "GroupStage": "fully_open",
          "GroupWeightedFillPrice": 4054.2,
          "TotalFilledVolume": 200,
          "RemainingVolume": 200,
          "HighestBookedTargetIndex": 1,
        }).encode()
      return None

    async def smembers(self, key: str):
      return set()

  exposures = await load_active_exposures(FakeRedis())
  assert len(exposures) == 1
  assert exposures[0].direction == "SELL"
  assert exposures[0].entry_price == pytest.approx(4054.2)
  assert exposures[0].source == "v7_plan"
  assert exposures[0].plan_id == "plan-sell-1"
  assert exposures[0].highest_booked_target_index == 1
