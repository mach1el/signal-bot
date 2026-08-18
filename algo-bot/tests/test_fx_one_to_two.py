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
  with pytest.raises(ValueError, match="requires policy=fx_fixed_2r_v1"):
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


def test_fx_technique_route_uses_two_entry_clips_not_five():
  from app.autotrade.execution_route import (
    ROUTE_MARKET_WITH_LIMIT_SCALE,
    SCALP_MICRO_CLIPS,
    resolve_execution_route_plan,
  )

  plan = resolve_execution_route_plan(
    direction="BUY",
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
  assert plan.route == ROUTE_MARKET_WITH_LIMIT_SCALE
  assert len(plan.planned_leg_entry_prices) == 2
  assert len(plan.planned_leg_entry_prices) != SCALP_MICRO_CLIPS
  assert pytest.approx(sum(plan.planned_leg_volume_ratios), abs=1e-6) == 1.0
  assert all(
    pytest.approx(ratio, abs=1e-4) == 0.5
    for ratio in plan.planned_leg_volume_ratios
  )


def test_fx_targeting_is_explicit_configuration_not_symbol_detection():
  cfg = _load_production_example().config
  for symbol in ("EURUSD", "GBPJPY"):
    effective = cfg.for_instrument(symbol)
    assert effective.policy_name == FX_FIXED_2R_V1_POLICY
    assert effective.targeting.mode is InstrumentTargetMode.FIXED_RR
    assert effective.targeting.target_r_multiples == _FX_TARGET_R_MULTIPLES
    assert effective.targeting.close_ratios == _FX_CLOSE_RATIOS
    assert effective.targeting.trail_after_r == 1.5
    assert effective.targeting.trail_to_r == 1.0
    assert effective.targeting.entry_clips == 2
    assert fixed_reward_risk(symbol, cfg) == 2.0
  assert fixed_reward_risk("XAU", cfg) is None
  assert fixed_reward_risk("XAUUSD", cfg) is None


def test_hfs_fixed_rr_has_no_one_r_fallback():
  cfg = _load_production_example().config
  # Room fits 1R (15) but not configured 2R (30): FX has no trade.
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
  assert fx is None


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


def test_fixed_rr_rejects_when_opposing_room_cannot_hold_two_r():
  cfg = _load_production_example().config
  match = _fx_match()
  evaluation = evaluate_execution_policy(
    match,
    spot_price=match.current_price,
    executable_quote=match.current_price,
    regime="trend",
    pip_size=0.0001,
    cfg=cfg,
    available_target_room_pips=10.0,
  )
  assert evaluation.allowed is False
  assert evaluation.reason_code == "fixed_rr_room_insufficient"
  assert evaluation.terminal is True


def test_gbpjpy_sell_uses_the_same_exact_two_r_contract():
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
  assert [target.close_ratio for target in plan.targets] == [
    Decimal("0.25"),
    Decimal("0.25"),
    Decimal("0.5"),
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
