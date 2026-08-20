from dataclasses import replace
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
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
async def test_hourly_map_sends_once_per_bucket_and_again_next_hour_even_if_unchanged(
  monkeypatch,
):
  """Owner-reported 2026-08-20: an hourly digest that only posts when the
  map materially changed could go silent for hours on a quiet market - the
  bucket got marked done either way, so the next check was another full
  interval away. This is the same map, unchanged, on three consecutive
  buckets - each new bucket must still send.
  """
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

  install_runtime_overrides(monkeypatch, legacy_overrides={"map_session_send": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"map_change_min": 1.0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"map_scan_interval_minutes": 60})
  monkeypatch.setattr(market_map_delivery, "get_meta", get_meta)
  monkeypatch.setattr(market_map_delivery, "set_meta", set_meta)
  monkeypatch.setattr(market_map_delivery, "get_current_market_map", get_map)
  monkeypatch.setattr(market_map_delivery, "send_scanner_with_retry", sent)

  first = datetime(2026, 7, 16, 7, 5, tzinfo=timezone.utc)
  same_hour = datetime(2026, 7, 16, 7, 45, tzinfo=timezone.utc)
  next_hour = datetime(2026, 7, 16, 8, 5, tzinfo=timezone.utc)

  assert await market_map_delivery._market_map_scan_tick(first)
  assert not await market_map_delivery._market_map_scan_tick(same_hour)
  assert await market_map_delivery._market_map_scan_tick(next_hour)
  assert sent.await_count == 2
  assert map_calls == ["XAU", "XAU"]
  assert meta["last_map_scan"] == "2026-07-16T08:00Z"
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
  meta = {"last_map_scan": "2026-07-16T07:00Z"}
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

  install_runtime_overrides(monkeypatch, legacy_overrides={"map_session_send": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"map_change_min": 1.0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"map_scan_interval_minutes": 60})
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
async def test_hourly_map_skips_xau_weekend_closure(monkeypatch):
  sent = AsyncMock()
  get_map = AsyncMock(return_value=_map())
  install_runtime_overrides(monkeypatch, legacy_overrides={"map_session_send": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_symbols": "XAU"})
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 42})
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
