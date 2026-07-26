"""Regression coverage for cross-engine execution integrity."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import fakeredis
import pytest

from app.autotrade.arbitration import (
  ExecutionPreflightDecision,
  ExecutionIntent,
  arbitrate_execution_intents,
  arbitrate_preflight_decisions,
)
from app.autotrade.candidate_publish import (
  autonomous_cycle_owner_key,
  publish_candidate_atomic,
)
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


def test_failed_tier_a_preflight_cannot_suppress_executable_tier_b():
  invalid_buy = _intent(
    "buy-a", direction="BUY", confluence=4, tier="A",
  )
  valid_sell = _intent(
    "sell-b", direction="SELL", confluence=3, tier="B",
  )
  decisions = [
    ExecutionPreflightDecision(
      intent=invalid_buy,
      executable=False,
      terminal=False,
      status="waiting",
      stage="news",
      reason_code="news_window_active",
      message="high-impact event",
    ),
    ExecutionPreflightDecision(
      intent=valid_sell,
      executable=True,
      terminal=False,
      status="checking",
      stage="policy",
      reason_code="preflight_allowed",
      message="allowed",
    ),
  ]

  result = arbitrate_preflight_decisions(decisions)

  assert [item.intent_id for item in result.ordered] == ["sell-b"]
  assert invalid_buy not in result.ordered
  assert decisions[0].reason_code == "news_window_active"


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


def test_execution_policy_keeps_fill_relative_room_from_planned_entry():
  evaluation = evaluate_execution_policy(
    _policy_match(),
    spot_price=4102.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["remaining_target_room_pips"] == 60.0
  assert evaluation.measured["target_model"] == "fill_relative"
  assert evaluation.measured["planned_entry_price"] == 4102.5
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


def test_execution_policy_rejects_zero_risk_multiplier():
  evaluation = evaluate_execution_policy(
    _policy_match(risk_multiplier=0),
    spot_price=4100.5,
    regime="trend",
    pip_size=0.1,
  )

  assert not evaluation.allowed
  assert evaluation.terminal
  assert evaluation.reason_code == "invalid_risk_multiplier"


def test_absolute_target_room_is_measured_from_planned_entry():
  evaluation = evaluate_execution_policy(
    _policy_match(
      target_model="absolute",
      absolute_target_price=4106.5,
      target_price=4106.5,
      targets_pips=(),
      target_reference_price="structural_level",
    ),
    spot_price=4102.5,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.measured["planned_entry_price"] == 4102.5
  assert evaluation.measured["remaining_target_room_pips"] == 40.0


def test_private_trend_policy_forwards_limit_and_single_distribution():
  evaluation = evaluate_execution_policy(
    _policy_match(
      strategy="Trend Pullback",
      entry_low=4100.0,
      entry_high=4100.3,
      current_price=4100.2,
      structure_swing=4098.5,
    ),
    spot_price=4100.2,
    regime="trend",
    pip_size=0.1,
  )

  assert evaluation.allowed
  assert evaluation.policy is not None
  assert evaluation.policy.order_type_preference == "limit"
  assert evaluation.measured["entry_distribution"] == "single"


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
async def test_preflight_rejects_unavailable_zone_split_capability(
  monkeypatch,
):
  from app.autotrade import worker
  from app.autotrade.trend import RegimeInfo

  subject = _policy_match(
    strategy="Trend Pullback",
    entry_low=4100.0,
    entry_high=4100.8,
    current_price=4100.6,
    structure_swing=4098.0,
  )
  intent = _intent("trend-zone-split", direction="BUY")
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_zone_fill_enabled", False)

  decision = await worker._common_preflight(
    redis_state.get_client(),
    intent,
    subject,
    spot=worker.AutoTradeSpot(4100.6, 1_000, True),
    regime=RegimeInfo(
      "trend", "up", 3, 1.0, True, None, ("test",),
    ),
    htf_zones=[],
    htf_levels=[],
    market_map=None,
    frames={},
  )

  assert not decision.executable
  assert decision.terminal
  assert decision.reason_code == "zone_split_capability_unavailable"


@pytest.mark.asyncio
async def test_preflight_waits_when_required_limit_is_on_wrong_side(
  monkeypatch,
):
  from app.autotrade import worker
  from app.autotrade.trend import RegimeInfo

  subject = _policy_match(
    strategy="Trend Pullback",
    entry_low=4101.0,
    entry_high=4101.3,
    current_price=4100.0,
    structure_swing=4098.0,
  )
  intent = _intent("trend-limit-side", direction="BUY")
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)
  monkeypatch.setattr(worker.settings, "auto_trade_zone_fill_enabled", True)

  decision = await worker._common_preflight(
    redis_state.get_client(),
    intent,
    subject,
    spot=worker.AutoTradeSpot(4100.0, 1_000, True),
    regime=RegimeInfo(
      "trend", "up", 3, 1.0, True, None, ("test",),
    ),
    htf_zones=[],
    htf_levels=[],
    market_map=None,
    frames={},
  )

  assert not decision.executable
  assert not decision.terminal
  assert decision.reason_code == "required_limit_side_unavailable"


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
async def test_one_cycle_owner_allows_only_one_distinct_candidate():
  client = redis_state.get_client()
  owner_key = autonomous_cycle_owner_key("XAU", "closed-m1-1000")

  results = await asyncio.gather(*[
    publish_candidate_atomic(
      client,
      stream="auto_trade:test:cycle",
      candidate_id=f"candidate-cycle-{index}",
      payload=json.dumps({"candidate_id": f"candidate-cycle-{index}"}),
      ttl=300,
      maxlen=100,
      ownership_key=owner_key,
      ownership_payload=f"candidate-cycle-{index}",
      ownership_ttl=300,
    )
    for index in range(12)
  ])

  assert sum(item.published for item in results) == 1
  assert await client.xlen("auto_trade:test:cycle") == 1
  assert sum(item.status == "conflict" for item in results) == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_kind", ["reaction", "thesis"])
async def test_test_fallback_rolls_back_claim_when_xadd_crashes(claim_kind):
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client._apexvoid_allow_non_atomic_test_fallback = True
  client.xadd = AsyncMock(side_effect=RuntimeError("crash before XADD"))
  kwargs = {
    f"{claim_kind}_key": f"claim:{claim_kind}:1",
    f"{claim_kind}_payload": '{"state":"candidate_published"}',
    f"{claim_kind}_ttl": 300,
  }

  with pytest.raises(RuntimeError, match="crash before XADD"):
    await publish_candidate_atomic(
      client,
      stream="auto_trade:test:crash",
      candidate_id=f"candidate-crash-{claim_kind}",
      payload="{}",
      ttl=300,
      maxlen=100,
      **kwargs,
    )

  assert await client.get(f"claim:{claim_kind}:1") is None
  assert await client.get(
    f"auto_trade:candidate:candidate-crash-{claim_kind}"
  ) is None


@pytest.mark.asyncio
async def test_successful_publication_keeps_reaction_and_thesis_chain():
  client = redis_state.get_client()

  result = await publish_candidate_atomic(
    client,
    stream="auto_trade:test:ownership",
    candidate_id="candidate-ownership",
    payload="{}",
    ttl=300,
    maxlen=100,
    reaction_key="claim:reaction:success",
    reaction_payload='{"state":"claimed"}',
    reaction_ttl=300,
    thesis_key="claim:thesis:success",
    thesis_payload='{"state":"candidate_published"}',
    thesis_ttl=300,
  )

  assert result.published
  assert await client.get("claim:reaction:success") == '{"state":"claimed"}'
  assert (
    await client.get("claim:thesis:success")
    == '{"state":"candidate_published"}'
  )


@pytest.mark.asyncio
async def test_eval_failure_fails_closed_without_production_fallback():
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client.eval = AsyncMock(side_effect=RuntimeError("EVAL unavailable"))

  result = await publish_candidate_atomic(
    client,
    stream="auto_trade:test:production-failure",
    candidate_id="candidate-production-failure",
    payload="{}",
    ttl=300,
    maxlen=100,
  )

  assert not result.published
  assert result.status == "atomic_publish_unavailable"
  assert await client.xlen("auto_trade:test:production-failure") == 0
  readiness = json.loads(await client.get("auto_trade:publication_readiness"))
  assert readiness == {
    "ready": False,
    "reason_code": "atomic_publish_unavailable",
  }


@pytest.mark.asyncio
async def test_eval_failure_uses_only_explicit_test_fallback():
  client = fakeredis.FakeAsyncRedis(decode_responses=True)
  client.eval = AsyncMock(side_effect=RuntimeError("EVAL unavailable"))

  result = await publish_candidate_atomic(
    client,
    stream="auto_trade:test:explicit-fallback",
    candidate_id="candidate-explicit-fallback",
    payload="{}",
    ttl=300,
    maxlen=100,
    allow_non_atomic_test_fallback=True,
  )

  assert result.published
  assert result.status == "published"
  assert await client.xlen("auto_trade:test:explicit-fallback") == 1
  assert await client.get(
    "auto_trade:candidate:candidate-explicit-fallback"
  ) == "published"


def test_all_selected_publication_failures_keep_exact_publisher_evidence():
  from app.autotrade.worker import _arbitration_followup

  selected_a = _intent(
    "buy-a", direction="BUY", confluence=4, tier="A",
  )
  selected_b = _intent(
    "buy-b", direction="BUY", confluence=3, tier="B",
  )
  opposite = _intent(
    "sell-b", direction="SELL", confluence=2, tier="B",
  )
  decisions = [
    ExecutionPreflightDecision(
      intent=item,
      executable=True,
      terminal=False,
      status="checking",
      stage="policy",
      reason_code="preflight_allowed",
      message="allowed",
    )
    for item in (selected_a, selected_b, opposite)
  ]
  arbitration = arbitrate_preflight_decisions(decisions)
  ordered_ids = {item.intent_id for item in arbitration.ordered}
  attempted_ids = {selected_a.intent_id, selected_b.intent_id}

  assert _arbitration_followup(
    selected_a,
    arbitration=arbitration,
    published_intent=None,
    ordered_ids=ordered_ids,
    attempted_intent_ids=attempted_ids,
  ) is None
  assert _arbitration_followup(
    selected_b,
    arbitration=arbitration,
    published_intent=None,
    ordered_ids=ordered_ids,
    attempted_intent_ids=attempted_ids,
  ) is None
  assert _arbitration_followup(
    opposite,
    arbitration=arbitration,
    published_intent=None,
    ordered_ids=ordered_ids,
    attempted_intent_ids=attempted_ids,
  ) == (
    "arbitration_suppressed",
    "selected_direction_exhausted",
    "intent excluded by cross-engine direction arbitration",
  )


@pytest.mark.asyncio
async def test_range_rail_status_uses_real_ownership_state():
  from app.autotrade.worker import _load_range_side_status

  client = redis_state.get_client()
  await client.set(
    "auto_trade:range_side:XAU:episode-2:BUY",
    json.dumps({
      "state": "MANAGING",
      "candidate_id": "candidate-2",
      "pending_order_ids": [71],
      "position_ids": [81],
      "group_id": "group-2",
    }),
  )

  status = await _load_range_side_status(
    client,
    symbol="XAU",
    range_id="episode-2",
    direction="BUY",
  )

  assert status == {
    "state": "MANAGING",
    "candidate_id": "candidate-2",
    "pending_order_ids": [71],
    "position_ids": [81],
    "group_id": "group-2",
  }


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


@pytest.mark.asyncio
async def test_route_keeps_preflight_arbitration_and_publication_evidence():
  client = redis_state.get_client()
  match = SimpleNamespace(
    symbol="XAU",
    match_id="route-stages-1",
    strategy="Demand Zone Reaction",
    family="supply_demand",
    direction="BUY",
    structural_source="supply_demand",
    structural_zone_id="demand-1",
    issued_at=1_000,
    expires_at=2_000,
    current_price=4100.0,
    entry_low=4100.0,
    entry_high=4101.0,
  )
  await record_route_outcome(
    client,
    match,
    stage="policy",
    status="checking",
    reason_code="preflight_allowed",
    message="allowed",
    retained=True,
  )
  await record_route_outcome(
    client,
    match,
    stage="stream_publish",
    status="candidate_published",
    reason_code="candidate_published",
    message="published",
    candidate_id="candidate-exact",
    group_id="group-exact",
    executor_event_id="171-0",
    retained=False,
    arbitration_reason_code="selected_for_publication",
    winner_intent_id="strategy:route-stages-1",
    signal_source="scanner_strategy_match",
  )

  route = json.loads(await client.get(
    "auto_trade:route_outcome:XAU:route-stages-1"
  ))
  assert route["preflight_reason_code"] == "preflight_allowed"
  assert route["arbitration_reason_code"] == "selected_for_publication"
  assert route["publication_reason_code"] == "candidate_published"
  assert route["current_stage"] == "stream_publish"
  assert route["candidate_id"] == "candidate-exact"
  assert route["executor_event_id"] == "171-0"
  assert route["winner_intent_id"] == "strategy:route-stages-1"
  assert route["signal_source"] == "scanner_strategy_match"
