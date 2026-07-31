from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest

from app.analysis import market_map_delivery
from app.analysis.market_map import (
  MapEntry,
  MarketMap,
  market_map_from_payload,
  market_map_payload,
)
from app.persistence import redis_state


def _map(lo: float = 4025.0, hi: float = 4028.0) -> MarketMap:
  return MarketMap(
    [MapEntry("buy", lo, hi, 4025, 4028, "zone", ["OB", "fresh"], 9)],
    4041,
    4047,
    4032,
    4062,
    "down",
    "M30",
  )


@pytest.mark.asyncio
async def test_hourly_map_sends_once_per_bucket_and_skips_unchanged_next_hour(
  monkeypatch,
):
  meta = {}
  sent = AsyncMock(return_value=SimpleNamespace(message_id=9001))
  current = {"map": _map()}
  map_calls = []

  async def get_meta(key):
    return meta.get(key)

  async def set_meta(key, value):
    meta[key] = value

  async def get_map(symbol):
    assert symbol == "XAU"
    map_calls.append(symbol)
    return current["map"]

  monkeypatch.setattr(market_map_delivery.settings, "map_session_send", True)
  monkeypatch.setattr(market_map_delivery.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(market_map_delivery.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(market_map_delivery.settings, "map_change_min", 1.0)
  monkeypatch.setattr(market_map_delivery.settings, "map_scan_interval_minutes", 60)
  monkeypatch.setattr(market_map_delivery, "get_meta", get_meta)
  monkeypatch.setattr(market_map_delivery, "set_meta", set_meta)
  monkeypatch.setattr(market_map_delivery, "get_current_market_map", get_map)
  monkeypatch.setattr(market_map_delivery, "send_scanner_with_retry", sent)

  first = datetime(2026, 7, 16, 7, 5, tzinfo=timezone.utc)
  same_hour = datetime(2026, 7, 16, 7, 45, tzinfo=timezone.utc)
  next_hour = datetime(2026, 7, 16, 8, 5, tzinfo=timezone.utc)

  assert await market_map_delivery._market_map_scan_tick(first)
  assert not await market_map_delivery._market_map_scan_tick(same_hour)
  assert not await market_map_delivery._market_map_scan_tick(next_hour)
  assert sent.await_count == 1
  assert map_calls == ["XAU", "XAU"]
  assert meta["last_map_scan"] == "2026-07-16T08:00Z"
  assert meta["last_market_map:XAU"] == market_map_payload(_map())
  client = redis_state.get_client()
  assert market_map_from_payload(await client.get(
    "auto_trade:market_map_display:XAU"
  )) == _map()
  assert 0 < await client.ttl("auto_trade:market_map_display:XAU") <= 7200
  stored = await client.get("auto_trade:market_map_telegram:XAU")
  assert stored is not None


@pytest.mark.asyncio
async def test_hourly_map_deletes_previous_owner_message(monkeypatch):
  previous = _map()
  current = replace(
    previous,
    entries=[replace(previous.entries[0], lo=4026.0, hi=4029.0)],
  )
  meta = {
    "last_map_scan": "2026-07-16T07:00Z",
    "last_market_map:XAU": market_map_payload(previous),
  }
  sent = AsyncMock(return_value=SimpleNamespace(message_id=2002))
  deleted = AsyncMock()
  client = redis_state.get_client()
  await client.set(
    "auto_trade:market_map_telegram:XAU",
    '{"chat_id":42,"message_id":1001,"updated_at":1}',
    ex=60,
  )

  async def get_meta(key):
    return meta.get(key)

  async def set_meta(key, value):
    meta[key] = value

  monkeypatch.setattr(market_map_delivery.settings, "map_session_send", True)
  monkeypatch.setattr(market_map_delivery.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(market_map_delivery.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(market_map_delivery.settings, "map_change_min", 1.0)
  monkeypatch.setattr(market_map_delivery.settings, "map_scan_interval_minutes", 60)
  monkeypatch.setattr(market_map_delivery, "get_meta", get_meta)
  monkeypatch.setattr(market_map_delivery, "set_meta", set_meta)
  monkeypatch.setattr(
    market_map_delivery,
    "get_current_market_map",
    AsyncMock(return_value=current),
  )
  monkeypatch.setattr(market_map_delivery, "send_scanner_with_retry", sent)
  monkeypatch.setattr(market_map_delivery, "delete_scanner_message", deleted)

  fired = await market_map_delivery._market_map_scan_tick(
    datetime(2026, 7, 16, 8, 5, tzinfo=timezone.utc)
  )

  assert fired
  deleted.assert_awaited_once_with(42, 1001)
  sent.assert_awaited_once()
  stored = json.loads(await client.get("auto_trade:market_map_telegram:XAU"))
  assert stored["message_id"] == 2002
  assert stored["chat_id"] == 42


@pytest.mark.asyncio
async def test_hourly_map_resends_when_band_moves_by_threshold(monkeypatch):
  previous = _map()
  current = replace(
    previous,
    entries=[replace(previous.entries[0], lo=4026.0, hi=4029.0)],
  )
  meta = {
    "last_map_scan": "2026-07-16T07:00Z",
    "last_market_map:XAU": market_map_payload(previous),
  }
  sent = AsyncMock(return_value=SimpleNamespace(message_id=8123))

  async def get_meta(key):
    return meta.get(key)

  async def set_meta(key, value):
    meta[key] = value

  monkeypatch.setattr(market_map_delivery.settings, "map_session_send", True)
  monkeypatch.setattr(market_map_delivery.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(market_map_delivery.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(market_map_delivery.settings, "map_change_min", 1.0)
  monkeypatch.setattr(market_map_delivery.settings, "map_scan_interval_minutes", 60)
  monkeypatch.setattr(market_map_delivery, "get_meta", get_meta)
  monkeypatch.setattr(market_map_delivery, "set_meta", set_meta)
  monkeypatch.setattr(
    market_map_delivery,
    "get_current_market_map",
    AsyncMock(return_value=current),
  )
  monkeypatch.setattr(market_map_delivery, "send_scanner_with_retry", sent)
  monkeypatch.setattr(market_map_delivery, "delete_scanner_message", AsyncMock())

  fired = await market_map_delivery._market_map_scan_tick(
    datetime(2026, 7, 16, 8, 5, tzinfo=timezone.utc)
  )

  assert fired
  sent.assert_awaited_once()
  assert sent.await_args.kwargs == {"chat_id": 42}


@pytest.mark.asyncio
async def test_hourly_map_skips_xau_weekend_closure(monkeypatch):
  sent = AsyncMock()
  get_map = AsyncMock(return_value=_map())
  monkeypatch.setattr(market_map_delivery.settings, "map_session_send", True)
  monkeypatch.setattr(market_map_delivery.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(market_map_delivery.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(
    market_map_delivery,
    "get_meta",
    AsyncMock(return_value=None),
  )
  monkeypatch.setattr(market_map_delivery, "set_meta", AsyncMock())
  monkeypatch.setattr(market_map_delivery, "get_current_market_map", get_map)
  monkeypatch.setattr(market_map_delivery, "send_scanner_with_retry", sent)

  # Friday 21:00 UTC, all Saturday, and Sunday before 22:00 UTC.
  for stamp in (
    datetime(2026, 7, 17, 21, 5, tzinfo=timezone.utc),
    datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc),
    datetime(2026, 7, 19, 21, 0, tzinfo=timezone.utc),
  ):
    assert not await market_map_delivery._market_map_scan_tick(stamp)

  sent.assert_not_awaited()
  get_map.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_demand_map_uses_scanner_bot(monkeypatch):
  sent = AsyncMock(return_value=SimpleNamespace(message_id=7001))
  deleted = AsyncMock()
  monkeypatch.setattr(market_map_delivery.settings, "telegram_owner_id", 42)
  monkeypatch.setattr(
    market_map_delivery,
    "get_current_market_map",
    AsyncMock(return_value=_map()),
  )
  monkeypatch.setattr(market_map_delivery, "send_scanner_with_retry", sent)
  monkeypatch.setattr(market_map_delivery, "delete_scanner_message", deleted)

  assert await market_map_delivery.send_current_market_map("XAU")
  sent.assert_awaited_once()
  assert "XAU Market Map" in sent.await_args.args[0]
  assert sent.await_args.kwargs == {"chat_id": 42}
  deleted.assert_not_awaited()
  client = redis_state.get_client()
  stored = json.loads(await client.get("auto_trade:market_map_telegram:XAU"))
  assert stored["message_id"] == 7001


def test_scan_bucket_key_uses_configured_interval():
  assert market_map_delivery._scan_bucket_key(
    datetime(2026, 7, 16, 7, 59, tzinfo=timezone.utc),
    60,
  ) == "2026-07-16T07:00Z"
  assert market_map_delivery._scan_bucket_key(
    datetime(2026, 7, 16, 7, 44, tzinfo=timezone.utc),
    30,
  ) == "2026-07-16T07:30Z"
