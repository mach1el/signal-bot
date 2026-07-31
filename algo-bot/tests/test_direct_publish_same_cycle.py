from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.autotrade import direct_publish_same_cycle as retry
from app.autotrade import worker
from app.autotrade import zone_execution_cutover as cutover
from app.autotrade.strategy_match import StrategyMatch


pytestmark = pytest.mark.no_database


def _range_match() -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id="range-setup",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy="Range Edge Scalp",
    strategy_mode="range_scalp",
    direction="SELL",
    key_level=4114.5,
    entry_low=4113.0,
    entry_high=4116.0,
    current_price=4114.5,
    confluence=2,
    reasons=("range edge rejection",),
    atr=4.0,
    structure_swing=4116.0,
    targets_pips=(300,),
    family="range",
    range_id="range-1",
    range_low=4080.0,
    range_high=4116.0,
    full_take_profit_pips=300,
    confluence_zone_id="zone-1",
    structural_zone_id="zone-1",
    confirmation_bar_ts="1785390300",
    reaction_type="rejection_edge",
  )


@pytest.mark.asyncio
async def test_same_cycle_install_is_identity_without_armed_retry(monkeypatch):
  # ACK/ARMED same-cycle retries are gone. install_same_cycle_publish_retry
  # keeps the cutover wiring contract but must not re-invoke publish.
  match = _range_match()
  watching = worker.PublishResult(
    status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
    plan_id="v7:range-setup",
    reason_code="waiting_retest_entry_zone",
    zone_id="zone-1",
    setup_id="range-setup",
  )
  direct = AsyncMock(return_value=watching)
  monkeypatch.setattr(cutover, "_safe_direct_publish", direct)
  monkeypatch.setattr(worker, "try_publish_executable_signal", direct)
  monkeypatch.setattr(retry, "_INSTALLED", False)

  retry.install_same_cycle_publish_retry()
  result = await cutover._safe_direct_publish(
    object(),
    match,
    symbol="XAU",
  )

  assert result.status == worker.PUBLISH_STATUS_REMAINED_WATCHING
  assert direct.await_count == 1
