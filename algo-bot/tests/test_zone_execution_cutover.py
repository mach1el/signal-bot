from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade.strategy_match import StrategyMatch
from app.autotrade import worker
from app.autotrade import zone_execution_cutover as cutover
from app.autotrade import zone_watch as zw
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.fixture
def client():
  return redis_state.get_client()


def _match(*, strategy: str = "Key Level Reaction") -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id="setup-1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy=strategy,
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4114.5,
    entry_low=4113.0,
    entry_high=4116.0,
    current_price=4114.5,
    confluence=3,
    reasons=("supply rejection",),
    atr=4.0,
    structure_swing=4116.0,
    targets_pips=(300,),
    family="key_level" if strategy != "Range Edge Scalp" else "range",
    structural_source="key_level",
    confluence_zone_id="zone-1",
    structural_zone_id="zone-1",
    structural_zone_low=4113.0,
    structural_zone_high=4116.0,
    touch_bar_ts="1785390000",
    confirmation_bar_ts="1785390300",
    reaction_type="rejection_choch",
  )


def _result(*, low: float = 4113.0, high: float = 4116.0, setup="Key Level Reaction"):
  return SimpleNamespace(
    setup=setup,
    direction="SELL",
    entry_zone=SimpleNamespace(low=low, high=high),
    structural_low=low,
    structural_high=high,
    structural_timeframe="M15",
    structural_source="key_level",
    structural_id="zone-1",
    confluence_zone_id="zone-1",
    confluence_tags=("key_level", "supply"),
    confluence=3,
    source_score=12.0,
    confirmation_type="rejection_choch",
    confirmation="rejection_choch",
    execution_eligibility=SimpleNamespace(allowed=True, market_map_id="map-1"),
  )


def test_grade_policy_is_explicit():
  assert cutover._grade(_result(), "M15") == zw.GRADE_A
  assert cutover._grade(_result(setup="Range Edge Scalp"), "M5") == zw.GRADE_B


@pytest.mark.asyncio
async def test_width_contract_records_telemetry_even_when_legacy_gate_is_off(
  client,
  monkeypatch,
):
  monkeypatch.setattr(cutover.settings, "scanner_zone_width_gate_enabled", False)
  eligible = await cutover._record_width_telemetry(
    client,
    symbol="XAU",
    zone_id="narrow",
    result=_result(low=4113.0, high=4113.5),
    source_tf="M5",
  )
  assert eligible is False
  raw = await client.get(cutover.width_telemetry_key("XAU", "narrow"))
  assert raw is not None
  assert '"rejection_reason":"zone_too_narrow"' in raw


@pytest.mark.asyncio
async def test_zone_presence_counts_one_touch_per_visit(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    now=100,
  )
  await zw.transition_zone_watch(client, record.zone_id, zw.WATCHING_RETEST)
  first, entered = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=200, htf_evidence=True,
  )
  second, entered_again = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=260, htf_evidence=True,
  )
  assert entered is True
  assert entered_again is False
  assert first.touch_count == second.touch_count == 1
  assert first.episode_id == second.episode_id


@pytest.mark.asyncio
async def test_rediscovery_refreshes_metadata_without_resetting_touch(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
    now=100,
  )
  touched = await zw.record_zone_touch(client, record.zone_id, now=150)
  refreshed, created = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4112.9,
    high=4116.1,
    source_timeframe="M15",
    structural_sources=("key_level", "supply_demand"),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    score=14.0,
    now=300,
  )
  assert created is False
  assert refreshed.touch_count == touched.touch_count == 1
  assert refreshed.last_confirmed_at == 300
  assert refreshed.low == pytest.approx(4112.9)
  assert refreshed.score == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_active_plan_state_race_is_reconciled(monkeypatch):
  match = _match()
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(return_value=worker.PublishResult(
      status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
      plan_id="v7:setup-1",
      reason_code="zone_watching_retest",
      zone_id="zone-1",
      setup_id="setup-1",
    )),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v7_state",
    AsyncMock(return_value=worker.ExistingV7State(
      plan_id="v7:setup-1",
      setup_state="plan_published",
      plan_state="armed",
      plan_exists=True,
      already_published=True,
      already_terminal=False,
      owner_matches=True,
    )),
  )
  result = await cutover._safe_direct_publish(
    SimpleNamespace(), match, symbol="XAU",
  )
  assert result.status == worker.PUBLISH_STATUS_DUPLICATE_RECONCILED


@pytest.mark.asyncio
async def test_direct_exception_returns_durable_fallback(monkeypatch):
  match = _match()
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(side_effect=RuntimeError("redis blip")),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v7_state",
    AsyncMock(return_value=worker.ExistingV7State(
      plan_id="v7:setup-1",
      setup_state="worker_acknowledged",
      plan_state=None,
      plan_exists=False,
      already_published=False,
      already_terminal=False,
      owner_matches=False,
    )),
  )
  result = await cutover._safe_direct_publish(
    SimpleNamespace(), match, symbol="XAU",
  )
  assert result.status == worker.PUBLISH_STATUS_REMAINED_WATCHING
  assert result.reason_code == "direct_publish_failed_durable_fallback"


@pytest.mark.asyncio
async def test_waiting_zone_creates_no_setup_or_ready_event(client, monkeypatch):
  from app.analysis import scanner

  result = _result()
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (_match(), None, {}),
  )
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4100.0, 4100.2, 1_785_390_000)),
  )

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is None
  assert await client.get("analysis:setup:setup-1") is None
  assert await client.xlen("auto_trade:strategy_match_ready") == 0
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state == zw.WATCHING_RETEST
