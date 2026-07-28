"""Proves worker.py actually publishes TradePlan V7 to execution:trade_plans.

Exercises app.autotrade.worker._publish_trade_plan_v7 directly against a real
Redis client (same fakeredis-backed client the rest of the suite uses) - not
a mock of the publish call - so a regression here means the live runtime
stopped publishing, not just that a function was called with the right args.

Since P3 (the M1 candlestick trigger), a CONFIRMED setup no longer publishes
on the first call - it arms and waits. Every test here calls
_publish_trade_plan_v7 twice: once to arm, once with a qualifying M1 bar in
`frames["M1"]` to actually trigger the publish.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from app.analysis.market_map import MapEntry, MarketMap
from app.autotrade import worker
from app.autotrade.setup_lifecycle import (
  ARMED_WAITING_TRIGGER,
  CONFIRMED,
  INVALIDATED,
  PLAN_PUBLISHED,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan_stream import read_plan_state, read_trade_plan
from app.persistence import redis_state


def _match(**overrides) -> StrategyMatch:
  # expires_at must be in the future relative to real wall-clock time (not
  # a fixed historical epoch) since P4's EXPIRED sweep in
  # _publish_trade_plan_v7 compares it against datetime.now(timezone.utc).
  base = dict(
    version=STRATEGY_MATCH_VERSION,
    match_id="match-v7-1",
    symbol="XAU",
    source_tf="M15",
    event_ts="1719999600",
    issued_at=1719999600,
    expires_at=int(time.time()) + 3600,
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


def _m1_trigger_bar(
  *, entry_low: float = 4088.10, entry_high: float = 4090.00,
) -> pd.DataFrame:
  # A wick_rejection bar for a BUY setup: wicks below the zone then closes
  # back above it, lower-wick fraction well past the default 0.5 threshold.
  index = pd.date_range("2026-07-22 14:45", periods=1, freq="1min", tz="UTC")
  return pd.DataFrame({
    "open": [entry_high - 1.0],
    "high": [entry_high + 0.5],
    "low": [entry_low - 2.0],
    "close": [entry_high + 0.3],
    "volume": [500.0],
  }, index=index)


def _market_map(*entries: MapEntry) -> MarketMap:
  return MarketMap(
    entries=list(entries),
    price=4089.0,
    eq=None,
    box_low=None,
    box_high=None,
    bias="up",
    bias_tf="H1",
    actionable_entries=list(entries),
  )


@pytest.mark.asyncio
async def test_confirmed_setup_arms_and_publishes_only_at_m1_trigger():
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(price=4089.0, ts=1719999600, fresh=True, bid=4088.9, ask=4089.1)

  # First call: CONFIRMED -> ARMED_WAITING_TRIGGER, no plan yet.
  armed_plan_id = await worker._publish_trade_plan_v7(client, "XAU", spot, match)
  assert armed_plan_id is None
  armed_record = await load_setup(client, "match-v7-1")
  assert armed_record.state == ARMED_WAITING_TRIGGER

  # Still armed, no M1 data at all: stays armed, still no plan.
  still_waiting = await worker._publish_trade_plan_v7(client, "XAU", spot, match)
  assert still_waiting is None
  assert (await load_setup(client, "match-v7-1")).state == ARMED_WAITING_TRIGGER

  # Qualifying M1 candle: publishes exactly now, stop anchored to the wick.
  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match, frames={"M1": _m1_trigger_bar()},
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.thesis_id == "thesis-v7-1"
  assert plan.setup_id == "match-v7-1"
  assert plan.analysis.direction == "BUY"
  assert plan.analysis.bias == "up"
  assert plan.source_structure.kind == "demand"
  assert plan.stop.source == "m1_trigger_wick"
  # The trigger bar's low (4086.10) drives the stop, not the raw zone edge.
  assert float(plan.stop.price) < 4088.10
  assert await read_plan_state(client, plan_id) == "published"
  record = await load_setup(client, "match-v7-1")
  assert record.state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_final_v7_gate_rejects_same_opposing_major_geometry_as_scanner():
  client = redis_state.get_client()
  match = _match(
    match_id="match-v7-opposing-major",
    thesis_id="thesis-v7-opposing-major",
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0,
    ts=1719999600,
    fresh=True,
    bid=4088.9,
    ask=4089.1,
  )
  market_map = _market_map(MapEntry(
    "sell",
    4089.2,
    4095.0,
    4089,
    4095,
    "major",
    ["supply"],
    13.0,
  ))

  await worker._publish_trade_plan_v7(
    client,
    "XAU",
    spot,
    match,
    market_map=market_map,
  )
  plan_id = await worker._publish_trade_plan_v7(
    client,
    "XAU",
    spot,
    match,
    frames={"M1": _m1_trigger_bar()},
    market_map=market_map,
  )

  assert plan_id is None
  assert (await load_setup(client, match.match_id)).state == INVALIDATED
  assert await read_trade_plan(client, worker._v7_plan_id(match)) is None
  gate = await client.hgetall(
    "auto_trade:gate_reject:XAU:v7_opposing_major_no_room"
  )
  assert int(gate["count"]) == 1


@pytest.mark.asyncio
async def test_final_v7_gate_caps_target_ladder_before_opposing_structure():
  client = redis_state.get_client()
  match = _match(
    match_id="match-v7-target-cap",
    thesis_id="thesis-v7-target-cap",
    structure_swing=4087.5,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0,
    ts=1719999600,
    fresh=True,
    bid=4088.9,
    ask=4089.1,
  )
  market_map = _market_map(MapEntry(
    "sell",
    4096.0,
    4098.0,
    4096,
    4098,
    "zone",
    ["supply"],
    10.0,
  ))

  await worker._publish_trade_plan_v7(
    client,
    "XAU",
    spot,
    match,
    market_map=market_map,
  )
  plan_id = await worker._publish_trade_plan_v7(
    client,
    "XAU",
    spot,
    match,
    frames={"M1": _m1_trigger_bar()},
    market_map=market_map,
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert len(plan.targets) == 1


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

  await worker._publish_trade_plan_v7(client, "XAU", spot, match)
  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match, frames={"M1": _m1_trigger_bar()},
  )

  assert plan_id is not None
  record = await load_setup(client, "match-v7-2")
  assert record.state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_second_setup_for_same_thesis_is_rejected_not_duplicated():
  client = redis_state.get_client()
  spot = worker.AutoTradeSpot(price=4089.0, ts=1719999600, fresh=True, bid=4088.9, ask=4089.1)

  first_match = _match(match_id="match-v7-3a", thesis_id="thesis-v7-shared")
  await _confirm_setup(client, first_match)
  await worker._publish_trade_plan_v7(client, "XAU", spot, first_match)
  first_plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, first_match, frames={"M1": _m1_trigger_bar()},
  )
  assert first_plan_id is not None

  second_match = _match(match_id="match-v7-3b", thesis_id="thesis-v7-shared")
  await _confirm_setup(client, second_match)
  await worker._publish_trade_plan_v7(client, "XAU", spot, second_match)
  second_plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, second_match, frames={"M1": _m1_trigger_bar()},
  )

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


@pytest.mark.asyncio
async def test_non_qualifying_m1_bar_does_not_publish():
  client = redis_state.get_client()
  match = _match(match_id="match-v7-5", thesis_id="thesis-v7-5")
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(price=4089.0, ts=1719999600, fresh=True, bid=4088.9, ask=4089.1)
  await worker._publish_trade_plan_v7(client, "XAU", spot, match)

  # A tiny, symmetric doji sitting inside the zone with no directional wick,
  # body, or close - qualifies for none of the six patterns.
  index = pd.date_range("2026-07-22 14:45", periods=1, freq="1min", tz="UTC")
  flat_bar = pd.DataFrame({
    "open": [4089.0], "high": [4089.05], "low": [4088.95], "close": [4089.0],
    "volume": [100.0],
  }, index=index)

  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match, frames={"M1": flat_bar},
  )

  assert plan_id is None
  record = await load_setup(client, "match-v7-5")
  assert record.state == ARMED_WAITING_TRIGGER
