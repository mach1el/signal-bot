"""Regression coverage for cross-engine execution integrity."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.autotrade.arbitration import (
  ExecutionIntent,
  arbitrate_execution_intents,
)
from app.autotrade.candidate_publish import publish_candidate_atomic
from app.autotrade.execution_policy import evaluate_execution_policy
from app.autotrade.route_outcome import record_route_outcome
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _intent(
  intent_id: str,
  *,
  direction: str,
  confluence: int = 3,
  tier: str = "A",
  freshness: float = 100.0,
  distance_pips: float = 0.0,
) -> ExecutionIntent:
  return ExecutionIntent(
    intent_id=intent_id,
    source="scanner_strategy_match",
    strategy="Liquidity Sweep",
    direction=direction,
    confluence=confluence,
    tier=tier,
    freshness=freshness,
    distance_pips=distance_pips,
  )


def test_arbiter_suppresses_equal_opposite_direction_intents():
  result = arbitrate_execution_intents([
    _intent("buy", direction="BUY"),
    _intent("sell", direction="SELL"),
  ])

  assert result.ordered == ()
  assert {item.intent_id for item in result.suppressed} == {"buy", "sell"}
  assert result.reason_code == "opposite_direction_conflict"


def test_arbiter_orders_only_the_winning_direction():
  result = arbitrate_execution_intents([
    _intent("buy-a", direction="BUY", confluence=4),
    _intent("buy-b", direction="BUY", confluence=3),
    _intent("sell-b", direction="SELL", confluence=2, tier="B"),
  ])

  assert [item.intent_id for item in result.ordered] == ["buy-a", "buy-b"]
  assert [item.intent_id for item in result.suppressed] == ["sell-b"]


def _policy_match(**overrides):
  values = {
    "strategy": "Mapped Zone Reaction",
    "direction": "BUY",
    "entry_low": 4100.0,
    "entry_high": 4101.0,
    "current_price": 4100.5,
    "confluence": 3,
    "atr": 1.0,
    "structure_swing": 4099.5,
    "targets_pips": (30, 60),
    "target_price": None,
    "risk_multiplier": 1.0,
  }
  values.update(overrides)
  return SimpleNamespace(**values)


def test_execution_policy_uses_remaining_room_from_detection_price():
  evaluation = evaluate_execution_policy(
    _policy_match(),
    spot_price=4102.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["remaining_target_room_pips"] == 40.0
  assert evaluation.measured["order_type_preference"] == "either"
  assert evaluation.measured["effective_risk_multiplier"] == 1.0


def test_execution_policy_applies_family_risk_multiplier():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Liquidity Sweep",
      entry_high=4101.0,
      structure_swing=4099.5,
      risk_multiplier=0.5,
    ),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["effective_risk_multiplier"] == 0.375


def test_execution_policy_rejects_wide_and_unknown_strategies():
  wide = evaluate_execution_policy(
    _policy_match(entry_high=4103.0),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )
  unknown = evaluate_execution_policy(
    _policy_match(strategy="Unreviewed Detector"),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )

  assert not wide.allowed
  assert wide.terminal
  assert wide.reason_code == "policy_zone_too_wide"
  assert not unknown.allowed
  assert unknown.terminal
  assert unknown.reason_code == "unknown_strategy_policy"


@pytest.mark.asyncio
async def test_candidate_claim_and_stream_append_are_single_winner():
  client = redis_state.get_client()

  results = await asyncio.gather(*[
    publish_candidate_atomic(
      client,
      stream="auto_trade:test:atomic",
      candidate_id="candidate-atomic",
      payload='{"candidate_id":"candidate-atomic"}',
      ttl=300,
      maxlen=100,
    )
    for _ in range(12)
  ])

  assert sum(1 for published, _ in results if published) == 1
  assert await client.xlen("auto_trade:test:atomic") == 1
  assert await client.get(
    "auto_trade:candidate:candidate-atomic"
  ) == "published"


@pytest.mark.asyncio
async def test_route_funnel_is_unique_and_history_tracks_material_change():
  client = redis_state.get_client()
  match = SimpleNamespace(
    symbol="XAU",
    match_id="route-match-1",
    strategy="Liquidity Sweep",
    family="liquidity_reversal",
    direction="BUY",
    structural_source="liquidity",
    issued_at=1_000,
    expires_at=2_000,
    current_price=4100.0,
    entry_low=4101.0,
    entry_high=4102.0,
  )
  for distance in (13.2, 13.7, 15.0):
    await record_route_outcome(
      client,
      match,
      stage="entry_drift",
      status="waiting",
      reason_code="strategy_entry_moved",
      message="entry waits for price",
      measured={"distance_pips": distance},
      retained=True,
    )

  assert await client.xlen("auto_trade:route_history:XAU") == 2
  assert int(await client.hget(
    "auto_trade:metrics:XAU", "strategy_match_waiting"
  )) == 1
  latest = json.loads(await client.get(
    "auto_trade:route_outcome:XAU:route-match-1"
  ))
  assert latest["measured"]["distance_pips"] == 15.0
