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


def test_fixed_rr_targeting_requires_ratio_and_matching_policy():
  with pytest.raises(ValueError, match="requires reward_risk"):
    InstrumentTargetingConfig(mode="fixed_rr")
  with pytest.raises(ValueError, match="must not set reward_risk"):
    InstrumentTargetingConfig(mode="ladder_pips", reward_risk=2.0)
  with pytest.raises(ValueError, match="requires policy=fx_fixed_2r_v1"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy="xau_current_v1",
      targeting={"mode": "fixed_rr", "reward_risk": 2.0},
    )


def test_fx_policy_is_locked_to_exactly_two_r():
  with pytest.raises(ValueError, match="targeting.reward_risk=2.0"):
    InstrumentConfig(
      enabled=False,
      canonical_symbol="TESTFX",
      broker_symbol="TESTFX",
      policy=FX_FIXED_2R_V1_POLICY,
      targeting={"mode": "fixed_rr", "reward_risk": 1.5},
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


def test_fx_targeting_is_explicit_configuration_not_symbol_detection():
  cfg = _load_production_example().config
  for symbol in ("EURUSD", "GBPJPY"):
    effective = cfg.for_instrument(symbol)
    assert effective.policy_name == FX_FIXED_2R_V1_POLICY
    assert effective.targeting.mode is InstrumentTargetMode.FIXED_RR
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


def test_fx_reaction_stop_envelope_is_twelve_to_twenty_five():
  from app.autotrade.protective_stop import stop_bounds_for_reaction_room

  cfg = _load_production_example().config
  fx_min, fx_max, fx_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=50,
    pip_size=0.0001,
    cfg=cfg,
    symbol="EURUSD",
  )
  gold_min, gold_max, gold_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=90,
    pip_size=0.1,
    cfg=cfg,
    symbol="XAU",
  )
  assert (fx_min, fx_max) == (12, 25)
  assert fx_measured["fixed_rr_targeting"] is True
  assert (gold_min, gold_max) == (60, 60)
  assert gold_measured["fixed_rr_targeting"] is False


def test_fx_trade_plan_is_one_full_close_target_at_exactly_two_r():
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
  reward = abs(plan.targets[0].price - entry)
  assert reward == risk * Decimal("2")
  assert len(plan.targets) == 1
  assert plan.targets[0].close_ratio == Decimal("1")
  assert plan.management.be_after_target_id is None
  # The 50-pip match target was only provisional; stop geometry owns TP.
  assert reward / Decimal("0.0001") != Decimal("50")


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
  assert plan.stop.price > entry > plan.targets[0].price
  assert entry - plan.targets[0].price == (
    plan.stop.price - entry
  ) * Decimal("2")


def test_xau_keeps_its_existing_ladder_policy():
  cfg = _load_production_example().config
  xau = cfg.for_instrument("XAU")
  assert xau.targeting.mode is InstrumentTargetMode.LADDER_PIPS
  assert xau.targeting.reward_risk is None
