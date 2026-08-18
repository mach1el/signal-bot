"""Tests for HFS live StrategyMatch + publish bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.autotrade import worker
from app.scalping.models import (
  ARCHETYPE_RANGE_SWEEP,
  CONTEXT_VERSION,
  OPPORTUNITY_VERSION,
  ScalpContextSnapshot,
  ScalpOpportunity,
)
from app.scalping.publish import build_hfs_strategy_match, publish_hfs_live
from app.autotrade.strategy_match import _identity_ok, _valid_match


pytestmark = pytest.mark.no_database


def _opp() -> ScalpOpportunity:
  return ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="opp1",
    context_id="ctx1",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=90,
    zone_low=4000.0,
    zone_high=4005.0,
    key_level=4002.0,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=90,
    trigger_price=4002.0,
    invalidation_price=3990.0,
    expected_target_price=4025.0,
    expected_target_pips=25.0,
    expected_stop_pips=15.0,
    expected_reward_risk=1.5,
    location_position=0.25,
    score=1.0,
    reasons=("lower_edge_sweep_reclaim",),
    expires_at=400,
    episode_id="ep1",
  )


def _ctx() -> ScalpContextSnapshot:
  return ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx1",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=3980.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.25,
    active_range_low=4000.0,
    active_range_high=4050.0,
    active_range_eq=4025.0,
    nearest_support_low=4000.0,
    nearest_support_high=4002.0,
    nearest_resistance_low=4048.0,
    nearest_resistance_high=4050.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=4.0,
  )


def test_build_hfs_strategy_match_is_valid():
  match = build_hfs_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.strategy == "HFS Range Sweep"
  assert match.structural_source == "hfs"
  assert match.structural_kind == "demand"
  assert match.direction == "BUY"
  assert match.full_take_profit_pips == 20
  assert match.targets_pips == (10, 20)
  assert match.family == "hfs"
  assert match.strategy_mode == "hfs_scalp"
  assert _identity_ok(match)
  assert _valid_match(match)


def test_build_hfs_1to2_publishes_half_at_one_r():
  # Owner 2026-08-11: 1:2 books half at 1R and half at 2R; final TP stays 2R.
  opp = _opp()
  # Rebuild with exact 1:2 geometry (stop 15 → target 30).
  from dataclasses import replace
  opp = replace(
    opp,
    expected_target_pips=30.0,
    expected_target_price=4030.0,
    expected_stop_pips=15.0,
    expected_reward_risk=2.0,
  )
  match = build_hfs_strategy_match(
    opp, _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.targets_pips == (10, 20)
  assert match.full_take_profit_pips == 20
  assert _valid_match(match)


def test_build_hfs_fx_publishes_single_two_r_target():
  from dataclasses import replace

  opp = replace(
    _opp(),
    symbol="EURUSD",
    expected_target_pips=30.0,
    expected_target_price=1.1630,
    expected_stop_pips=15.0,
    expected_reward_risk=2.0,
    zone_low=1.1600,
    zone_high=1.1605,
    key_level=1.1602,
    trigger_price=1.1602,
    invalidation_price=1.1585,
  )
  ctx = replace(_ctx(), symbol="EURUSD")
  match = build_hfs_strategy_match(
    opp, ctx, bar_ts=120, quote_bid=1.1601, quote_ask=1.1602,
  )
  assert match.targets_pips == (30,)
  assert match.full_take_profit_pips == 30
  assert _valid_match(match)


def test_build_hfs_1to1_stays_single_full_exit():
  from dataclasses import replace
  opp = replace(
    _opp(),
    expected_target_pips=15.0,
    expected_target_price=4015.0,
    expected_stop_pips=15.0,
    expected_reward_risk=1.0,
  )
  match = build_hfs_strategy_match(
    opp, _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.targets_pips == (10, 15)
  assert match.full_take_profit_pips == 15
  assert _valid_match(match)


def test_build_hfs_strategy_match_carries_execution_eligibility():
  # Regression: strategy_mode="hfs_scalp" routes through the generic
  # source="scanner_strategy_match" intent branch in worker.py (only
  # "mapped_zone_reaction" is exempt), and that branch hard-rejects any
  # match whose execution_eligibility is None as static_eligibility_missing.
  # HFS never runs the classic scanner.py detection path that normally
  # populates it, so every HFS opportunity died here unconditionally --
  # 34 of 55 live HFS publishes in production on 2026-08-06.
  match = build_hfs_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  assert match.execution_eligibility is not None
  assert match.execution_eligibility.allowed is True
  assert match.execution_eligibility.direction == "BUY"


def _patch_common(monkeypatch, pub, *, status, thesis_id="thesis-1"):
  monkeypatch.setattr(
    pub.scanner,
    "_advance_setup_to_confirmed",
    AsyncMock(return_value=("setup-1", thesis_id)),
  )
  persist = AsyncMock(side_effect=lambda _client, stamped: stamped)
  monkeypatch.setattr(pub, "_persist_hfs_match", persist)
  publish = AsyncMock(return_value=worker.PublishResult(
    status=status,
    plan_id="v8:setup-1",
    reason_code="candidate_published",
    zone_id="ep1",
    setup_id="setup-1",
  ))
  monkeypatch.setattr(pub.worker, "try_publish_executable_signal", publish)
  monkeypatch.setattr(
    pub,
    "runtime_config",
    type("C", (), {
      "runtime": type("R", (), {
        "auto_trade": type("A", (), {"enabled": True})(),
      })(),
    })(),
  )
  return persist, publish


@pytest.mark.asyncio
async def test_publish_hfs_live_calls_worker(monkeypatch):
  from app.scalping import publish as pub

  match = build_hfs_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  persist, publish = _patch_common(
    monkeypatch, pub, status=worker.PUBLISH_STATUS_PUBLISHED,
  )

  result = await publish_hfs_live(
    object(), match, symbol="XAU", bar_ts=120,
  )
  assert result is not None
  assert result.status == worker.PUBLISH_STATUS_PUBLISHED
  persist.assert_awaited_once()
  publish.assert_awaited_once()
  stamped = publish.await_args.args[1]
  assert stamped.thesis_id == "thesis-1"


@pytest.mark.asyncio
async def test_persist_hfs_match_writes_worker_keys():
  from app.scalping.publish import _persist_hfs_match

  match = build_hfs_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  stored: dict[str, str] = {}

  class FakeRedis:
    async def get(self, key):
      return stored.get(key)

    async def set(self, key, value, ex=None):
      stored[key] = value

  out = await _persist_hfs_match(FakeRedis(), match)
  assert out.match_id == match.match_id
  assert "auto_trade:strategy_match:XAU" in stored or any(
    "strategy_match" in k for k in stored
  )
  assert any("strategy_matches" in k for k in stored)
