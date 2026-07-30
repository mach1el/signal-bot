"""ZoneWatch retained-zone domain tests."""

from __future__ import annotations

import pytest

from app.analysis.confluence_zone import confluence_zone_id
from app.autotrade import zone_watch as zw
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.fixture
def client():
  return redis_state.get_client()


def _zone_id(low=4113.0, high=4116.0, tags=("supply",)) -> str:
  return confluence_zone_id(
    "XAU", "sell", low, high, tags, atr=2.0, pip_size=0.01,
  )


@pytest.mark.asyncio
async def test_discover_zone_watch_is_idempotent(client):
  zone_id = _zone_id()
  record, created = await zw.discover_zone_watch(
    client,
    zone_id=zone_id,
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M5",
    structural_sources=("supply_demand",),
    confluence_tags=("supply",),
    grade=zw.GRADE_A,
    now=100,
  )
  assert created
  assert record.state == zw.DISCOVERED
  assert record.width == pytest.approx(3.0)

  again, created_again = await zw.discover_zone_watch(
    client,
    zone_id=zone_id,
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M5",
    structural_sources=("supply_demand",),
    confluence_tags=("supply",),
    grade=zw.GRADE_A,
    now=200,
  )
  assert not created_again
  assert again.zone_id == record.zone_id
  assert again.state == record.state
  assert again.touch_count == record.touch_count
  assert again.discovered_at == record.discovered_at
  assert again.last_confirmed_at == 200
  assert again.revision == record.revision + 1


def test_zone_identity_is_stable_across_coordinate_jitter():
  id_a = _zone_id(4007.47, 4012.47)
  id_b = confluence_zone_id(
    "XAU", "sell", 4007.41, 4012.53, ("supply",), atr=2.0, pip_size=0.01,
  )
  assert id_a == id_b


def test_zone_identity_is_stable_across_tag_order():
  id_a = confluence_zone_id(
    "XAU", "sell", 4113.0, 4116.0, ["supply", "key_level"],
    atr=2.0, pip_size=0.01,
  )
  id_b = confluence_zone_id(
    "XAU", "sell", 4113.0, 4116.0, ["key_level", "supply"],
    atr=2.0, pip_size=0.01,
  )
  assert id_a == id_b


@pytest.mark.asyncio
async def test_watched_zone_never_creates_a_strategy_match_or_ready_event(
  client,
):
  zone_id = _zone_id()
  await zw.discover_zone_watch(
    client,
    zone_id=zone_id,
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M5",
    structural_sources=("supply_demand",),
    confluence_tags=("supply",),
    grade=zw.GRADE_A,
  )
  await zw.transition_zone_watch(
    client, zone_id, zw.WATCHING_RETEST, reason_code="zone_discovered",
  )

  assert await client.get("auto_trade:strategy_match:XAU") is None
  assert await client.xlen("auto_trade:strategy_match_ready") == 0
  assert await client.get(f"analysis:setup:{zone_id}") is None


@pytest.mark.asyncio
async def test_price_entering_zone_moves_to_evaluating_and_back(client):
  zone_id = _zone_id()
  await zw.discover_zone_watch(
    client, zone_id=zone_id, symbol="XAU", direction="SELL",
    low=4113.0, high=4116.0, source_timeframe="M5",
    structural_sources=("supply_demand",), confluence_tags=("supply",),
    grade=zw.GRADE_A,
  )
  await zw.transition_zone_watch(client, zone_id, zw.WATCHING_RETEST)
  evaluating, changed = await zw.transition_zone_watch(
    client, zone_id, zw.EVALUATING, reason_code="price_entered_zone",
  )
  assert changed
  assert evaluating.state == zw.EVALUATING

  watching_again, changed = await zw.transition_zone_watch(
    client, zone_id, zw.WATCHING_RETEST, reason_code="no_valid_signal",
  )
  assert changed
  assert watching_again.state == zw.WATCHING_RETEST


@pytest.mark.asyncio
async def test_structure_break_invalidates_the_zone(client):
  zone_id = _zone_id()
  await zw.discover_zone_watch(
    client, zone_id=zone_id, symbol="XAU", direction="SELL",
    low=4113.0, high=4116.0, source_timeframe="M5",
    structural_sources=("supply_demand",), confluence_tags=("supply",),
    grade=zw.GRADE_A,
  )
  await zw.transition_zone_watch(client, zone_id, zw.WATCHING_RETEST)
  invalidated, changed = await zw.transition_zone_watch(
    client, zone_id, zw.INVALIDATED, reason_code="structure_broke",
  )
  assert changed
  assert invalidated.state == zw.INVALIDATED

  with pytest.raises(zw.ZoneWatchError):
    await zw.transition_zone_watch(client, zone_id, zw.WATCHING_RETEST)


@pytest.mark.asyncio
async def test_repeated_touches_downgrade_then_exhaust_the_zone(client):
  zone_id = _zone_id()
  await zw.discover_zone_watch(
    client, zone_id=zone_id, symbol="XAU", direction="SELL",
    low=4113.0, high=4116.0, source_timeframe="M5",
    structural_sources=("supply_demand",), confluence_tags=("supply",),
    grade=zw.GRADE_A,
  )
  after_1 = await zw.record_zone_touch(client, zone_id)
  assert after_1.touch_count == 1
  assert after_1.grade == zw.GRADE_A
  assert zw.is_actively_watchable(after_1)

  after_2 = await zw.record_zone_touch(client, zone_id)
  assert after_2.touch_count == 2
  assert after_2.grade == zw.GRADE_B
  assert zw.is_actively_watchable(after_2)

  after_3 = await zw.record_zone_touch(client, zone_id)
  assert after_3.touch_count == 3
  assert after_3.state == zw.EXHAUSTED
  assert not zw.is_actively_watchable(after_3)


@pytest.mark.asyncio
async def test_htf_evidence_prevents_exhaustion_past_the_touch_threshold(
  client,
):
  zone_id = _zone_id()
  await zw.discover_zone_watch(
    client, zone_id=zone_id, symbol="XAU", direction="SELL",
    low=4113.0, high=4116.0, source_timeframe="H1",
    structural_sources=("supply_demand",), confluence_tags=("supply",),
    grade=zw.GRADE_A,
  )
  await zw.record_zone_touch(client, zone_id)
  await zw.record_zone_touch(client, zone_id)
  after_3 = await zw.record_zone_touch(client, zone_id, htf_evidence=True)
  assert after_3.touch_count == 3
  assert after_3.state != zw.EXHAUSTED


def test_only_a_and_b_grades_are_actively_watchable():
  record = zw.ZoneWatch(
    version=1, zone_id="z", symbol="XAU", direction="SELL",
    low=4113.0, high=4116.0, width=3.0, source_timeframe="M5",
    structural_sources=(), confluence_tags=(), grade=zw.GRADE_C, score=1.0,
    freshness=0, touch_count=0, discovered_at=0, last_confirmed_at=0,
    last_touch_at=None, invalidation_price=None, state=zw.DISCOVERED,
    market_map_id="", structure_signature="", updated_at=0,
  )
  assert not zw.is_actively_watchable(record)
