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
  assert _identity_ok(match)
  assert _valid_match(match)


@pytest.mark.asyncio
async def test_publish_hfs_live_calls_worker(monkeypatch):
  from app.scalping import publish as pub

  match = build_hfs_strategy_match(
    _opp(), _ctx(), bar_ts=120, quote_bid=4001.0, quote_ask=4002.0,
  )
  monkeypatch.setattr(
    pub.scanner,
    "_advance_setup_to_confirmed",
    AsyncMock(return_value=("setup-1", "thesis-1")),
  )
  publish = AsyncMock(return_value=worker.PublishResult(
    status=worker.PUBLISH_STATUS_PUBLISHED,
    plan_id="v7:setup-1",
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

  result = await publish_hfs_live(
    object(), match, symbol="XAU", bar_ts=120,
  )
  assert result is not None
  assert result.status == worker.PUBLISH_STATUS_PUBLISHED
  publish.assert_awaited_once()
  stamped = publish.await_args.args[1]
  assert stamped.thesis_id == "thesis-1"
