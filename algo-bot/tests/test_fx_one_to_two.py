"""Config-driven fixed-RR policy for FX instruments."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match
from app.configuration.models.instruments import (
  FX_FIXED_2R_V1_POLICY,
  InstrumentConfig,
  InstrumentTargetMode,
  InstrumentTargetingConfig,
)
from app.core.instrument_geometry import fixed_reward_risk
from app.scalping.strategies import _select_target
from tests.test_config_effective_instrument_context import _load_production_example


pytestmark = pytest.mark.no_database

_FX_TARGET_R_MULTIPLES = (1.0, 1.5, 2.0)
_FX_CLOSE_RATIOS = (0.25, 0.25, 0.50)


def _targeting(reward_risk: float = 2.0) -> dict[str, object]:
  levels = tuple(
    value * reward_risk / 2.0 for value in _FX_TARGET_R_MULTIPLES
  )
  return {
    "mode": "fixed_rr",
    "reward_risk": reward_risk,
    "target_r_multiples": levels,
    "close_ratios": _FX_CLOSE_RATIOS,
    "trail_after_r": 1.5 * reward_risk / 2.0,
    "trail_to_r": 1.0 * reward_risk / 2.0,
    "entry_clips": 2,
  }


def test_fixed_rr_targeting_requires_ratio_and_matching_policy():
  with pytest.raises(ValueError, match="requires reward_risk"):
    InstrumentTargetingConfig(mode="fixed_rr")
  with pytest.raises(ValueError, match="must not set fixed-RR fields"):
    InstrumentTargetingConfig(mode="ladder_pips", reward_risk=2.0)
  with pytest.raises(ValueError, match="requires target_r_multiples"):
    InstrumentTargetingConfig(mode="fixed_rr", reward_risk=2.0)
  with pytest.raises(ValueError, match="must sum to 1.0"):
    InstrumentTargetingConfig(
      mode="fixed_rr",
      reward_risk=2.0,
      target_r_multiples=_FX_TARGET_R_MULTIPLES,
      close_ratios=(0.2, 0.2, 0.2),
    )
  with pytest.raises(ValueError, match="must be set together"):
    InstrumentTargetingConfig(
      mode="fixed_rr",
      reward_risk=2.0,
      target_r_multiples=_FX_TARGET_R_MULTIPLES,
      close_ratios=_FX_CLOSE_RATIOS,
      trail_after_r=1.5,
    )
  with pytest.raises(ValueError, match="fixed_rr targeting requires policy in"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy="xau_current_v1",
      targeting=_targeting(),
    )


def test_fx_policy_is_locked_to_two_r_partial_and_trail_contract():
  with pytest.raises(ValueError, match="targets 1R/1.5R/2R"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting=_targeting(1.5),
    )
  with pytest.raises(ValueError, match="targets 1R/1.5R/2R"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting={
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": (0.5, 1.5, 2.0),
        "close_ratios": (0.25, 0.25, 0.50),
        "trail_after_r": 1.5,
        "trail_to_r": 0.5,
        "entry_clips": 2,
      },
    )
  with pytest.raises(ValueError, match="entry_clips=2"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting={
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": _FX_TARGET_R_MULTIPLES,
        "close_ratios": _FX_CLOSE_RATIOS,
        "trail_after_r": 1.5,
        "trail_to_r": 1.0,
        "entry_clips": 5,
      },
    )


def _fx_match(symbol: str = "EURUSD") -> StrategyMatch:
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id="fx-fixed-rr-match",
    symbol=symbol,
    source_tf="M5",
    event_ts="1719999600",
    issued_at=1719999600,
    expires_at=1720003200,
    strategy="Trend Pullback",
    strategy_mode="with_trend",
    direction="BUY",
    key_level=1.1002,
    entry_low=1.1000,
    entry_high=1.1004,
    current_price=1.1002,
    confluence=3,
    reasons=("htf_uptrend", "demand_reaction"),
    atr=0.0008,
    structure_swing=1.0998,
    # Provisional room only. The final target must be derived from the stop.
    targets_pips=(50,),
    tier="A",
    family="trend_pullback",
    structural_zone_id="eurusd-demand-1.1000",
    structural_zone_low=1.1000,
    structural_zone_high=1.1004,
    structural_kind="demand",
    structural_timeframe="M15",
    htf_bias="up",
    regime_kind="trend",
  )


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_fx_technique_route_is_single_leg_market(direction: str):
  from app.autotrade.execution_route import (
    ROUTE_MARKET,
    resolve_execution_route_plan,
  )

  plan = resolve_execution_route_plan(
    direction=direction,
    order_type_preference="market",
    entry_distribution="single",
    executable_quote=1.1002,
    zone_low=1.1000,
    zone_high=1.1004,
    atr=0.0008,
    zone_fill_enabled=True,
    digits=5,
    strategy="FVG",
    strategy_family="zone",
    entry_clips=2,
  )
  assert plan.valid is True
  assert plan.route == ROUTE_MARKET
  assert plan.planned_leg_entry_prices == ()
  assert plan.planned_leg_volume_ratios == ()
  assert plan.immediate_market is True
  assert plan.routing_reason == "technique: single-leg market (no micro-grid)"


def test_fx_auto_plan_books_single_leg_market_for_fvg():
  cfg = _load_production_example().config
  match = replace(_fx_match(), strategy="FVG", family="zone")
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["planned_execution_route"] == "market"
  assert evaluation.measured.get("planned_leg_volume_ratios") in (None, [], ())

  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="fx-fvg-single-plan",
    setup_id="fx-fvg-single-setup",
    thesis_id="fx-fvg-single-thesis",
    pip_size=Decimal("0.0001"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
    approved_measured=evaluation.measured,
  )
  assert plan.entry.type == "market"
  assert plan.entry.legs == ()


def test_fx_targeting_is_explicit_configuration_not_symbol_detection():
  cfg = _load_production_example().config
  # GBPJPY front-loads close_ratios (fx_fixed_2r_frontload_v1, 2026 dig:
  # ATR ~180 pips/day vs EURUSD's ~70, moves reverse hard once they've run)
  # -- everything else about the 2R contract is identical across FX pairs.
  expected_close_ratios = {
    "EURUSD": _FX_CLOSE_RATIOS,
    "USDJPY": _FX_CLOSE_RATIOS,
    "GBPJPY": (0.40, 0.25, 0.35),
  }
  for symbol, close_ratios in expected_close_ratios.items():
    effective = cfg.for_instrument(symbol)
    assert effective.policy_name in (
      FX_FIXED_2R_V1_POLICY, "fx_fixed_2r_frontload_v1",
    )
    assert effective.targeting.mode is InstrumentTargetMode.FIXED_RR
    assert effective.targeting.target_r_multiples == _FX_TARGET_R_MULTIPLES
    assert effective.targeting.close_ratios == close_ratios
    assert effective.targeting.trail_after_r == 1.5
    assert effective.targeting.trail_to_r == 1.0
    assert effective.targeting.entry_clips == 2
    assert effective.execution.technique.require_sweep_body is False
    assert fixed_reward_risk(symbol, cfg) == 2.0
  assert fixed_reward_risk("XAU", cfg) is None
  assert fixed_reward_risk("XAUUSD", cfg) is None


def test_hfs_fixed_rr_prefers_two_r_then_falls_back_to_one_r():
  cfg = _load_production_example().config
  # Room fits 1R (15) but not preferred 2R (30): FX takes exactly 1R.
  gold = _select_target(
    direction="BUY",
    entry=1.16,
    room_pips=18,
    stop_pips=15,
    min_net=10,
    pip_size=0.0001,
    symbol="XAU",
    cfg=cfg,
  )
  fx = _select_target(
    direction="BUY",
    entry=1.16,
    room_pips=18,
    stop_pips=15,
    min_net=10,
    pip_size=0.0001,
    symbol="EURUSD",
    cfg=cfg,
  )
  assert gold is not None
  assert gold[1] == 15.0
  assert fx is not None
  assert fx[1] == 15.0


def test_hfs_fixed_rr_takes_two_r_when_room_fits():
  cfg = _load_production_example().config
  target = _select_target(
    direction="SELL",
    entry=216.0,
    room_pips=40,
    stop_pips=15,
    min_net=10,
    pip_size=0.01,
    symbol="GBPJPY",
    cfg=cfg,
  )
  assert target is not None
  assert target[1] == 30.0


def test_fx_reaction_stop_envelopes_diverge_while_gold_stays_locked():
  from app.autotrade.protective_stop import stop_bounds_for_reaction_room

  cfg = _load_production_example().config
  eurusd_min, eurusd_max, eurusd_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=50,
    pip_size=0.0001,
    cfg=cfg,
    symbol="EURUSD",
  )
  gbpjpy_min, gbpjpy_max, gbpjpy_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=50,
    pip_size=0.01,
    cfg=cfg,
    symbol="GBPJPY",
  )
  gold_min, gold_max, gold_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=90,
    pip_size=0.1,
    cfg=cfg,
    symbol="XAU",
  )
  assert (eurusd_min, eurusd_max) == (10, 18)
  assert eurusd_measured["fixed_rr_targeting"] is True
  assert (gbpjpy_min, gbpjpy_max) == (15, 30)
  assert gbpjpy_measured["fixed_rr_targeting"] is True
  assert (gold_min, gold_max) == (60, 60)
  assert gold_measured["fixed_rr_targeting"] is False


def test_fx_auto_reaction_books_pack_volume_multiplier():
  """Autonomous FX reaction must stamp 1.5× like manual /algo FX.

  Live 2026-08-21: GBPJPY Key Level filled 0.12 lots (raw equity table)
  while pack ``manual.risk_multiplier`` / ``fx_volume_multiplier`` promised
  1.5×. Scalp stays on range_max (2.0) without stacking the pack scale.
  """
  from tests.test_execution_pipeline_integrity import _policy_match

  cfg = _load_production_example().config
  fx_match = _fx_match("EURUSD")
  fx = evaluate_execution_policy(
    fx_match,
    spot_price=fx_match.current_price,
    executable_quote=fx_match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
  )
  assert fx.allowed is True
  assert fx.measured["instrument_volume_multiplier"] == pytest.approx(1.5)
  assert fx.measured["effective_risk_multiplier"] == pytest.approx(1.5)

  gold = evaluate_execution_policy(
    _policy_match(),
    spot_price=4102.5,
    regime="trend",
    pip_size=0.1,
    cfg=cfg,
  )
  assert gold.allowed is True
  assert gold.measured.get("instrument_volume_multiplier", 1.0) == pytest.approx(
    1.0
  )
  assert gold.measured["effective_risk_multiplier"] == pytest.approx(1.0)

  scalp_match = replace(
    fx_match,
    strategy="HFS Range Sweep",
    family="hfs",
    strategy_mode="scalp",
    tier="A",
  )
  scalp = evaluate_execution_policy(
    scalp_match,
    spot_price=fx_match.current_price,
    executable_quote=fx_match.current_price,
    regime="range",
    pip_size=0.0001,
    cfg=cfg,
  )
  # May reject on room/geometry; when allowed, pack must not stack onto 2.0.
  if scalp.allowed:
    assert scalp.measured["instrument_volume_multiplier"] == pytest.approx(1.0)
    assert scalp.measured["effective_risk_multiplier"] == pytest.approx(2.0)


def test_fx_trade_plan_books_partials_then_finishes_at_exactly_two_r():
  cfg = _load_production_example().config
  match = _fx_match()
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_policy_mode"] == "fixed_rr"
  assert evaluation.measured["effective_risk_multiplier"] == pytest.approx(1.5)

  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="fx-plan-1",
    setup_id="fx-setup-1",
    thesis_id="fx-thesis-1",
    pip_size=Decimal("0.0001"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
    approved_measured=evaluation.measured,
  )
  assert plan.risk.risk_multiplier == Decimal("1.5")

  entry = Decimal(str(evaluation.measured["planned_entry_price"]))
  risk = abs(entry - plan.stop.price)
  assert len(plan.targets) == 3
  assert [target.close_ratio for target in plan.targets] == [
    Decimal("0.25"),
    Decimal("0.25"),
    Decimal("0.5"),
  ]
  for target, multiple in zip(
    plan.targets,
    (Decimal("1"), Decimal("1.5"), Decimal("2")),
  ):
    assert abs(target.price - entry) == risk * multiple
  assert plan.management.be_after_target_id == "TP1"
  assert plan.management.trail_after_target_id == "TP2"
  assert plan.management.trail_to_target_id == "TP1"
  # The 50-pip match target was only provisional; stop geometry owns TP.
  assert abs(plan.targets[-1].price - entry) / Decimal("0.0001") != Decimal("50")


def test_fixed_rr_falls_back_to_one_r_when_two_r_does_not_fit():
  cfg = _load_production_example().config
  match = _fx_match()
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
    available_target_room_pips=15.0,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_room_fallback_used"] is True
  assert evaluation.measured["target_reward_risk"] == pytest.approx(1.0)
  assert evaluation.measured["planned_target_r_multiples"] == ["1.0"]
  assert evaluation.measured["planned_target_close_ratios"] == ["1.0"]
  assert "planned_trail_after_target_id" not in evaluation.measured

  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="fx-plan-fallback",
    setup_id="fx-setup-fallback",
    thesis_id="fx-thesis-fallback",
    pip_size=Decimal("0.0001"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
    approved_measured=evaluation.measured,
  )
  assert len(plan.targets) == 1
  assert plan.targets[0].close_ratio == Decimal("1")
  assert plan.management.be_after_target_id is None
  assert plan.management.trail_after_target_id is None
  assert plan.management.trail_to_target_id is None


def test_fixed_rr_rejects_when_opposing_room_cannot_hold_one_r():
  cfg = _load_production_example().config
  match = _fx_match()
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
    available_target_room_pips=9.0,
  )
  assert evaluation.allowed is False
  assert evaluation.reason_code == "fixed_rr_room_insufficient"
  assert evaluation.terminal is True
  assert evaluation.measured["target_fallback_reward_risk"] == 1.0


def test_fixed_rr_one_r_fallback_is_symmetric_for_sell():
  cfg = _load_production_example().config
  match = replace(
    _fx_match("GBPJPY"),
    match_id="gbpjpy-fallback-sell",
    direction="SELL",
    key_level=190.04,
    entry_low=190.00,
    entry_high=190.08,
    current_price=190.04,
    atr=0.12,
    structure_swing=190.12,
    targets_pips=(70,),
    structural_zone_id="gbpjpy-supply-fallback",
    structural_zone_low=190.00,
    structural_zone_high=190.08,
    structural_kind="supply",
    htf_bias="down",
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.01,
    cfg=cfg,
    available_target_room_pips=20.0,
  )
  assert evaluation.allowed is True
  assert evaluation.measured["target_room_fallback_used"] is True
  entry = Decimal(str(evaluation.measured["planned_entry_price"]))
  targets = tuple(
    Decimal(value) for value in evaluation.measured["planned_target_prices"]
  )
  assert len(targets) == 1
  assert targets[0] < entry
  assert evaluation.measured["planned_target_close_ratios"] == ["1.0"]


def test_gbpjpy_sell_uses_two_r_contract_with_frontloaded_partials():
  cfg = _load_production_example().config
  match = replace(
    _fx_match("GBPJPY"),
    match_id="gbpjpy-fixed-rr-match",
    direction="SELL",
    key_level=190.04,
    entry_low=190.00,
    entry_high=190.08,
    current_price=190.04,
    atr=0.12,
    structure_swing=190.12,
    targets_pips=(70,),
    structural_zone_id="gbpjpy-supply-190.00",
    structural_zone_low=190.00,
    structural_zone_high=190.08,
    structural_kind="supply",
    htf_bias="down",
  )
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.01,
    cfg=cfg,
  )
  assert evaluation.allowed is True
  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="gbpjpy-plan-1",
    setup_id="gbpjpy-setup-1",
    thesis_id="gbpjpy-thesis-1",
    pip_size=Decimal("0.01"),
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    cfg=cfg,
    max_volume=100_000_000,
  )
  entry = Decimal(str(evaluation.measured["planned_entry_price"]))
  assert plan.stop.price > entry > plan.targets[-1].price
  assert entry - plan.targets[-1].price == (
    plan.stop.price - entry
  ) * Decimal("2")
  # Front-loaded vs EURUSD/USDJPY's 25/25/50 -- same 1R/1.5R/2R ladder,
  # different partial-close split (fx_fixed_2r_frontload_v1).
  assert [target.close_ratio for target in plan.targets] == [
    Decimal("0.4"),
    Decimal("0.25"),
    Decimal("0.35"),
  ]
  assert plan.management.be_after_target_id == "TP1"
  assert plan.management.trail_after_target_id == "TP2"
  assert plan.management.trail_to_target_id == "TP1"


def test_xau_keeps_its_existing_ladder_policy():
  cfg = _load_production_example().config
  xau = cfg.for_instrument("XAU")
  assert xau.targeting.mode is InstrumentTargetMode.LADDER_PIPS
  assert xau.targeting.reward_risk is None
  assert xau.targeting.target_r_multiples == ()
  assert xau.targeting.close_ratios == ()
  assert xau.targeting.trail_after_r is None
  assert xau.targeting.trail_to_r is None
