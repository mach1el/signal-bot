"""DCA-into-zone scale ladder for reaction families (owner spec, 2026-07).

Leg 1 fills at the zone's proximal edge with the configured first-leg
fraction (default 70%); the remainder only fills at a further,
momentum-confirmed price scale_step_atr*ATR deeper into the zone - a real
resting limit order, so it only fills if price actually travels there (no
separate live momentum poll needed). A zone too narrow to qualify for the
ladder falls back to a single entry at the computed price.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.execution_route import (
  ROUTE_MARKET,
  ROUTE_ZONE_SPLIT,
  resolve_execution_route_plan,
)


pytestmark = pytest.mark.no_database


def _cfg(**overrides):
  values = {
    "auto_trade_zone_fill_enabled": True,
    "auto_trade_zone_fill_min_atr": 0.5,
    "auto_trade_inside_zone_market_entry_enabled": True,
    "auto_trade_zone_fill_fallback_enabled": True,
    "auto_trade_xau_price_digits": 2,
    "auto_trade_zone_scale_first_leg_fraction": 0.70,
    "auto_trade_zone_scale_step_atr": 0.5,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def _policy_match(**overrides):
  values = {
    "strategy": "Key Level Reaction",
    "direction": "SELL",
    "entry_low": 4035.0,
    "entry_high": 4036.5,
    "current_price": 4035.5,
    "confluence": 3,
    "atr": 1.0,
    "structure_swing": 4038.0,
    "targets_pips": (30, 60),
    "target_price": None,
    "risk_multiplier": 1.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


@pytest.mark.parametrize(
  "strategy",
  [
    "Key Level Reaction",
    "Demand Zone Reaction",
    "Supply Zone Reaction",
    "Session Level Reaction",
    "Trendline Reaction",
  ],
)
def test_all_reaction_families_use_the_zone_scale_ladder(strategy):
  evaluation = evaluate_execution_policy(
    _policy_match(strategy=strategy),
    spot_price=4035.5,
    executable_quote=4035.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert evaluation.allowed
  assert evaluation.measured["order_type_preference"] == "limit"
  assert evaluation.measured["entry_distribution"] == "zone_scale"
  assert evaluation.measured["planned_execution_route"] == "zone_split"


def test_sell_zone_first_leg_is_proximal_low_at_seventy_percent():
  # SELL zone 4035-4036.5, price still at the low edge (proximal, first
  # touched rising into resistance from below) when the signal fires.
  evaluation = evaluate_execution_policy(
    _policy_match(direction="SELL"),
    spot_price=4035.0,
    executable_quote=4035.0,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4035.0)
  assert measured["planned_leg_volume_ratios"] == pytest.approx([0.70, 0.30])
  # Second leg is one momentum step (0.5 * ATR=1.0 = 0.5) deeper into the
  # zone, toward the far edge (4036.5), never past it.
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4035.5)


def test_sell_zone_already_inside_anchors_first_leg_at_current_price():
  # Detection latency means price is often already past the near edge by
  # the time the signal confirms (e.g. sweep_reclaim needs a full bar to
  # confirm). A resting SELL limit left at the stale 4035 edge would sit
  # below the current quote - not a valid resting order, so it would never
  # place/fill. Leg 1 must anchor to the current quote instead.
  evaluation = evaluate_execution_policy(
    _policy_match(direction="SELL"),
    spot_price=4035.5,
    executable_quote=4035.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4035.5)
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4036.0)


def test_buy_zone_first_leg_is_proximal_high_at_seventy_percent():
  # BUY zone 4035-4036.5, price still at the high edge (proximal, first
  # touched falling into demand from above) when the signal fires.
  evaluation = evaluate_execution_policy(
    _policy_match(direction="BUY", structure_swing=4033.5),
    spot_price=4036.5,
    executable_quote=4036.5,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4036.5)
  assert measured["planned_leg_volume_ratios"] == pytest.approx([0.70, 0.30])
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4036.0)


def test_buy_zone_already_inside_anchors_first_leg_at_current_price():
  # Same detection-latency case as the SELL side: price already fell past
  # the near (high) edge into the zone by confirmation time - a resting
  # BUY limit left at the stale 4036.5 edge would sit above the current
  # quote and never place/fill. Leg 1 must anchor to the current quote.
  evaluation = evaluate_execution_policy(
    _policy_match(direction="BUY", structure_swing=4033.5),
    spot_price=4036.0,
    executable_quote=4036.0,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  measured = evaluation.measured
  assert measured["planned_leg_entry_prices"][0] == pytest.approx(4036.0)
  assert measured["planned_leg_entry_prices"][1] == pytest.approx(4035.5)


def test_scale_ladder_never_places_the_second_leg_past_the_far_edge():
  # Zone only 0.35 wide (above a 0.3*ATR qualification floor) with a
  # 0.5*ATR step - the second leg would overshoot past the far edge, so it
  # must clamp to the far edge instead.
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=4035.1,
    zone_low=4035.0,
    zone_high=4035.35,
    atr=1.0,
    zone_fill_enabled=True,
    zone_fill_min_atr=0.3,
    scale_step_atr=0.5,
  )
  assert plan.route == ROUTE_ZONE_SPLIT
  assert plan.planned_leg_entry_prices[1] == pytest.approx(4035.35)


def test_narrow_zone_falls_back_to_a_single_entry_at_that_price():
  # Zone width 0.3 with ATR=1.0 -> 0.3 < 0.5*ATR qualification floor, so the
  # ladder is unavailable; must act like a single entry at the computed
  # price rather than reject or silently keep waiting.
  evaluation = evaluate_execution_policy(
    _policy_match(entry_low=4035.0, entry_high=4035.3),
    spot_price=4035.15,
    executable_quote=4035.15,
    regime="range",
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert evaluation.allowed
  assert evaluation.measured["planned_execution_route"] == "market"
  assert evaluation.measured["planned_leg_entry_prices"] == []


def test_first_leg_fraction_and_step_are_configurable():
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="limit",
    entry_distribution="zone_scale",
    executable_quote=4035.0,
    zone_low=4035.0,
    zone_high=4040.0,
    atr=1.0,
    zone_fill_enabled=True,
    zone_fill_min_atr=0.5,
    scale_first_leg_fraction=0.5,
    scale_step_atr=1.0,
  )
  assert plan.planned_leg_volume_ratios == pytest.approx([0.5, 0.5])
  assert plan.planned_leg_entry_prices == pytest.approx([4035.0, 4036.0])


def test_market_preference_still_cannot_use_zone_scale():
  plan = resolve_execution_route_plan(
    direction="SELL",
    order_type_preference="market",
    entry_distribution="zone_scale",
    executable_quote=4035.1,
    zone_low=4035.0,
    zone_high=4040.0,
    atr=1.0,
    zone_fill_enabled=True,
  )
  assert not plan.valid
  assert plan.route == ROUTE_ZONE_SPLIT
