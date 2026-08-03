"""refactor/p0-direct-zone-signal-execution: per-timeframe lookback contract.

Section 3 of the refactor mandate: H1/M15/M5 each need their own configurable
closed-bar lookback (deep enough to find durable structural evidence) and M1
stays a shallow trigger/timing-only window - not a single flat count applied
to every timeframe.
"""

from __future__ import annotations
from app.core.config import settings

import pytest

from app.analysis import ohlc_source, scanner
from app.autotrade import worker


pytestmark = pytest.mark.no_database


class _RecordingSource:
  def __init__(self):
    self.calls: dict[str, int] = {}

  async def window(self, symbol, tf, n):
    self.calls[tf] = n
    import pandas as pd
    return pd.DataFrame(
      {"open": [], "high": [], "low": [], "close": [], "volume": []},
    )


def test_default_lookback_settings_fall_inside_the_documented_xau_ranges():
  assert 300 <= settings.xau_lookback_h1_bars <= 500
  assert 500 <= settings.xau_lookback_m15_bars <= 800
  assert 800 <= settings.xau_lookback_m5_bars <= 1200
  assert 100 <= settings.xau_lookback_m1_bars <= 200


def test_window_for_timeframe_resolves_each_timeframe_independently(
  monkeypatch,
):
  monkeypatch.setattr(settings, "xau_lookback_h1_bars", 400)
  monkeypatch.setattr(settings, "xau_lookback_m15_bars", 650)
  monkeypatch.setattr(settings, "xau_lookback_m5_bars", 1000)
  monkeypatch.setattr(settings, "xau_lookback_m1_bars", 150)

  assert ohlc_source.window_for_timeframe("H1") == 400
  assert ohlc_source.window_for_timeframe("M15") == 650
  assert ohlc_source.window_for_timeframe("M5") == 1000
  assert ohlc_source.window_for_timeframe("M1") == 150
  assert ohlc_source.window_for_timeframe("h1") == 400  # case-insensitive


def test_window_for_timeframe_clamps_a_misconfigured_low_value(monkeypatch):
  monkeypatch.setattr(settings, "xau_lookback_m1_bars", 1)
  assert ohlc_source.window_for_timeframe("M1") == 50


@pytest.mark.asyncio
async def test_scanner_load_frames_requests_the_deepest_window_for_m5(
  monkeypatch,
):
  monkeypatch.setattr(settings, "xau_lookback_h1_bars", 400)
  monkeypatch.setattr(settings, "xau_lookback_m15_bars", 650)
  monkeypatch.setattr(settings, "xau_lookback_m5_bars", 1000)
  source = _RecordingSource()

  await scanner._load_frames(source, "XAU", "M5", ["H1", "M15"])

  assert source.calls["M5"] == 1000
  assert source.calls["H1"] == 400
  assert source.calls["M15"] == 650


@pytest.mark.asyncio
async def test_worker_load_frames_gives_m1_a_shallow_trigger_only_window(
  monkeypatch,
):
  monkeypatch.setattr(settings, "xau_lookback_h1_bars", 400)
  monkeypatch.setattr(settings, "xau_lookback_m15_bars", 650)
  monkeypatch.setattr(settings, "xau_lookback_m5_bars", 1000)
  monkeypatch.setattr(settings, "xau_lookback_m1_bars", 150)
  source = _RecordingSource()

  await worker._load_frames(source, "XAU")

  assert source.calls[worker.EXECUTION_TIMEFRAME] == 150
  assert source.calls["M5"] == 1000
  assert source.calls["H1"] == 400
  assert source.calls["M1"] < source.calls["M5"], (
    "M1 must stay a shallow trigger/timing window, never the deepest fetch"
  )
