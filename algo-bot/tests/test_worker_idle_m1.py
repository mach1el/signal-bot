"""Idle M1 worker ticks skip leftover TradePlan routing when there is no match."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.autotrade import worker
from app.autotrade.gate import AutoScalpDecision
from app.autotrade.trend import RegimeInfo, TrendDecision
from app.persistence import redis_state
from tests.configuration.canonical_fixtures import install_runtime_overrides


pytestmark = pytest.mark.no_database


def _frame() -> pd.DataFrame:
  index = pd.date_range("2026-07-20", periods=20, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open": [4016.8] * 20,
    "high": [4017.4] * 20,
    "low": [4016.2] * 20,
    "close": [4017.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)


@pytest.mark.asyncio
async def test_idle_m1_skips_v8_publish_and_still_writes_last_gate(monkeypatch):
  client = redis_state.get_client()
  now = int(datetime.now(timezone.utc).timestamp())
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_symbols": "XAU"})
  publish = AsyncMock(return_value=None)
  monkeypatch.setattr(worker, "_publish_trade_plan_v8", publish)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))
  source = AsyncMock()
  source.window = AsyncMock(return_value=_frame())
  monkeypatch.setattr(
    worker,
    "_load_spot",
    AsyncMock(return_value=worker.AutoTradeSpot(4017.2, now, True)),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_auto_scalp_gate",
    lambda *args, **kwargs: AutoScalpDecision("waiting_for_box"),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_market_map_strategy",
    lambda *args, **kwargs: worker.MarketMapStrategyDecision("disabled"),
  )
  monkeypatch.setattr(
    worker,
    "evaluate_trend_gate",
    lambda *args, **kwargs: TrendDecision("no_setup"),
  )
  monkeypatch.setattr(
    worker,
    "classify_regime",
    lambda *args, **kwargs: RegimeInfo(
      "chop", None, 0, 1.0, False, None, ("idle",),
    ),
  )

  result = await worker._handle_event(
    f"XAU:M1:{now}", source=source, client=client,
  )

  assert result is not None
  publish.assert_not_awaited()
  status = await client.get("auto_trade:last_gate:XAU")
  assert status is not None
