"""Telegram delivery correlation audit (independent-position-tracking quickfix).

Confirms two invariants the incident report asked to be verified rather than
assumed:

1. clear_active_setup_tracking (called on every "opened" event) only ever
   touches scanner watchlist/forming state (scanner:setup:*) - never
   execution tracking or Telegram message anchors for an already-open
   position (auto_trade:position:*, auto_trade:msg:*, auto_trade:tp_msg:*).
2. The reply target for take_profit/stop_moved/position_closed is resolved
   strictly from that event's own position_id - never falls back to a
   group-level key or another active same-direction trade's message.
"""

from __future__ import annotations

import pytest

from app.analysis.scanner import clear_active_setup_tracking
from app.autotrade.delivery import (
  _compact_route_line,
  _group_message_key,
  _message_key,
  _resolve_reply_message_id,
  tp_message_key,
)
from app.persistence import redis_state


@pytest.mark.asyncio
async def test_clear_active_setup_tracking_never_touches_execution_state():
  client = redis_state.get_client()
  await client.set("scanner:setup:active:XAU:M5:BUY", "1")
  await client.set("scanner:setup:active_band:XAU:M5:BUY:zone-1", "1")
  await client.set("auto_trade:position:91", "should-survive")
  await client.set(_message_key("internal", 91), "12345")
  await client.set(tp_message_key("internal", 91), "12346")

  await clear_active_setup_tracking(client, "XAU", tf="M5", direction="BUY")

  assert await client.get("scanner:setup:active:XAU:M5:BUY") is None
  assert await client.get("scanner:setup:active_band:XAU:M5:BUY:zone-1") is None
  assert await client.get("auto_trade:position:91") == "should-survive"
  assert await client.get(_message_key("internal", 91)) == "12345"
  assert await client.get(tp_message_key("internal", 91)) == "12346"


@pytest.mark.parametrize("event_type", ["take_profit", "stop_moved", "position_closed"])
@pytest.mark.asyncio
async def test_position_events_resolve_reply_only_from_their_own_position_id(
  event_type,
):
  client = redis_state.get_client()
  # Position A's own message, position B's message, and a group-level
  # message all exist - only A's own key may ever be picked for A's event.
  await client.set(_message_key("internal", 91), "1001")
  await client.set(_message_key("internal", 92), "2002")
  await client.set(_group_message_key("internal", "group-a"), "3003")

  message_id, reason = await _resolve_reply_message_id(
    client,
    {
      "type": event_type,
      "position_id": 91,
      "group_id": "group-a",
    },
    "internal",
  )

  assert message_id == 1001
  assert reason == ""


@pytest.mark.parametrize("event_type", ["take_profit", "stop_moved", "position_closed"])
@pytest.mark.asyncio
async def test_position_events_never_fall_back_to_group_or_other_position(
  event_type,
):
  client = redis_state.get_client()
  # This position has no message of its own yet, but a sibling position in
  # the same group and a group-level message both exist - neither may be
  # used as a substitute reply target.
  await client.set(_message_key("internal", 92), "2002")
  await client.set(_group_message_key("internal", "group-a"), "3003")

  message_id, reason = await _resolve_reply_message_id(
    client,
    {
      "type": event_type,
      "position_id": 91,
      "group_id": "group-a",
    },
    "internal",
  )

  assert message_id is None
  assert reason != ""


def test_compact_route_line_never_shows_a_preflight_pass_through_code():
  # preflight_reason_code lingers as whatever the last preflight-stage
  # event recorded - once a candidate clears every preflight check that's
  # "preflight_allowed", which then sits there as the displayed "why" on
  # every later status check even though it explains nothing (it means
  # "passed", not "here's what happened"). Confirmed live: a card reading
  # "Key Level Reaction · waiting · preflight allowed" told the owner
  # nothing about why nothing had executed yet.
  line = _compact_route_line({
    "strategy": "Key Level Reaction",
    "status": "waiting",
    "preflight_reason_code": "preflight_allowed",
  })

  assert line == "Key Level Reaction · waiting"


def test_compact_route_line_still_shows_a_genuine_rejection_reason():
  line = _compact_route_line({
    "strategy": "Key Level Reaction",
    "status": "blocked",
    "reason_code": "opposing_entry_contained",
  })

  assert line == "Key Level Reaction · blocked · opposing_entry_contained"


def test_compact_route_line_prefers_reason_code_over_stale_preflight_code():
  line = _compact_route_line({
    "strategy": "Key Level Reaction",
    "status": "blocked",
    "reason_code": "policy_reward_risk_insufficient",
    "preflight_reason_code": "preflight_allowed",
  })

  assert line == "Key Level Reaction · blocked · policy_reward_risk_insufficient"
