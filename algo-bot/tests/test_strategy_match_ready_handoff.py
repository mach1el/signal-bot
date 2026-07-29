"""End-to-end Scanner -> Worker handoff regressions for the P0 incident."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.analysis import scanner
from app.analysis.market_map import MapEntry, MarketMap
from app.analysis.types import Zone
from app.autotrade import worker
from app.autotrade.gate import AutoScalpDecision
from app.autotrade.multi_match import strategy_matches_key
from app.autotrade.strategy_match import strategy_match_key
from app.autotrade.strategy_match_ready import (
  READY_GROUP,
  READY_STREAM,
  ensure_ready_group,
  ready_dedup_key,
)
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.setup_lifecycle import (
  INVALIDATED,
  PLAN_PUBLISHED,
  active_thesis_key,
  load_setup,
  transition_setup,
)
from app.autotrade.setup_card import load_forming_card_status_snapshot
from app.autotrade.trade_plan_stream import read_trade_plan
from app.autotrade.trend import RegimeInfo, TrendDecision
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _frames(now: int, price: float = 4101.0) -> dict[str, pd.DataFrame]:
  def frame(freq: str, periods: int) -> pd.DataFrame:
    index = pd.date_range(
      pd.Timestamp(now, unit="s", tz="UTC") - pd.Timedelta(freq) * periods,
      periods=periods,
      freq=freq,
    )
    return pd.DataFrame({
      "open": [price - 0.2] * periods,
      "high": [price + 0.5] * periods,
      "low": [price - 0.5] * periods,
      "close": [price] * periods,
      "volume": [100.0] * periods,
    }, index=index)

  return {
    "M1": frame("1min", 80),
    "M5": frame("5min", 80),
    "M15": frame("15min", 80),
    "H1": frame("1h", 80),
  }


def _context(frames: dict[str, pd.DataFrame], now: int):
  return SimpleNamespace(
    tf="M5",
    htf_bias="down",
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={
      "M5": SimpleNamespace(
        bias="down",
        scalp_range=None,
        scalp_barriers=[],
      ),
      "M15": SimpleNamespace(bias="down"),
      "H1": SimpleNamespace(bias="down"),
    },
    settings=SimpleNamespace(range_scalp_enabled=True),
    frames=frames,
    regime=SimpleNamespace(kind="trend"),
    spot_price=4101.0,
    spot_ts=now,
    trigger_ts=str(now),
    analysis=SimpleNamespace(per_tf={}),
  )


def _reaction(now: int) -> scanner.DetectionResult:
  return scanner.DetectionResult(
    setup="Supply Zone Reaction",
    direction="SELL",
    key_level=4101.0,
    entry_zone=Zone(4100.0, 4102.0, "supply", score=4.0),
    current_price=4101.0,
    confluence=4,
    reasons=["supply rejection", "HTF bias down"],
    mode="with_bias",
    confirmation="wick_rejection",
    structural_source="supply_demand",
    structural_id="p0-supply-4100-4102",
    structural_low=4100.0,
    structural_high=4102.0,
    structural_timeframe="M5",
    structural_kind="supply",
    confirmation_type="wick_rejection",
    confirmation_bar_ts=str(now),
    touch_bar_ts=str(now - 300),
    source_touches=1,
    bias_relationship="with_bias",
  )


def _market_map(
  now: int,
  *entries: MapEntry,
) -> MarketMap:
  return MarketMap(
    entries=list(entries),
    price=4101.0,
    eq=None,
    box_low=None,
    box_high=None,
    bias="down",
    bias_tf="H1",
    map_id=f"map-{now}",
    generated_at=now,
    actionable_entries=list(entries),
  )


async def _configure(
  monkeypatch,
  *,
  market_map: MarketMap,
  frames: dict[str, pd.DataFrame],
  now: int,
  bid: float = 4101.0,
  ask: float = 4101.2,
) -> tuple[object, AsyncMock]:
  client = redis_state.get_client()
  context = _context(frames, now)
  notify = AsyncMock(return_value=SimpleNamespace(message_id=7001))

  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "H1,M15")
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "scanner_gate_max_source_touches", 0)
  monkeypatch.setattr(scanner.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(
    scanner.settings, "auto_trade_strategy_match_enabled", True,
  )
  monkeypatch.setattr(
    scanner.settings, "scanner_actionability_gate_enabled", False,
  )
  monkeypatch.setattr(
    scanner.settings, "key_level_role_ambiguity_gate_enabled", False,
  )
  monkeypatch.setattr(
    scanner,
    "_load_market_context_for_symbol",
    AsyncMock(return_value=(context, frames)),
  )
  monkeypatch.setattr(
    scanner, "build_map", lambda *_args, **_kwargs: market_map,
  )

  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_symbols", "XAU")
  monkeypatch.setattr(
    worker.settings, "auto_trade_strategy_match_enabled", True,
  )
  monkeypatch.setattr(worker.settings, "auto_trade_news_guard_minutes", 0)
  monkeypatch.setattr(
    worker.settings, "auto_trade_supply_reaction_enabled", True,
  )
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  monkeypatch.setattr(
    worker, "_load_frames", AsyncMock(return_value=frames),
  )
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(
      price=(bid + ask) / 2,
      ts=now,
      fresh=True,
      bid=bid,
      ask=ask,
    )),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_auto_scalp_gate",
    lambda *_args, **_kwargs: AutoScalpDecision("waiting_for_box"),
  )
  monkeypatch.setattr(
    worker,
    "_resolve_worker_range",
    AsyncMock(return_value=(
      AutoScalpDecision("waiting_for_box"),
      None,
      {},
    )),
  )
  monkeypatch.setattr(
    worker,
    "classify_regime",
    lambda *_args, **_kwargs: RegimeInfo(
      "trend", "down", 3, 1.0, True, None, ("p0 replay",),
    ),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_trend_gate",
    lambda *_args, **_kwargs: TrendDecision("no_setup"),
  )
  monkeypatch.setattr(worker, "_htf_zones", lambda *_args, **_kwargs: [])
  monkeypatch.setattr(worker, "_htf_levels", lambda *_args, **_kwargs: [])
  return client, notify


async def _scan(
  client,
  notify,
  reaction: scanner.DetectionResult,
  now: int,
  *,
  expect_sent: bool = True,
) -> None:
  sent = await scanner._handle_event(
    f"XAU:M5:{now}",
    client=client,
    detectors=[lambda _ctx: reaction],
    notify=notify,
  )
  if expect_sent:
    assert sent


def _plan_count_key() -> str:
  return worker.settings.auto_trade_trade_plan_stream


@pytest_asyncio.fixture
async def real_ready_redis():
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.fail("REAL_REDIS_URL is required for pending recovery tests")
  client = Redis.from_url(url, decode_responses=True)
  await client.ping()
  await client.flushdb()
  try:
    yield client
  finally:
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_m1_before_m5_ready_event_publishes_without_another_m1(
  monkeypatch,
):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
  )

  await worker._handle_event(f"XAU:M1:{now}", client=client)
  assert await client.xlen(_plan_count_key()) == 0

  await _scan(client, notify, _reaction(now), now)
  assert await client.xlen(_plan_count_key()) == 0
  queued_match = worker.StrategyMatch.from_json(
    await client.get(strategy_match_key("XAU")),
  )
  assert queued_match is not None
  queued_route = json.loads(
    await client.get(route_outcome_key("XAU", queued_match.match_id)),
  )
  assert queued_route["status"] == "queued"
  assert queued_route["reason_code"] == "strategy_match_ready_enqueued"

  consume_once = getattr(worker, "_consume_strategy_match_ready_once", None)
  assert consume_once is not None, "no durable scanner -> worker wake-up exists"
  await consume_once(client=client)

  assert await client.xlen(_plan_count_key()) == 1
  match = worker.StrategyMatch.from_json(
    await client.get(strategy_match_key("XAU")),
  )
  assert match is not None
  route = json.loads(
    await client.get(route_outcome_key("XAU", match.match_id)),
  )
  plan_id = worker._v7_plan_id(match)
  assert route["status"] == "candidate_published"
  assert route["candidate_id"] == plan_id
  assert route["winner_intent_id"] == f"strategy:{match.match_id}"
  assert route["measured"]["event_id"]
  assert route["measured"]["scanner_event_ts"] == str(now)
  assert route["measured"]["ready_event_ts"] <= (
    route["measured"]["worker_received_ts"]
  )
  assert (await load_setup(client, match.match_id)).state == PLAN_PUBLISHED
  card_status = await load_forming_card_status_snapshot(
    client,
    match.match_id,
  )
  assert card_status is not None
  assert card_status.state == "plan_published"


@pytest.mark.asyncio
async def test_m5_before_m1_and_ready_retry_publish_exactly_one_plan(
  monkeypatch,
):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
  )

  await _scan(client, notify, _reaction(now), now)
  raw = await client.get(strategy_match_key("XAU"))
  assert raw is not None
  await worker._handle_event(f"XAU:M1:{now}", client=client)

  consume_once = getattr(worker, "_consume_strategy_match_ready_once", None)
  if consume_once is not None:
    await consume_once(client=client)

  assert await client.xlen(_plan_count_key()) == 1


@pytest.mark.asyncio
async def test_nearby_quote_outside_zone_waits_instead_of_distance_chasing(
  monkeypatch,
):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
    bid=4099.0,
    ask=4099.2,
  )

  await _scan(client, notify, _reaction(now), now)
  await worker._handle_event(f"XAU:M1:{now}", client=client)

  assert await client.xlen(_plan_count_key()) == 0
  raw = await client.get(strategy_match_key("XAU"))
  assert raw is not None
  match = worker.StrategyMatch.from_json(raw)
  assert match is not None
  assert await read_trade_plan(client, worker._v7_plan_id(match)) is None


@pytest.mark.asyncio
async def test_concurrent_m1_and_ready_event_publish_exactly_one_plan(
  monkeypatch,
):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
  )
  await _scan(client, notify, _reaction(now), now)

  consume_once = getattr(worker, "_consume_strategy_match_ready_once", None)
  assert consume_once is not None
  await asyncio.gather(
    worker._handle_event(f"XAU:M1:{now}", client=client),
    consume_once(client=client),
  )

  assert await client.xlen(_plan_count_key()) == 1


@pytest.mark.asyncio
async def test_duplicate_detection_and_ready_events_publish_one_plan(
  monkeypatch,
):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
  )
  await _scan(client, notify, _reaction(now), now)
  original = (await client.xrange(READY_STREAM))[0][1]

  await _scan(
    client,
    notify,
    _reaction(now),
    now,
    expect_sent=False,
  )
  assert await client.xlen(READY_STREAM) == 1

  await client.xadd(READY_STREAM, original)
  assert await client.xlen(READY_STREAM) == 2
  assert await worker._consume_strategy_match_ready_once(client=client)
  assert await worker._consume_strategy_match_ready_once(client=client)

  assert await client.xlen(_plan_count_key()) == 1
  pending = await client.xpending(READY_STREAM, READY_GROUP)
  assert pending["pending"] == 0


@pytest.mark.asyncio
async def test_terminal_setup_ready_event_is_acked_without_plan(monkeypatch):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
  )
  await _scan(client, notify, _reaction(now), now)
  raw = await client.get(strategy_match_key("XAU"))
  match = worker.StrategyMatch.from_json(raw)
  assert match is not None
  await transition_setup(
    client,
    match.match_id,
    INVALIDATED,
    reason_code="test_terminal_before_ready",
  )

  assert await worker._consume_strategy_match_ready_once(client=client)
  assert await client.xlen(_plan_count_key()) == 0
  pending = await client.xpending(READY_STREAM, READY_GROUP)
  assert pending["pending"] == 0


@pytest.mark.asyncio
async def test_startup_recovery_skips_match_owned_by_another_setup(monkeypatch):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
  )
  await _scan(client, notify, _reaction(now), now)
  match = worker.StrategyMatch.from_json(
    await client.get(strategy_match_key("XAU")),
  )
  assert match is not None
  assert match.thesis_id
  await client.delete(
    READY_STREAM,
    ready_dedup_key(match.match_id),
    ready_dedup_key(match.match_id, recovery=True),
  )
  await client.set(
    active_thesis_key("XAU", match.thesis_id),
    "different-setup",
    ex=60,
  )

  await worker._recover_unfinished_strategy_matches(client)

  assert await client.xlen(READY_STREAM) == 0


@pytest.mark.real_redis
@pytest.mark.asyncio
async def test_pending_ready_event_is_recovered_after_worker_restart(
  monkeypatch,
  real_ready_redis,
):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  _client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now),
    frames=frames,
    now=now,
  )
  await _scan(real_ready_redis, notify, _reaction(now), now)
  await ensure_ready_group(real_ready_redis)
  claimed = await real_ready_redis.xreadgroup(
    READY_GROUP,
    "worker-that-stopped",
    {READY_STREAM: ">"},
    count=1,
  )
  assert len(claimed[0][1]) == 1
  duplicate_fields = claimed[0][1][0][1]
  assert (await real_ready_redis.xpending(
    READY_STREAM,
    READY_GROUP,
  ))["pending"] == 1

  consumed = await worker._consume_strategy_match_ready_once(
    client=real_ready_redis,
    consumer="replacement-worker",
    recover_pending=True,
    pending_min_idle_ms=0,
  )

  assert consumed
  assert await real_ready_redis.xlen(_plan_count_key()) == 1
  await real_ready_redis.xadd(READY_STREAM, duplicate_fields)
  assert await worker._consume_strategy_match_ready_once(
    client=real_ready_redis,
    consumer="replacement-worker",
  )
  assert await real_ready_redis.xlen(_plan_count_key()) == 1
  assert (await real_ready_redis.xpending(
    READY_STREAM,
    READY_GROUP,
  ))["pending"] == 0


@pytest.mark.asyncio
async def test_static_opposing_overlap_never_creates_executable_match_or_card(
  monkeypatch,
):
  now = int(datetime.now(timezone.utc).timestamp())
  frames = _frames(now)
  opposing = MapEntry(
    side="buy",
    lo=4099.0,
    hi=4103.0,
    label_lo=4099,
    label_hi=4103,
    tier="major",
    tags=["demand"],
    score=10.0,
    contains_price=True,
  )
  client, notify = await _configure(
    monkeypatch,
    market_map=_market_map(now, opposing),
    frames=frames,
    now=now,
  )

  await scanner._handle_event(
    f"XAU:M5:{now}",
    client=client,
    detectors=[lambda _ctx: _reaction(now)],
    notify=notify,
  )

  assert await client.get(strategy_match_key("XAU")) is None
  assert await client.get(strategy_matches_key("XAU")) is None
  assert [
    key async for key in client.scan_iter("analysis:setup:*")
  ] == []
  assert await client.xlen(_plan_count_key()) == 0
  # Non-negotiable Telegram requirement: no executable StrategyMatch means
  # no Telegram send at all - not even an ANALYSIS ONLY card (which this
  # test used to expect via the now-removed
  # `notification_results = digest or analysis_only_results` fallback).
  notify.assert_not_awaited()
