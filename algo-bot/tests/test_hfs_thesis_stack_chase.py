"""Regressions for Aug 20 HFS gold dig: thesis leak, stack SELL targets, chase.

1. Same-direction stack must collapse multi-leg routes to single_limit so
   validate() does not check short HFS targets against the full zone.
2. TradePlanError from validate becomes TradePlanBuildRejected (claim release).
3. HFS chase quotes are execution-eligible in V8 (not waiting_retest).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock
import time

import pytest

from app.autotrade.active_exposure import apply_same_direction_stack_sizing
from app.autotrade.setup_lifecycle import active_thesis_key
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan_builder import (
  TradePlanBuildRejected,
  build_trade_plan_from_strategy_match,
)
from app.autotrade import worker
from app.persistence import redis_state
from tests.test_publish_trade_plan_v8 import _confirm_setup, _match


pytestmark = pytest.mark.no_database


@pytest.fixture(autouse=True)
def _no_news_by_default(monkeypatch):
  monkeypatch.setattr(
    worker, "event_in_window", AsyncMock(return_value=None),
  )


@pytest.fixture(autouse=True)
def _freeze_technique_killzone_hour(monkeypatch):
  from app.autotrade import killzone as kz

  real = kz.evaluate_killzone_gate
  real_win = kz.evaluate_reaction_publish_window

  def _gated(*, ts=None, hour=None, cfg=None, require=True):
    return real(ts=None, hour=14, cfg=cfg, require=require)

  def _window(*, ts=None, hour=None, cfg=None, require=True):
    return real_win(ts=None, hour=14, cfg=cfg, require=require)

  monkeypatch.setattr(kz, "evaluate_killzone_gate", _gated)
  monkeypatch.setattr(kz, "evaluate_reaction_publish_window", _window)


def test_same_direction_stack_collapses_market_scale_to_single_limit():
  measured = {
    "planned_stop_price": 4503.0,
    "planned_execution_route": "market_with_limit_scale",
    "planned_entry_price": 4500.0,
    "planned_leg_entry_prices": [4498.0, 4500.0],
    "planned_leg_volume_ratios": [0.8, 0.2],
    "effective_risk_multiplier": 1.0,
  }
  out = apply_same_direction_stack_sizing(measured, size_fraction=0.60)
  assert out["planned_execution_route"] == "single_limit"
  assert out["planned_leg_entry_prices"] == [4500.0]
  assert out["planned_leg_volume_ratios"] == [1.0]
  assert out["effective_risk_multiplier"] == pytest.approx(0.6)
  assert out["same_direction_stack"] is True


def test_hfs_sell_same_direction_stack_builds_valid_single_limit_plan():
  """Live 2026-08-20 XAU 49ffb74: stack → market_watch → TradePlanError.

  Short targets from the proximal planned entry sat above zone_low when
  entry_prices expanded to the full zone. Collapsing to single_limit keeps
  targets coherent with the order price.
  """
  match = StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id="hfs-stack-sell",
    symbol="XAU",
    source_tf="M1",
    event_ts="1787210000",
    issued_at=1787210000,
    expires_at=1787213600,
    strategy="HFS Range Sweep",
    strategy_mode="hfs_scalp",
    direction="SELL",
    key_level=4498.5,
    entry_low=4497.0,
    entry_high=4500.0,
    current_price=4498.5,
    confluence=1,
    reasons=("hfs",),
    atr=2.0,
    structure_swing=4505.0,
    targets_pips=(12, 24),
    tier="A",
    family="hfs",
    structural_zone_id="zone-hfs-sell",
    structural_zone_low=4497.0,
    structural_zone_high=4500.0,
    structural_kind="supply",
    structural_timeframe="M5",
    htf_bias="down",
    regime_kind="chop",
  )
  approved = {
    "planned_stop_price": 4503.0,
    "planned_execution_route": "market_with_limit_scale",
    "planned_entry_price": 4500.0,
    "planned_leg_entry_prices": [4498.0, 4500.0],
    "planned_leg_volume_ratios": [0.8, 0.2],
    "effective_risk_multiplier": 1.0,
    "stop_source": "protective_stop_plan",
  }
  plan = build_trade_plan_from_strategy_match(
    match,
    plan_id="plan-hfs-stack",
    setup_id="setup-hfs-stack",
    thesis_id="thesis-hfs-stack",
    pip_size=Decimal("0.1"),
    spot_price=4498.5,
    executable_quote=4498.5,
    regime="chop",
    max_volume=1000,
    approved_measured=approved,
    same_direction_stack=True,
  )
  assert plan.entry.type == "single_limit"
  assert plan.entry.order_price == Decimal("4500.0")
  assert all(target.price < plan.entry.order_price for target in plan.targets)


def test_validate_trade_plan_error_becomes_build_rejected():
  match = StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id="bad-targets",
    symbol="XAU",
    source_tf="M1",
    event_ts="1",
    issued_at=1,
    expires_at=10_000,
    strategy="HFS Range Sweep",
    strategy_mode="hfs_scalp",
    direction="SELL",
    key_level=4500.0,
    entry_low=4497.0,
    entry_high=4500.0,
    current_price=4498.0,
    confluence=1,
    reasons=("hfs",),
    atr=2.0,
    structure_swing=4505.0,
    targets_pips=(12,),
    tier="A",
    family="hfs",
    structural_zone_id="zone-bad",
    structural_zone_low=4497.0,
    structural_zone_high=4500.0,
    structural_kind="supply",
    structural_timeframe="M5",
    htf_bias="down",
    regime_kind="chop",
  )
  # Absolute targets above the single-limit entry (wrong side for SELL).
  approved = {
    "planned_stop_price": 4505.0,
    "planned_execution_route": "single_limit",
    "planned_entry_price": 4498.0,
    "target_policy_mode": "fixed_rr",
    "planned_target_prices": [4502.0],
    "planned_target_close_ratios": [1.0],
    "effective_risk_multiplier": 1.0,
    "stop_source": "protective_stop_plan",
  }
  with pytest.raises(TradePlanBuildRejected) as excinfo:
    build_trade_plan_from_strategy_match(
      match,
      plan_id="plan-bad",
      setup_id="setup-bad",
      thesis_id="thesis-bad",
      pip_size=Decimal("0.1"),
      spot_price=4498.0,
      executable_quote=4498.0,
      regime="chop",
      max_volume=1000,
      approved_measured=approved,
    )
  assert excinfo.value.reason_code == "trade_plan_invalid"
  assert "SELL targets" in excinfo.value.message


@pytest.mark.asyncio
async def test_publish_releases_thesis_when_build_raises_trade_plan_error(
  monkeypatch,
):
  """Uncaught TradePlanError used to orphan analysis:active_thesis (1681edb5)."""
  from app.autotrade.trade_plan import TradePlanError

  client = redis_state.get_client()
  match = _match(
    match_id="match-thesis-release",
    thesis_id="thesis-release-on-error",
    strategy="HFS Range Sweep",
    strategy_mode="hfs_scalp",
    family="hfs",
    structural_source="hfs",
  )
  await _confirm_setup(client, match)
  key = active_thesis_key("XAU", match.thesis_id)
  await client.delete(key)

  def _boom(*_args, **_kwargs):
    raise TradePlanError("SELL targets must all be below the entry zone")

  monkeypatch.setattr(worker, "build_trade_plan_from_strategy_match", _boom)

  spot = worker.AutoTradeSpot(
    price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1,
  )
  assert await worker._publish_trade_plan_v8(client, "XAU", spot, match) is None
  assert await client.get(key) is None


@pytest.mark.asyncio
async def test_hfs_chase_quote_is_not_parked_as_waiting_retest():
  """Activation allows chase; V8 must treat chase as executable.

  Assert we do not persist ``waiting_retest_entry_zone`` for an in-budget
  chase quote. Publish may still fail later on stop/envelope geometry; the
  contract under test is chase eligibility, not a full HFS fill.
  """
  from app.autotrade.execution_confirmation import (
    WAITING_RETEST,
    load_execution_confirmation,
  )

  client = redis_state.get_client()
  match = _match(
    match_id="match-hfs-chase",
    thesis_id="thesis-hfs-chase",
    strategy="HFS Range Sweep",
    strategy_mode="hfs_scalp",
    direction="SELL",
    family="hfs",
    structural_source="hfs",
    structural_kind="supply",
    key_level=4090.0,
    entry_low=4088.0,
    entry_high=4090.0,
    current_price=4086.0,
    structure_swing=4095.0,
    targets_pips=(20,),
    full_take_profit_pips=20,
    htf_bias="down",
  )
  await _confirm_setup(client, match)
  # 20 pips below zone_low (pip=0.1 → 2.0 price) — chase, not inside.
  chase = worker.AutoTradeSpot(
    price=4086.0,
    ts=int(time.time()),
    fresh=True,
    bid=4086.0,
    ask=4086.2,
  )
  await worker._publish_trade_plan_v8(client, "XAU", chase, match)
  conf = await load_execution_confirmation(client, match.match_id)
  assert conf is not None
  assert conf.phase != WAITING_RETEST
