"""execution:trade_plans stream plumbing - publish/read round trip only.

Phase 1: no broker execution, no ownership/claim logic yet (that is Phase 3,
when the C# TradePlanExecutionEngine actually consumes this stream).
"""

from __future__ import annotations

import json

import pytest

from app.autotrade.trade_plan import TradePlan
from app.autotrade.trade_plan_stream import (
  plan_key,
  plan_state_key,
  publish_trade_plan,
  read_plan_state,
  read_trade_plan,
)
from app.persistence import redis_state


def _plan(**overrides) -> TradePlan:
  base = json.loads(
    (
      __import__("pathlib").Path(__file__).resolve().parents[2]
      / "contracts" / "autotrade" / "trade-plan-v7.json"
    ).read_text()
  )
  plan_dict = next(
    case["plan"] for case in base["valid_plans"] if case["name"] == "market_watch_buy"
  )
  plan_dict = {**plan_dict, **overrides}
  return TradePlan.from_dict(plan_dict)


@pytest.mark.asyncio
async def test_publish_writes_stream_entry_and_plan_key():
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-1")

  event_id = await publish_trade_plan(client, plan)

  assert event_id
  assert await client.xlen("execution:trade_plans") == 1
  stored = await client.get(plan_key("plan-stream-1"))
  assert json.loads(stored)["plan_id"] == "plan-stream-1"


@pytest.mark.asyncio
async def test_publish_sets_plan_state_to_published():
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-2")

  await publish_trade_plan(client, plan)

  assert await read_plan_state(client, "plan-stream-2") == "published"
  assert await client.get(plan_state_key("plan-stream-2")) is not None


@pytest.mark.asyncio
async def test_read_trade_plan_round_trips_through_redis():
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-3")

  await publish_trade_plan(client, plan)
  reread = await read_trade_plan(client, "plan-stream-3")

  assert reread is not None
  assert reread.plan_id == plan.plan_id
  assert reread.stop.price == plan.stop.price


@pytest.mark.asyncio
async def test_read_trade_plan_returns_none_for_unknown_id():
  client = redis_state.get_client()

  assert await read_trade_plan(client, "does-not-exist") is None
  assert await read_plan_state(client, "does-not-exist") is None


@pytest.mark.asyncio
async def test_publishing_never_touches_the_v6_candidate_stream():
  # V7 publication must be fully isolated from the V6 auto_trade:candidates
  # stream - a shared stream would let a V7 plan accidentally trigger the
  # legacy candidate consumer.
  client = redis_state.get_client()
  plan = _plan(plan_id="plan-stream-isolation")

  await publish_trade_plan(client, plan)

  assert await client.xlen("auto_trade:candidates") == 0
