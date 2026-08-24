"""Structure-gated autonomous scalp entries into the confirmed zone."""

from __future__ import annotations

import pytest

from app.autotrade.execution_route import (
  ROUTE_MARKET,
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


@pytest.mark.parametrize(
  ("direction", "expected_prices"),
  [
    ("BUY", (4004.0, 4000.0)),
    ("SELL", (4001.0, 4005.0)),
  ],
)
def test_xau_auto_route_uses_configured_shallow_80_deep_20(
  direction: str,
  expected_prices: tuple[float, float],
):
  from tests.test_config_effective_instrument_context import (
    _load_production_example,
  )

  cfg = _load_production_example().config
  xau = cfg.for_instrument("XAU")
  plan = resolve_execution_route_plan(
    direction=direction,
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4004.0 if direction == "BUY" else 4001.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="HFS Range Sweep",
    strategy_family="hfs",
    entry_clips=xau.targeting.entry_clips,
  )

  assert plan.valid is True
  assert plan.route == ROUTE_MARKET_WITH_LIMIT_SCALE
  assert plan.planned_leg_entry_prices == expected_prices
  assert plan.planned_leg_volume_ratios == (0.8, 0.2)
  assert plan.routing_reason == "two-clip grid: shallow/deep volume split"


def test_hfs_chase_sell_books_full_market_not_five_legs_into_abandoned_zone():
  """Live 2026-08-21: quote below supply, five equal clips → only L1 rode TP."""
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4563.98,
    zone_low=4566.9775,
    zone_high=4567.86625,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="HFS Range Sweep",
    strategy_family="hfs",
  )
  assert plan.valid is True
  assert plan.route == ROUTE_MARKET
  assert plan.entry_geometry == "below"
  assert plan.planned_leg_entry_prices == ()
  assert plan.planned_leg_volume_ratios == ()
  assert plan.planned_entry_price == 4563.98
  assert plan.immediate_market is True


def test_hfs_chase_buy_books_full_market_not_micro_grid():
  plan = resolve_execution_route_plan(
    direction="BUY",
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=4010.0,
    zone_low=4000.0,
    zone_high=4005.0,
    atr=4.0,
    zone_fill_enabled=True,
    strategy="HFS Impulse Pullback",
    strategy_family="hfs",
  )
  assert plan.route == ROUTE_MARKET
  assert plan.entry_geometry == "above"
  assert plan.planned_leg_entry_prices == ()
  assert plan.immediate_market is True


def test_technique_fvg_uses_scalp_micro_grid():
  plan = resolve_execution_route_plan(
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
  assert plan.valid is True
  assert plan.route == ROUTE_MARKET_WITH_LIMIT_SCALE
  assert len(plan.planned_leg_entry_prices) == SCALP_MICRO_CLIPS
  assert all(
    pytest.approx(ratio, abs=1e-4) == 0.2
    for ratio in plan.planned_leg_volume_ratios
  )


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
