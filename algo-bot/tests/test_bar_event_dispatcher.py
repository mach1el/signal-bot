from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.analysis import bar_event_dispatcher as dispatcher


def _enable_handlers(monkeypatch) -> None:
  monkeypatch.setattr(
    dispatcher,
    "runtime_config",
    SimpleNamespace(
      runtime=SimpleNamespace(
        scanner=SimpleNamespace(enabled=True),
        auto_trade=SimpleNamespace(enabled=True),
      )
    ),
  )


pytestmark = pytest.mark.no_database


def test_parse_closed_bar():
  assert dispatcher.parse_closed_bar(b"XAU:M1:1700000000") == (
    "XAU", "M1", "1700000000",
  )
  assert dispatcher.parse_closed_bar("bad") is None


@pytest.mark.asyncio
async def test_dispatch_runs_isolated_handlers(monkeypatch):
  _enable_handlers(monkeypatch)
  scanner = AsyncMock()
  worker = AsyncMock()
  zone = AsyncMock()
  hfs = AsyncMock()
  monkeypatch.setattr(
    "app.analysis.scanner._handle_event", scanner, raising=False,
  )
  monkeypatch.setattr(
    "app.autotrade.worker._handle_event", worker, raising=False,
  )
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    zone,
    raising=False,
  )
  monkeypatch.setattr(
    "app.scalping.runtime.handle_closed_bar", hfs, raising=False,
  )

  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=SimpleNamespace(),
    source=SimpleNamespace(),
  )

  assert "scanner" in ran
  assert "worker" in ran
  assert "zone_watch" in ran
  assert "hfs" in ran
  scanner.assert_awaited_once()
  worker.assert_awaited_once()
  zone.assert_awaited_once()
  hfs.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_keeps_later_handlers_if_scanner_raises(monkeypatch):
  _enable_handlers(monkeypatch)
  async def boom(*args, **kwargs):
    raise RuntimeError("scanner down")

  worker = AsyncMock()
  zone = AsyncMock()
  hfs = AsyncMock()
  monkeypatch.setattr("app.analysis.scanner._handle_event", boom, raising=False)
  monkeypatch.setattr(
    "app.autotrade.worker._handle_event", worker, raising=False,
  )
  monkeypatch.setattr(
    "app.autotrade.zone_execution_cutover.evaluate_active_zone_watches",
    zone,
    raising=False,
  )
  monkeypatch.setattr(
    "app.scalping.runtime.handle_closed_bar", hfs, raising=False,
  )

  ran = await dispatcher.dispatch_closed_bar(
    "XAU:M1:1700000000",
    client=SimpleNamespace(),
    source=SimpleNamespace(),
  )

  assert "scanner" not in ran
  assert ran == ["worker", "zone_watch", "hfs"]
  worker.assert_awaited_once()
  hfs.assert_awaited_once()
