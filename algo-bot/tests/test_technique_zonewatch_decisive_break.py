"""Closed-bar decisive break for ZoneWatch invalidation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade.zone_execution_cutover import (
  _closed_bar_decisive_break,
  _decisive_break_from_close,
)
from app.autotrade.zone_watch import ZoneWatch


pytestmark = pytest.mark.no_database


def _record(*, direction: str = "SELL") -> ZoneWatch:
  return ZoneWatch(
    version=1,
    zone_id="z-break",
    symbol="XAU",
    direction=direction,
    low=4100.0,
    high=4102.0,
    width=2.0,
    source_timeframe="M5",
    structural_sources=("FVG",),
    confluence_tags=(),
    grade="A",
    score=1.0,
    freshness=0,
    touch_count=0,
    discovered_at=1,
    last_confirmed_at=1,
    last_touch_at=None,
    invalidation_price=None,
    state="watching_retest",
    market_map_id="",
    structure_signature="sig",
    updated_at=1,
  )


def test_live_quote_past_far_edge_is_not_decisive_without_close():
  # Spot wick above SELL high must not invalidate by itself.
  assert _decisive_break_from_close(_record(), None) is False
  assert _decisive_break_from_close(_record(), 4101.0) is False  # still inside
  assert _decisive_break_from_close(_record(), 4103.0) is True   # closed above


def test_buy_closed_below_low_is_decisive():
  assert _decisive_break_from_close(_record(direction="BUY"), 4099.0) is True
  assert _decisive_break_from_close(_record(direction="BUY"), 4100.5) is False


@pytest.mark.asyncio
async def test_closed_bar_decisive_break_uses_latest_close(monkeypatch):
  frame = pd.DataFrame(
    {"open": [4101.0], "high": [4104.0], "low": [4100.5], "close": [4103.5], "volume": [1.0]},
    index=pd.DatetimeIndex(["2026-08-13T12:00:00Z"]),
  )

  class _Source:
    def __init__(self, client):
      self.client = client

    async def window(self, symbol, tf, n):
      assert symbol == "XAU"
      assert tf == "M5"
      return frame

  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.RedisOHLCSource",
    _Source,
  )
  assert await _closed_bar_decisive_break(AsyncMock(), _record()) is True


@pytest.mark.asyncio
async def test_closed_bar_wick_without_close_beyond_is_not_break(monkeypatch):
  # High wicked above supply but closed back inside → not decisive.
  frame = pd.DataFrame(
    {"open": [4101.0], "high": [4105.0], "low": [4100.5], "close": [4101.5], "volume": [1.0]},
    index=pd.DatetimeIndex(["2026-08-13T12:00:00Z"]),
  )

  class _Source:
    def __init__(self, client):
      pass

    async def window(self, symbol, tf, n):
      return frame

  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.RedisOHLCSource",
    _Source,
  )
  assert await _closed_bar_decisive_break(AsyncMock(), _record()) is False


@pytest.mark.asyncio
async def test_closed_bar_decisive_break_uses_injected_source(monkeypatch):
  frame = pd.DataFrame(
    {"open": [4101.0], "high": [4104.0], "low": [4100.5], "close": [4103.5], "volume": [1.0]},
    index=pd.DatetimeIndex(["2026-08-13T12:00:00Z"]),
  )
  constructed = {"n": 0}

  class _Default:
    def __init__(self, client):
      constructed["n"] += 1

    async def window(self, symbol, tf, n):
      raise AssertionError("should use injected source")

  class _Injected:
    async def window(self, symbol, tf, n):
      assert tf == "M5"
      return frame

  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.RedisOHLCSource",
    _Default,
  )
  assert await _closed_bar_decisive_break(
    AsyncMock(), _record(), source=_Injected(),
  ) is True
  assert constructed["n"] == 0
