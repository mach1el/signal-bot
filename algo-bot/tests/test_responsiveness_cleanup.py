"""Event-loop responsiveness: stale scalp armed sweep + ZoneWatch index MGET."""

from __future__ import annotations

import time

import fakeredis
import pytest
import pytest_asyncio

from app.autotrade.setup_expiry_sweeper import sweep_stale_scalp_armed_once
from app.autotrade import zone_watch as zw
from app.scalping.lifecycle import active_key, save_lifecycle
from app.scalping.models import ARMED, EXPIRED, ScalpLifecycleRecord


pytestmark = pytest.mark.no_database


@pytest_asyncio.fixture
async def client():
  redis = fakeredis.FakeAsyncRedis(decode_responses=True)
  redis._apexvoid_allow_non_atomic_test_fallback = True
  try:
    yield redis
  finally:
    await redis.aclose()


@pytest.mark.asyncio
async def test_sweep_stale_scalp_armed_removes_old_members(client):
  now = int(time.time())
  stale = ScalpLifecycleRecord(
    opportunity_id="opp-stale",
    episode_id="ep-1",
    state=ARMED,
    context_id="ctx-1",
    updated_at=now - 7200,
    reason_code="armed",
    measured={},
  )
  fresh = ScalpLifecycleRecord(
    opportunity_id="opp-fresh",
    episode_id="ep-2",
    state=ARMED,
    context_id="ctx-2",
    updated_at=now - 60,
    reason_code="armed",
    measured={},
  )
  await save_lifecycle(client, "XAU", stale)
  await save_lifecycle(client, "XAU", fresh)
  await client.sadd(active_key("XAU"), "orphan-missing")

  cleaned = await sweep_stale_scalp_armed_once(client, now=now, max_age_seconds=3600)
  assert cleaned == 2
  members = await client.smembers(active_key("XAU"))
  assert members == {"opp-fresh"}
  updated = await client.get("scalp:lifecycle:XAU:opp-stale")
  assert EXPIRED in updated


@pytest.mark.asyncio
async def test_list_active_zone_watches_uses_mget_and_drops_stale(client):
  live, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-live",
    symbol="XAU",
    direction="BUY",
    low=4400.0,
    high=4405.0,
    source_timeframe="M5",
    structural_sources=("fvg",),
    confluence_tags=("fvg",),
    grade=zw.GRADE_A,
  )
  await zw.transition_zone_watch(client, live.zone_id, zw.WATCHING_RETEST)
  await client.sadd(zw.ZONE_WATCH_INDEX_KEY, "zone-missing")

  listed = await zw.list_active_zone_watches(client, symbol="XAU")
  assert [item.zone_id for item in listed] == ["zone-live"]
  assert not await client.sismember(zw.ZONE_WATCH_INDEX_KEY, "zone-missing")
  assert await client.sismember(
    zw.zone_watch_symbol_index_key("XAU"), "zone-live",
  )


@pytest.mark.asyncio
async def test_symbol_index_lazily_backfills_legacy_global_records(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-legacy-eurusd",
    symbol="EURUSD",
    direction="SELL",
    low=1.1000,
    high=1.1010,
    source_timeframe="M5",
    structural_sources=("fvg",),
    confluence_tags=("fvg",),
    grade=zw.GRADE_A,
  )
  symbol_index = zw.zone_watch_symbol_index_key("EURUSD")
  await client.delete(symbol_index)
  await client.delete(zw._zone_watch_symbol_index_built_key("EURUSD"))

  assert await client.sismember(zw.ZONE_WATCH_INDEX_KEY, record.zone_id)
  assert not await client.sismember(symbol_index, record.zone_id)

  listed = await zw.list_active_zone_watches(client, symbol="eurusd")

  assert [item.zone_id for item in listed] == [record.zone_id]
  assert await client.sismember(symbol_index, record.zone_id)


@pytest.mark.asyncio
async def test_built_symbol_listing_does_not_fetch_other_symbols(
  client, monkeypatch,
):
  xau, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-xau",
    symbol="XAU",
    direction="BUY",
    low=4400.0,
    high=4405.0,
    source_timeframe="M5",
    structural_sources=("fvg",),
    confluence_tags=("fvg",),
    grade=zw.GRADE_A,
  )
  eurusd, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-eurusd",
    symbol="EURUSD",
    direction="SELL",
    low=1.1000,
    high=1.1010,
    source_timeframe="M5",
    structural_sources=("fvg",),
    confluence_tags=("fvg",),
    grade=zw.GRADE_A,
  )

  # First call performs the legacy migration and marks every represented
  # symbol. The measured call below must use only XAU's local membership set.
  await zw.list_active_zone_watches(client, symbol="XAU")
  mget_calls: list[list[str]] = []
  original_mget = client.mget

  async def tracked_mget(keys):
    mget_calls.append(list(keys))
    return await original_mget(keys)

  monkeypatch.setattr(client, "mget", tracked_mget)
  listed = await zw.list_active_zone_watches(client, symbol="XAU")

  assert [item.zone_id for item in listed] == [xau.zone_id]
  assert mget_calls == [[zw.zone_watch_key(xau.zone_id)]]
  assert zw.zone_watch_key(eurusd.zone_id) not in mget_calls[0]


@pytest.mark.asyncio
async def test_terminal_transition_removes_global_and_symbol_membership(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-terminal",
    symbol="GBPUSD",
    direction="BUY",
    low=1.2700,
    high=1.2710,
    source_timeframe="M5",
    structural_sources=("demand",),
    confluence_tags=("demand",),
    grade=zw.GRADE_A,
  )
  symbol_index = zw.zone_watch_symbol_index_key(record.symbol)
  assert await client.sismember(symbol_index, record.zone_id)

  await zw.transition_zone_watch(client, record.zone_id, zw.INVALIDATED)

  assert not await client.sismember(zw.ZONE_WATCH_INDEX_KEY, record.zone_id)
  assert not await client.sismember(symbol_index, record.zone_id)
