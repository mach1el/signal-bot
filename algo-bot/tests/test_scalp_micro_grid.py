"""Structure-gated scalp micro-grid: five equal clips into the zone."""

from __future__ import annotations

import pytest

from app.autotrade.execution_route import (
  ROUTE_MARKET_WITH_LIMIT_SCALE,
  SCALP_MICRO_CLIPS,
  resolve_execution_route_plan,
  scalp_micro_grid_legs,
)


pytestmark = pytest.mark.no_database


def test_buy_grid_steps_down_from_quote_to_distal():
  legs = scalp_micro_grid_legs(
    side="BUY", low=4000.0, high=4005.0, quote=4004.0, digits=2,
  )
  assert len(legs) == SCALP_MICRO_CLIPS
  assert legs[0] == 4004.0
  assert legs[-1] == 4000.0
  assert legs == tuple(sorted(legs, reverse=True))


def test_sell_grid_steps_up_from_quote_to_distal():
  legs = scalp_micro_grid_legs(
    side="SELL", low=4000.0, high=4005.0, quote=4001.0, digits=2,
  )
  assert len(legs) == SCALP_MICRO_CLIPS
  assert legs[0] == 4001.0
  assert legs[-1] == 4005.0
  assert legs == tuple(sorted(legs))


def test_hfs_route_is_five_equal_clips_not_one_market():
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4004.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="HFS Range Sweep",
    strategy_family="hfs",
  )
  assert plan.valid is True
  assert plan.route == ROUTE_MARKET_WITH_LIMIT_SCALE
  assert len(plan.planned_leg_entry_prices) == SCALP_MICRO_CLIPS
  assert pytest.approx(sum(plan.planned_leg_volume_ratios), abs=1e-6) == 1.0
  assert all(
    pytest.approx(ratio, abs=1e-4) == 0.2
    for ratio in plan.planned_leg_volume_ratios
  )


def test_technique_fvg_does_not_use_scalp_micro_grid():
  market = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4004.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="FVG",
    strategy_family="zone",
  )
  assert market.valid is True
  assert market.route == "market"
  assert market.planned_leg_entry_prices == ()

  scaled = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=4004.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    zone_fill_min_atr=0.5,
    strategy="FVG",
    strategy_family="supply_demand",
  )
  assert scaled.valid is True
  assert "micro-grid" not in scaled.routing_reason
  assert len(scaled.planned_leg_entry_prices) == 2


def test_key_level_reaction_is_not_forced_onto_scalp_grid():
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4004.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="Key Level Reaction",
    strategy_family="key_level",
  )
  assert plan.route == "market"
  assert plan.planned_leg_entry_prices == ()
