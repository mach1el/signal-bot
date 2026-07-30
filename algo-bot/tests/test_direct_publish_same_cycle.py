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
async def test_fresh_b_grade_trigger_gets_one_bounded_second_pass(monkeypatch):
  match = _range_match()
  first = worker.PublishResult(
    status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
    plan_id="v7:range-setup",
    reason_code="m1_trigger_wait",
    zone_id="zone-1",
    setup_id="range-setup",
  )
  second = worker.PublishResult(
    status=worker.PUBLISH_STATUS_PUBLISHED,
    plan_id="v7:range-setup",
    reason_code="candidate_published",
    zone_id="zone-1",
    setup_id="range-setup",
  )
  direct = AsyncMock(side_effect=[first, second])
  monkeypatch.setattr(cutover, "_safe_direct_publish", direct)
  monkeypatch.setattr(worker, "try_publish_executable_signal", direct)
  monkeypatch.setattr(retry, "_INSTALLED", False)
  monkeypatch.setattr(
    worker,
    "resolve_existing_v7_state",
    AsyncMock(return_value=worker.ExistingV7State(
      plan_id="v7:range-setup",
      setup_state="armed_waiting_trigger",
      plan_state=None,
      plan_exists=False,
      already_published=False,
      already_terminal=False,
      owner_matches=False,
    )),
  )

  retry.install_same_cycle_publish_retry()
  result = await cutover._safe_direct_publish(
    object(),
    match,
    symbol="XAU",
  )

  assert result.status == worker.PUBLISH_STATUS_PUBLISHED
  assert direct.await_count == 2
