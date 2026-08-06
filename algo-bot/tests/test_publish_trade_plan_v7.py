"""Proves worker.py actually publishes TradePlan V7 to execution:trade_plans.

Exercises app.autotrade.worker._publish_trade_plan_v7 directly against a real
Redis client (same fakeredis-backed client the rest of the suite uses) - not
a mock of the publish call - so a regression here means the live runtime
stopped publishing, not just that a function was called with the right args.

Formed setups publish as soon as the side-aware executable quote is inside or
inside the configured entry contract. Outside setups persist WAITING_RETEST.
M1 is optional timing evidence: a fresh trigger anchors the stop wick, while
its absence never blocks a setup whose executable quote is inside the zone.
"""

from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

from dataclasses import replace
import time
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.analysis.market_map import MapEntry, MarketMap
from app.analysis.execution_eligibility import (
  EXECUTION_ELIGIBILITY_VERSION,
  STATIC_ELIGIBLE,
  ExecutionEligibility,
)
from app.autotrade import worker
from app.autotrade.arbitration import ExecutionIntent
from app.autotrade.execution_confirmation import (
  PUBLISHED,
  WAITING_RETEST,
  load_execution_confirmation,
)
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  EXPIRED,
  INVALIDATED,
  PLAN_PUBLISHED,
  create_setup,
  load_setup,
  transition_setup,
)
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan_stream import (
  plan_key,
  plan_state_key,
  read_plan_state,
  read_trade_plan,
)
from app.autotrade.trend import RegimeInfo
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.fixture(autouse=True)
def _no_news_by_default(monkeypatch):
  # V7 hard-gates news via event_in_window; without a live DB the lookup
  # must fail-open (no event) so tests can exercise every other gate.
  monkeypatch.setattr(
    worker, "event_in_window", AsyncMock(return_value=None),
  )


def _eligibility(
  *,
  direction: str,
  entry_low: float,
  entry_high: float,
  planned_entry: float,
) -> ExecutionEligibility:
  return ExecutionEligibility(
    version=EXECUTION_ELIGIBILITY_VERSION,
    allowed=True,
    state=STATIC_ELIGIBLE,
    reason_code="static_eligible",
    message="scanner static eligibility passed",
    hard_block=False,
    direction=direction,
    entry_low=entry_low,
    entry_high=entry_high,
    planned_entry_price=planned_entry,
    calculated_at=int(time.time()),
  )


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
    strategy="Momentum Ride",
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
    family="momentum_continuation",
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
  base.setdefault(
    "execution_eligibility",
    _eligibility(
      direction=str(base["direction"]),
      entry_low=float(base["entry_low"]),
      entry_high=float(base["entry_high"]),
      planned_entry=float(base["current_price"]),
    ),
  )
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


async def _publish_nonreaction_after_m1(
  client,
  spot,
  match,
  **kwargs,
):
  # Zone presence alone authorizes publication; M1 is optional stop evidence.
  return await worker._publish_trade_plan_v7(
    client,
    "XAU",
    spot,
    match,
    frames={"M1": _m1_trigger_bar()},
    **kwargs,
  )


def _m1_trigger_bar(
  *, entry_low: float = 4088.10, entry_high: float = 4090.00,
) -> pd.DataFrame:
  # A wick_rejection bar for a BUY setup: wicks below the zone then closes
  # back above it, lower-wick fraction well past the default 0.5 threshold.
  # Timestamp must be after the match confirmation boundary so the optional
  # M1 window accepts it as fresh in-zone evidence.
  index = pd.date_range(
    pd.Timestamp(int(time.time()), unit="s", tz="UTC") - pd.Timedelta(seconds=30),
    periods=1,
    freq="1min",
    tz="UTC",
  )
  return pd.DataFrame({
    "open": [entry_high - 1.0],
    "high": [entry_high + 0.5],
    "low": [entry_low - 2.0],
    "close": [entry_high + 0.3],
    "volume": [500.0],
  }, index=index)


def _reaction_match(**overrides) -> StrategyMatch:
  now = int(time.time())
  base = dict(
    match_id="reaction-v7-1",
    source_tf="M5",
    event_ts=str(now - 60),
    issued_at=now - 60,
    expires_at=now + 900,
    strategy="Key Level Reaction",
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4040.23,
    entry_low=4038.36,
    entry_high=4042.09,
    current_price=4038.41,
    reasons=("strong reclaim",),
    atr=4.0,
    structure_swing=4045.0,
    targets_pips=(100, 200),
    family="key_level",
    structural_source="key_level",
    structural_zone_id="key-level-4040",
    structural_zone_low=4038.36,
    structural_zone_high=4042.09,
    touch_bar_ts=str(now - 120),
    confirmation_bar_ts=str(now - 60),
    reaction_type="strong_reclaim",
    structural_kind="resistance",
    structural_timeframe="M5",
    htf_bias="down",
    regime_kind="trend",
    thesis_id="thesis-key-level-4040",
  )
  base.update(overrides)
  return _match(**base)


def _sell_retest_bar(timestamp: int) -> pd.DataFrame:
  return pd.DataFrame(
    {
      "open": [4045.0],
      "high": [4046.5],
      "low": [4043.5],
      "close": [4043.7],
      "volume": [500.0],
    },
    index=pd.DatetimeIndex(
      [pd.Timestamp(timestamp, unit="s", tz="UTC")],
    ),
  )


def _buy_retest_bar(timestamp: int) -> pd.DataFrame:
  return pd.DataFrame(
    {
      "open": [4039.0],
      "high": [4040.5],
      "low": [4037.5],
      "close": [4040.3],
      "volume": [500.0],
    },
    index=pd.DatetimeIndex(
      [pd.Timestamp(timestamp, unit="s", tz="UTC")],
    ),
  )


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


def _intent_for_match(match: StrategyMatch) -> ExecutionIntent:
  return ExecutionIntent(
    intent_id=match.match_id,
    source="scanner_strategy_match",
    strategy=match.strategy,
    direction=match.direction,
    confluence=match.confluence,
    tier=match.tier,
    freshness=60.0,
    distance_pips=0.0,
    symbol=match.symbol,
    timeframe=match.source_tf,
    family=match.family or "",
    entry_low=match.entry_low,
    entry_high=match.entry_high,
    structural_id=match.structural_zone_id or "",
    match_id=match.match_id,
    reaction_id=match.reaction_id,
    thesis_id=match.thesis_id,
    current_price=match.current_price,
    targets_pips=match.targets_pips,
  )


@pytest.mark.asyncio
async def test_nonreaction_setup_publishes_with_optional_m1_stop_anchor():
  client = redis_state.get_client()
  match = _match()
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1)

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
  # The trigger bar's low (entry_low - 2.0) drives the stop, not the raw zone edge.
  assert float(plan.stop.price) < 4088.10
  assert await read_plan_state(client, plan_id) == "published"
  record = await load_setup(client, "match-v7-1")
  assert record.state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_nonreaction_setup_publishes_in_zone_without_m1():
  client = redis_state.get_client()
  match = _match(
    match_id="near-no-m1",
    thesis_id="near-no-m1-thesis",
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.5,
    ts=int(time.time()),
    fresh=True,
    bid=4089.4,
    ask=4089.5,
  )

  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.stop.source == "m5_structure"
  assert plan.provenance.confirmation_source == "m5_authoritative"
  assert (await load_setup(client, match.match_id)).state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_m5_authoritative_sell_inside_zone_publishes_same_cycle_once():
  client = redis_state.get_client()
  match = _reaction_match()
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4038.51,
    ts=int(time.time()),
    fresh=True,
    bid=4038.41,
    ask=4038.61,
  )

  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  )

  assert plan_id is not None
  assert (await load_setup(client, match.match_id)).state == PLAN_PUBLISHED
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.entry.price_side == "bid"
  assert plan.provenance.confirmation_source == "m5_authoritative"
  assert plan.provenance.zone_episode_id is not None
  assert plan.stop.source == "m5_structure"
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == PUBLISHED
  assert await client.xlen(leaf(runtime_config, "auto_trade_trade_plan_stream")) == 1

  assert await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  ) == plan_id
  assert await client.xlen(leaf(runtime_config, "auto_trade_trade_plan_stream")) == 1


@pytest.mark.asyncio
async def test_publication_result_reconciles_durable_v7_plan_when_local_is_none():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="d870ddc73b268fcb2feaa14a891c2108",
    thesis_id="thesis-d870ddc73b268fcb2feaa14a891c2108",
    direction="SELL",
    entry_low=4045.36521,
    entry_high=4047.14248,
    current_price=4045.56,
  )
  plan_id = worker._v7_plan_id(match)
  await client.set(
    plan_key(plan_id),
    '{"version":7,"plan_id":"' + plan_id + '"}',
    ex=3600,
  )
  await client.set(plan_state_key(plan_id), "published", ex=3600)

  result = await worker._strategy_publication_result(client, match, None)

  assert result.status == "published"
  assert result.candidate_id == plan_id


@pytest.mark.asyncio
async def test_rejected_plan_state_wins_over_retained_plan_payload():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="rejected-v7-replay",
    thesis_id="rejected-v7-thesis",
  )
  plan_id = worker._v7_plan_id(match)
  await client.set(plan_key(plan_id), '{"version":7}', ex=3600)
  await client.set(plan_state_key(plan_id), "rejected", ex=3600)

  result = await worker._strategy_publication_result(client, match, None)

  assert result.status == "terminal_reject"
  assert result.reason_code == "rejected"
  assert result.candidate_id is None


@pytest.mark.asyncio
async def test_published_setup_reconciles_on_replay_without_re_publishing():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="published-opposing-replay",
    thesis_id="published-opposing-thesis",
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4038.51,
    ts=int(time.time()),
    fresh=True,
    bid=4038.41,
    ask=4038.61,
  )
  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  )
  assert plan_id is not None

  # A second V7 pass with an opposing HTF market map must reconcile the
  # already-published plan without either re-publishing or invalidating it.
  # (Old code path: preflight short-circuited on existing_v7_plan; V7 now
  # owns that reconciliation itself.)
  replay_plan_id = await worker._publish_trade_plan_v7(
    client,
    "XAU",
    spot,
    match,
    market_map=_market_map(MapEntry(
      "buy",
      4037.0,
      4041.0,
      4037,
      4041,
      "major",
      ["demand"],
      20.0,
      contains_price=True,
    )),
  )

  assert replay_plan_id == plan_id
  assert (await load_setup(client, match.match_id)).state == PLAN_PUBLISHED
  assert await client.xlen(leaf(runtime_config, "auto_trade_trade_plan_stream")) == 1


@pytest.mark.asyncio
async def test_m5_authoritative_buy_uses_ask_for_same_cycle_eligibility():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="reaction-v7-buy",
    thesis_id="thesis-reaction-v7-buy",
    direction="BUY",
    key_level=4040.0,
    entry_low=4038.0,
    entry_high=4042.0,
    current_price=4041.8,
    structure_swing=4035.0,
    structural_kind="support",
    htf_bias="up",
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4041.75,
    ts=int(time.time()),
    fresh=True,
    bid=4041.6,
    ask=4041.9,
  )

  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.entry.price_side == "ask"
  assert plan.provenance.confirmation_source == "m5_authoritative"


@pytest.mark.asyncio
async def test_confirmed_trendline_sell_below_zone_waits_for_fresh_retest():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="incident-a-trendline",
    thesis_id="incident-a-thesis",
    strategy="Trendline Reaction",
    family="trendline",
    reaction_type="rejection_choch",
    key_level=4044.98,
    entry_low=4043.80,
    entry_high=4046.16,
    current_price=4040.68,
    structure_swing=4049.0,
    structural_source="trendline",
    structural_zone_id="trendline-4044",
    structural_zone_low=4043.80,
    structural_zone_high=4046.16,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4037.88,
    ts=int(time.time()),
    fresh=True,
    bid=4037.78,
    ask=4037.98,
  )

  assert await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  ) is None
  assert (await load_setup(client, match.match_id)).state == CONFIRMED
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == WAITING_RETEST
  assert await read_trade_plan(client, worker._v7_plan_id(match)) is None


@pytest.mark.asyncio
async def test_outside_reaction_routes_to_waiting_retest_via_v7(
  monkeypatch,
):
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="incident-a-preflight",
    thesis_id="incident-a-preflight-thesis",
    strategy="Trendline Reaction",
    family="trendline",
    reaction_type="rejection_choch",
    entry_low=4043.80,
    entry_high=4046.16,
    current_price=4040.68,
    structure_swing=4049.0,
    structural_source="trendline",
    structural_zone_id="trendline-preflight-4044",
    structural_zone_low=4043.80,
    structural_zone_high=4046.16,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4037.88,
    ts=int(time.time()),
    fresh=True,
    bid=4037.78,
    ask=4037.98,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_trendline_reaction_enabled": True,})

  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  )

  assert plan_id is None
  assert (await load_setup(client, match.match_id)).state == CONFIRMED
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == WAITING_RETEST


@pytest.mark.asyncio
async def test_inside_authoritative_reaction_admits_and_publishes(
  monkeypatch,
):
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="incident-b-preflight",
    thesis_id="incident-b-preflight-thesis",
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4038.51,
    ts=int(time.time()),
    fresh=True,
    bid=4038.41,
    ask=4038.61,
  )
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_strategy_match_enabled": True})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_news_guard_minutes": 0})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_key_level_reaction_enabled": True,})
  intent = _intent_for_match(match)

  # Admission accepts an inside authoritative reaction (no failure record).
  failure = await worker._admit_strategy_intent_for_cycle(
    client,
    intent,
    match,
    spot=spot,
    regime=RegimeInfo(
      "trend", "down", 3, 1.0, True, None, ("test",),
    ),
    htf_zones=[],
    htf_levels=[],
  )
  assert failure is None

  # V7 itself then publishes the plan for the same inside reaction.
  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  )
  assert plan_id is not None
  assert (await load_setup(client, match.match_id)).state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_retest_episode_finds_fresh_m1_and_publishes_in_same_cycle():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="incident-a-retest",
    thesis_id="incident-a-retest-thesis",
    strategy="Trendline Reaction",
    family="trendline",
    reaction_type="rejection_choch",
    key_level=4044.98,
    entry_low=4043.80,
    entry_high=4046.16,
    current_price=4040.68,
    structure_swing=4049.0,
    structural_source="trendline",
    structural_zone_id="trendline-retest-4044",
    structural_zone_low=4043.80,
    structural_zone_high=4046.16,
  )
  await _confirm_setup(client, match)
  outside_ts = int(time.time())
  outside = worker.AutoTradeSpot(
    price=4037.88,
    ts=outside_ts,
    fresh=True,
    bid=4037.78,
    ask=4037.98,
  )
  assert await worker._publish_trade_plan_v7(
    client, "XAU", outside, match,
  ) is None

  entered_ts = outside_ts + 60
  inside = worker.AutoTradeSpot(
    price=4044.60,
    ts=entered_ts,
    fresh=True,
    bid=4044.50,
    ask=4044.70,
  )
  before_publish = int(time.time())
  plan_id = await worker._publish_trade_plan_v7(
    client,
    "XAU",
    inside,
    match,
    frames={"M1": _sell_retest_bar(entered_ts)},
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.provenance.confirmation_source == "m1_retest"
  assert plan.provenance.confirmation_bar_ts == entered_ts
  assert plan.provenance.zone_episode_id
  assert plan.stop.source == "m1_trigger_wick"
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == PUBLISHED
  assert state.trigger_bar_ts == entered_ts
  assert state.episode_id == plan.provenance.zone_episode_id
  # Live incident fix: expiry restarts from actual publish time using
  # match_for_plan's own configured TTL - not inherited unchanged from
  # whenever the original match was built, which could leave a published
  # plan seconds from an already-near-exhausted deadline. An M1_RETEST
  # confirmation also truncates that TTL to the trigger's own validity
  # window (trigger_bar_ts + 60 + validity_bars*60) before this fix's
  # now_ts re-anchoring ever sees it - both must compose correctly.
  trigger_expiry = entered_ts + 60 + 2 * 60
  ttl_seconds = min(match.expires_at - match.issued_at, trigger_expiry - match.issued_at)
  assert plan.expires_at == pytest.approx(before_publish + ttl_seconds, abs=5)


@pytest.mark.asyncio
async def test_buy_retest_uses_ask_and_publishes_fresh_episode_trigger():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="buy-retest",
    thesis_id="buy-retest-thesis",
    direction="BUY",
    key_level=4040.0,
    entry_low=4038.36,
    entry_high=4042.09,
    current_price=4045.0,
    structure_swing=4035.0,
    structural_kind="support",
    htf_bias="up",
    structural_zone_id="buy-retest-zone",
    structural_zone_low=4038.36,
    structural_zone_high=4042.09,
  )
  await _confirm_setup(client, match)
  start = int(time.time())
  outside = worker.AutoTradeSpot(
    price=4048.0,
    ts=start,
    fresh=True,
    bid=4047.9,
    ask=4048.1,
  )
  assert await worker._publish_trade_plan_v7(
    client, "XAU", outside, match,
  ) is None

  entered_ts = start + 60
  inside = worker.AutoTradeSpot(
    price=4040.1,
    ts=entered_ts,
    fresh=True,
    bid=4040.0,
    ask=4040.2,
  )
  plan_id = await worker._publish_trade_plan_v7(
    client,
    "XAU",
    inside,
    match,
    frames={"M1": _buy_retest_bar(entered_ts)},
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.entry.price_side == "ask"
  assert plan.provenance.confirmation_source == "m1_retest"
  assert plan.provenance.confirmation_bar_ts == entered_ts
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == PUBLISHED


@pytest.mark.asyncio
async def test_reaction_expiry_is_terminal_while_waiting_retest():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="expiry-waiting-retest",
    thesis_id="expiry-waiting-retest-thesis",
    strategy="Trendline Reaction",
    family="trendline",
    reaction_type="rejection_choch",
    entry_low=4043.80,
    entry_high=4046.16,
    current_price=4040.68,
    structure_swing=4049.0,
    structural_zone_id="expiry-zone-waiting-retest",
    structural_zone_low=4043.80,
    structural_zone_high=4046.16,
  )
  await _confirm_setup(client, match)
  start = int(time.time())
  outside = worker.AutoTradeSpot(
    price=4037.88, ts=start, fresh=True, bid=4037.78, ask=4037.98,
  )
  await worker._publish_trade_plan_v7(client, "XAU", outside, match)

  expired = replace(match, expires_at=int(time.time()) - 1)
  assert await worker._publish_trade_plan_v7(
    client, "XAU", outside, expired,
  ) is None
  assert (await load_setup(client, match.match_id)).state == EXPIRED
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == "expired"
  assert await read_trade_plan(client, worker._v7_plan_id(match)) is None


@pytest.mark.asyncio
async def test_structure_invalidation_prevents_retest_revival():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="reaction-invalidated",
    thesis_id="reaction-invalidated-thesis",
    structure_swing=4045.0,
  )
  await _confirm_setup(client, match)
  invalidating_spot = worker.AutoTradeSpot(
    price=4045.6,
    ts=int(time.time()),
    fresh=True,
    bid=4045.5,
    ask=4045.7,
  )

  assert await worker._publish_trade_plan_v7(
    client, "XAU", invalidating_spot, match,
  ) is None
  assert (await load_setup(client, match.match_id)).state == INVALIDATED
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == "invalidated"

  inside = worker.AutoTradeSpot(
    price=4040.1,
    ts=invalidating_spot.ts + 60,
    fresh=True,
    bid=4040.0,
    ask=4040.2,
  )
  assert await worker._publish_trade_plan_v7(
    client,
    "XAU",
    inside,
    match,
    frames={"M1": _sell_retest_bar(inside.ts)},
  ) is None
  assert await read_trade_plan(client, worker._v7_plan_id(match)) is None


@pytest.mark.asyncio
async def test_reaction_missing_confirmation_metadata_fails_closed():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="reaction-metadata-missing",
    thesis_id="reaction-metadata-missing-thesis",
    confirmation_bar_ts=None,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4038.51,
    ts=int(time.time()),
    fresh=True,
    bid=4038.41,
    ask=4038.61,
  )

  assert await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  ) is None
  assert (await load_setup(client, match.match_id)).state == INVALIDATED
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == "invalidated"


@pytest.mark.asyncio
async def test_far_waits_then_executes_only_on_zone_reentry_without_m1():
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="trigger-left-zone",
    thesis_id="trigger-left-zone-thesis",
    strategy="Trendline Reaction",
    family="trendline",
    reaction_type="rejection_choch",
    key_level=4044.98,
    entry_low=4043.80,
    entry_high=4046.16,
    current_price=4040.68,
    structure_swing=4049.0,
    structural_source="trendline",
    structural_zone_id="trendline-trigger-left",
    structural_zone_low=4043.80,
    structural_zone_high=4046.16,
  )
  await _confirm_setup(client, match)
  start = int(time.time())
  outside = worker.AutoTradeSpot(
    price=4037.88, ts=start, fresh=True, bid=4037.78, ask=4037.98,
  )
  assert await worker._publish_trade_plan_v7(
    client, "XAU", outside, match,
  ) is None
  waiting = await load_execution_confirmation(client, match.match_id)
  assert waiting is not None
  assert waiting.phase == WAITING_RETEST

  returned = worker.AutoTradeSpot(
    price=4043.90,
    ts=start + 60,
    fresh=True,
    bid=4043.80,
    ask=4044.00,
  )
  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", returned, match,
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.provenance.confirmation_source == "m5_authoritative"
  assert (await load_execution_confirmation(
    client, match.match_id,
  )).phase == PUBLISHED


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("direction", "bid", "ask"),
  (
    ("SELL", 4033.90, 4040.00),
    ("BUY", 4040.00, 4046.20),
  ),
)
async def test_midpoint_inside_does_not_override_executable_quote_outside(
  direction, bid, ask,
):
  client = redis_state.get_client()
  match = _reaction_match(
    match_id=f"spread-boundary-{direction.lower()}",
    thesis_id=f"spread-boundary-thesis-{direction.lower()}",
    direction=direction,
    structure_swing=4045.0 if direction == "SELL" else 4035.0,
    structural_kind="resistance" if direction == "SELL" else "support",
    htf_bias="down" if direction == "SELL" else "up",
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4040.0,
    ts=int(time.time()),
    fresh=True,
    bid=bid,
    ask=ask,
  )

  assert await worker._publish_trade_plan_v7(
    client, "XAU", spot, match,
  ) is None
  state = await load_execution_confirmation(client, match.match_id)
  assert state is not None
  assert state.phase == WAITING_RETEST


@pytest.mark.asyncio
async def test_final_v7_gate_rejects_entry_inside_opposing_structure():
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
  # BUY planned entry sits inside opposing supply — must not publish
  # (live incident: SELL Key Level Reaction from demand).
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
  rejected = int(
    await client.hget("auto_trade:metrics:XAU", "target_room_rejected") or 0
  )
  assert rejected >= 1


@pytest.mark.asyncio
async def test_final_v7_gate_rejects_sell_from_demand_containment():
  """Live log 2026-07-31: SELL Key Level Reaction published from demand.

  opposing_entry_contained was preference-only; must hard-reject instead.
  """
  client = redis_state.get_client()
  match = _reaction_match(
    match_id="match-v7-sell-from-demand",
    thesis_id="thesis-v7-sell-from-demand",
    key_level=4055.0,
    entry_low=4053.12,
    entry_high=4057.08,
    current_price=4054.5,
    structure_swing=4058.5,
    structural_zone_id="zone-xau-4053-4057",
    structural_zone_low=4053.12,
    structural_zone_high=4057.08,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4054.5,
    ts=int(time.time()),
    fresh=True,
    bid=4054.40,
    ask=4054.60,
  )
  market_map = _market_map(MapEntry(
    "buy",
    4050.0,
    4056.0,
    4050,
    4056,
    "major",
    ["demand"],
    12.0,
  ))

  plan_id = await worker._publish_trade_plan_v7(
    client,
    "XAU",
    spot,
    match,
    market_map=market_map,
  )

  assert plan_id is None
  assert (await load_setup(client, match.match_id)).state == INVALIDATED
  assert await read_trade_plan(client, worker._v7_plan_id(match)) is None
  rejected = int(
    await client.hget("auto_trade:metrics:XAU", "target_room_rejected") or 0
  )
  assert rejected >= 1


@pytest.mark.asyncio
async def test_range_edge_scalp_publishes_inside_opposing_structure():
  """Scalp may trade inside HTF opposing as long as native range room fits."""
  client = redis_state.get_client()
  match = _match(
    match_id="match-v7-range-edge-opposing",
    thesis_id="thesis-v7-range-edge-opposing",
    strategy="Range Edge Scalp",
    strategy_mode="range_scalp",
    direction="BUY",
    family="range",
    structural_source="range_edge",
    structural_kind="demand",
    key_level=4089.0,
    entry_low=4088.10,
    entry_high=4090.00,
    current_price=4089.0,
    targets_pips=(20, 40, 60),
    full_take_profit_pips=20,
    range_id="range-xau-4070-4110",
    range_low=4070.0,
    range_high=4110.0,
    structure_swing=4070.0,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0,
    ts=int(time.time()),
    fresh=True,
    bid=4088.9,
    ask=4089.1,
  )
  # BUY entry sits inside opposing supply — reaction would hard-reject;
  # Range Edge Scalp must still publish (native room already selected 20p).
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
  assert plan.analysis.direction == "BUY"
  rejected = int(
    await client.hget("auto_trade:metrics:XAU", "target_room_rejected") or 0
  )
  assert rejected == 0


@pytest.mark.asyncio
async def test_hfs_scalp_publishes_inside_opposing_structure():
  """HFS with fitted native room must ignore HTF opposing containment."""
  client = redis_state.get_client()
  match = _match(
    match_id="match-v7-hfs-opposing",
    thesis_id="thesis-v7-hfs-opposing",
    strategy="HFS Range Sweep",
    strategy_mode="hfs_scalp",
    direction="BUY",
    family="hfs",
    structural_source="hfs",
    structural_kind="demand",
    key_level=4089.0,
    entry_low=4088.10,
    entry_high=4090.00,
    current_price=4089.0,
    targets_pips=(20,),
    full_take_profit_pips=20,
    structure_swing=4070.0,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0,
    ts=int(time.time()),
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
  assert plan.analysis.direction == "BUY"
  rejected = int(
    await client.hget("auto_trade:metrics:XAU", "target_room_rejected") or 0
  )
  assert rejected == 0


@pytest.mark.asyncio
async def test_range_edge_without_target_room_still_hits_opposing():
  """Scalp opposing bypass requires fitted full_take_profit_pips.

  Planned BUY entry is literally inside opposing supply → hard reject
  via target-room containment (not soft barrier-ahead telemetry).
  """
  client = redis_state.get_client()
  match = _match(
    match_id="match-v7-range-edge-no-room",
    thesis_id="thesis-v7-range-edge-no-room",
    strategy="Range Edge Scalp",
    strategy_mode="range_scalp",
    direction="BUY",
    family="range",
    structural_source="range_edge",
    structural_kind="demand",
    key_level=4089.0,
    entry_low=4088.10,
    entry_high=4090.00,
    current_price=4089.0,
    targets_pips=(30, 60, 90),
    full_take_profit_pips=None,
    range_id="range-xau-4070-4110",
    range_low=4070.0,
    range_high=4110.0,
    structure_swing=4070.0,
  )
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0,
    ts=int(time.time()),
    fresh=True,
    bid=4088.9,
    ask=4089.1,
  )
  market_map = _market_map(MapEntry(
    "sell",
    4088.0,
    4095.0,
    4088,
    4095,
    "major",
    ["supply"],
    13.0,
  ))

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


@pytest.mark.asyncio
async def test_final_v7_gate_caps_target_ladder_before_opposing_structure():
  client = redis_state.get_client()
  match = _match(
    match_id="match-v7-target-cap",
    thesis_id="thesis-v7-target-cap",
    structure_swing=4087.5,
  )
  await _confirm_setup(client, match)
  now = int(time.time())
  spot = worker.AutoTradeSpot(
    price=4089.0,
    ts=now,
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
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_contract_mode": "legacy_v6"})
  client = redis_state.get_client()
  match = _match(match_id="match-v7-2", thesis_id="thesis-v7-2")
  await _confirm_setup(client, match)
  spot = worker.AutoTradeSpot(
    price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1,
  )

  plan_id = await _publish_nonreaction_after_m1(
    client,
    spot,
    match,
  )

  assert plan_id is not None
  record = await load_setup(client, "match-v7-2")
  assert record.state == PLAN_PUBLISHED


@pytest.mark.asyncio
async def test_second_setup_for_same_thesis_is_rejected_not_duplicated():
  client = redis_state.get_client()
  spot = worker.AutoTradeSpot(
    price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1,
  )

  first_match = _match(match_id="match-v7-3a", thesis_id="thesis-v7-shared")
  await _confirm_setup(client, first_match)
  first_plan_id = await _publish_nonreaction_after_m1(
    client, spot, first_match,
  )
  assert first_plan_id is not None

  second_match = _match(match_id="match-v7-3b", thesis_id="thesis-v7-shared")
  await _confirm_setup(client, second_match)
  second_plan_id = await _publish_nonreaction_after_m1(
    client, spot, second_match,
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
  spot = worker.AutoTradeSpot(
    price=4089.0, ts=int(time.time()), fresh=True, bid=4088.9, ask=4089.1,
  )

  plan_id = await worker._publish_trade_plan_v7(client, "XAU", spot, match)

  assert plan_id is None
  assert await load_setup(client, "match-v7-4") is None


@pytest.mark.asyncio
async def test_nonreaction_non_qualifying_m1_still_publishes_on_zone_presence():
  client = redis_state.get_client()
  match = _match(match_id="match-v7-5", thesis_id="thesis-v7-5")
  await _confirm_setup(client, match)
  now = int(time.time())
  spot = worker.AutoTradeSpot(
    price=4089.0, ts=now, fresh=True, bid=4088.9, ask=4089.1,
  )
  # A tiny, symmetric doji sitting inside the zone with no directional wick,
  # body, or close - qualifies for none of the six patterns. Zone presence
  # alone still authorizes publication; M1 is optional preference evidence.
  index = pd.date_range(
    pd.Timestamp(now, unit="s", tz="UTC") - pd.Timedelta(seconds=30),
    periods=1,
    freq="1min",
    tz="UTC",
  )
  flat_bar = pd.DataFrame({
    "open": [4089.0], "high": [4089.05], "low": [4088.95], "close": [4089.0],
    "volume": [100.0],
  }, index=index)

  plan_id = await worker._publish_trade_plan_v7(
    client, "XAU", spot, match, frames={"M1": flat_bar},
  )

  assert plan_id is not None
  plan = await read_trade_plan(client, plan_id)
  assert plan is not None
  assert plan.stop.source == "m5_structure"
  assert plan.provenance.confirmation_source == "m5_authoritative"
  record = await load_setup(client, "match-v7-5")
  assert record.state == PLAN_PUBLISHED
