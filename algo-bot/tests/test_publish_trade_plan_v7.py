"""Proves worker.py actually publishes TradePlan V7 to execution:trade_plans.

Exercises app.autotrade.worker._publish_trade_plan_v7 directly against a real
Redis client (same fakeredis-backed client the rest of the suite uses) - not
a mock of the publish call - so a regression here means the live runtime
stopped publishing, not just that a function was called with the right args.
"""

from __future__ import annotations

import pytest

from app.autotrade import worker
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  PLAN_PUBLISHED,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan_stream import read_plan_state, read_trade_plan
from app.persistence import redis_state


def _match(**overrides) -> StrategyMatch:
  base = dict(
    version=STRATEGY_MATCH_VERSION,
    match_id="match-v7-1",
    symbol="XAU",
    source_tf="M15",
    event_ts="1719999600",
    issued_at=1719999600,
    expires_at=1719999600 + 3600,
    strategy="Trend Pullback",
    strategy_mode="with_trend",
    direction="BUY",
    key_level=4089.0,
    entry_low=4088.10,
    entry_high=4090.00,
    current_price=4089.0,
    confluence=3,
    reasons=("htf_uptrend",),
    atr=1.8,
    structure_swing=4081.80,
    targets_pips=(60, 140, 250),
    tier="A",
    family="trend_pullback",
    structural_zone_id="zone-xau-4088-4090",
    structural_zone_low=4088.10,
    structural_zone_high=4090.00,
    structural_kind="demand",
    structural_timeframe="H1",
    htf_bias="up",
    regime_kind="trend",
    thesis_id="thesis-v7-1",
  )
  base.update(overrides)
  return StrategyMatch(**base)


async def _confirm_setup(client, match: StrategyMatch) -> None:
  record, _created = await create_setup(
    client,
    setup_id=match.match_id,
    thesis_id=match.thesis_id,
    symbol=match.symbol,
    source_structure_id=match.structural_zone_id,
    formation_timeframe=match.structural_timeframe,
    expires_at=match.expires_at,
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    record, _changed = await transition_setup(client, match.match_id, state)


@pytest.mark.asyncio
async def test_publishes_plan_unconditionally():
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(price=4089.0, ts=1719999600, fresh=True, bid=4088.9, ask=4089.1)

  plan_id = await worker._publish_trade_plan_v7(client, "XAU", spot, match)

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.thesis_id == "thesis-v7-1"
  assert plan.setup_id == "match-v7-1"
  assert plan.analysis.direction == "BUY"
  assert plan.analysis.bias == "up"
  assert plan.source_structure.kind == "demand"
  assert await read_plan_state(client, plan_id) == "published"
  record = await load_setup(client, "match-v7-1")
  assert record.state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_publish_no_longer_reads_contract_mode_at_all(monkeypatch):
  # _publish_trade_plan_v7 must not gate on AUTO_TRADE_CONTRACT_MODE - V7 is
  # the sole autonomous path, unconditionally, not a mode. Force the
  # setting to a value that would have disabled V7 under the old gate (and
  # is now rejected by Settings validation, but this function doesn't
  # validate - it just must not branch on it) to prove the gate is gone.
  monkeypatch.setattr(worker.settings, "auto_trade_contract_mode", "legacy_v6")
  client = redis_state.get_client()
  match = _match(match_id="match-v7-2", thesis_id="thesis-v7-2")
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(price=4089.0, ts=1719999600, fresh=True, bid=4088.9, ask=4089.1)

  plan_id = await worker._publish_trade_plan_v7(client, "XAU", spot, match)

  assert plan_id is not None
  record = await load_setup(client, "match-v7-2")
  assert record.state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_second_setup_for_same_thesis_is_rejected_not_duplicated():
  client = redis_state.get_client()
  spot = worker.AutoTradeSpot(price=4089.0, ts=1719999600, fresh=True, bid=4088.9, ask=4089.1)

  first_match = _match(match_id="match-v7-3a", thesis_id="thesis-v7-shared")
  await _confirm_setup(client, first_match)
  first_plan_id = await worker._publish_trade_plan_v7(client, "XAU", spot, first_match)
  assert first_plan_id is not None

  second_match = _match(match_id="match-v7-3b", thesis_id="thesis-v7-shared")
  await _confirm_setup(client, second_match)
  second_plan_id = await worker._publish_trade_plan_v7(client, "XAU", spot, second_match)

  assert second_plan_id is None
  # Only one plan_id was ever minted for this thesis.
  first_plan = await read_trade_plan(client, first_plan_id)
  assert first_plan is not None
  second_plan = await read_trade_plan(client, worker._v7_plan_id(second_match))
  assert second_plan is None


@pytest.mark.asyncio
async def test_setup_not_confirmed_is_rejected():
  # e.g. a map_strategy.py-sourced match, which never runs through
  # scanner.py's setup lifecycle wiring at all.
  client = redis_state.get_client()
  match = _match(match_id="match-v7-4", thesis_id="thesis-v7-4")
  spot = worker.AutoTradeSpot(price=4089.0, ts=1719999600, fresh=True, bid=4088.9, ask=4089.1)

  plan_id = await worker._publish_trade_plan_v7(client, "XAU", spot, match)

  assert plan_id is None
  assert await load_setup(client, "match-v7-4") is None
