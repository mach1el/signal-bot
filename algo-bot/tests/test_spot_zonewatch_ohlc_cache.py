"""Spot-loop ZoneWatch shares one OHLC cache per evaluation."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.autotrade import zone_execution_cutover as cutover


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_spot_eval_begins_and_clears_ohlc_cache(monkeypatch):
  events: list[str] = []

  class _Source:
    def __init__(self, client):
      events.append("init")
      self._bar_cache = None

    def begin_closed_bar_cache(self):
      events.append("begin")
      self._bar_cache = {}

    def end_closed_bar_cache(self):
      events.append("end")
      self._bar_cache = None

  async def _eval(client, *, symbol, event_ts, source=None):
    events.append("eval")
    assert symbol == "XAU"
    assert event_ts == "1"
    assert source is not None
    assert source._bar_cache == {}
    return None

  monkeypatch.setattr(cutover, "RedisOHLCSource", _Source)
  monkeypatch.setattr(cutover, "evaluate_active_zone_watches", _eval)

  await cutover.evaluate_spot_zone_watches(
    AsyncMock(), symbol="XAU", event_ts="1",
  )

  assert events == ["init", "begin", "eval", "end"]
