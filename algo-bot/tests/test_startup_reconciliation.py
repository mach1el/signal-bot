"""P0-10: startup reconciliation for state left over from before this
branch's invariants existed - orphaned forming cards, un-indexed active
setups, status-with-no-card, and terminal setups with a stale route_outcome
snapshot.
"""

from __future__ import annotations

import json
import time

import pytest

from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.setup_card import (
  forming_message_key,
  forming_reconcile_pending_key,
  forming_status_key,
  load_forming_card,
)
from app.autotrade.setup_lifecycle import (
  INVALIDATED,
  create_setup,
  setup_expiry_index_key,
  transition_setup,
)
from app.autotrade.startup_reconciliation import reconcile_startup_state
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_reconcile_repairs_un_indexed_active_setup():
  client = redis_state.get_client()
  future = int(time.time()) + 3600
  await create_setup(
    client, setup_id="startup-1", thesis_id="t1", symbol="XAU",
    expires_at=future,
  )
  # Simulate a record written before the expiry index existed at all.
  await client.zrem(setup_expiry_index_key(), "startup-1")

  await reconcile_startup_state(client)

  assert await client.zscore(setup_expiry_index_key(), "startup-1") == float(future)
  metrics = await client.hgetall("auto_trade:metrics:XAU")
  assert int(metrics.get("setup_expiry_index_repaired", 0)) >= 1


@pytest.mark.asyncio
async def test_reconcile_clears_redis_for_missing_canonical_setup():
  # Startup must not touch Telegram (flood source). Redis forming keys only.
  client = redis_state.get_client()
  await client.set(
    forming_message_key("startup-ghost"),
    json.dumps({"chat_id": 1, "message_id": 2, "text": "x"}),
    ex=60,
  )

  await reconcile_startup_state(client)

  assert await load_forming_card(client, "startup-ghost") is None
  metrics = await client.hgetall("auto_trade:metrics:XAU")
  assert int(metrics.get("missing_canonical_setup_projection_removed", 0)) >= 1


@pytest.mark.asyncio
async def test_reconcile_clears_redis_orphan_card_for_terminal_setup():
  client = redis_state.get_client()
  await create_setup(
    client, setup_id="startup-terminal", thesis_id="t1", symbol="XAU",
  )
  await transition_setup(client, "startup-terminal", INVALIDATED)
  await client.set(
    forming_message_key("startup-terminal"),
    json.dumps({"chat_id": 1, "message_id": 2, "text": "x"}),
    ex=60,
  )

  await reconcile_startup_state(client)

  assert await load_forming_card(client, "startup-terminal") is None
  metrics = await client.hgetall("auto_trade:metrics:XAU")
  assert int(metrics.get("orphan_forming_card_removed", 0)) >= 1


@pytest.mark.asyncio
async def test_reconcile_marks_pending_when_status_exists_without_a_card():
  client = redis_state.get_client()
  await create_setup(
    client, setup_id="startup-pending", thesis_id="t1", symbol="XAU",
  )
  await client.set(
    forming_status_key("startup-pending"),
    json.dumps({"state": "queued", "status_line": "QUEUED", "priority": 10}),
    ex=60,
  )

  await reconcile_startup_state(client)

  marker_raw = await client.get(forming_reconcile_pending_key("startup-pending"))
  assert marker_raw is not None
  marker = json.loads(marker_raw)
  assert marker["status_line"] == "QUEUED"
  metrics = await client.hgetall("auto_trade:metrics:XAU")
  assert int(metrics.get("forming_status_pending_card", 0)) >= 1


@pytest.mark.asyncio
async def test_reconcile_repairs_stale_route_outcome_for_terminal_setup():
  client = redis_state.get_client()
  await create_setup(
    client, setup_id="startup-route", thesis_id="t1", symbol="XAU",
  )
  await transition_setup(client, "startup-route", INVALIDATED)
  # A route_outcome snapshot left over from before the setup went
  # terminal - still reads "waiting", never corrected.
  await client.set(
    route_outcome_key("XAU", "startup-route"),
    json.dumps({"status": "waiting", "reason_code": "stale"}),
    ex=3600,
  )

  await reconcile_startup_state(client)

  repaired_raw = await client.get(route_outcome_key("XAU", "startup-route"))
  repaired = json.loads(repaired_raw)
  assert repaired["status"] == "blocked"
  metrics = await client.hgetall("auto_trade:metrics:XAU")
  assert int(metrics.get("route_projection_repaired", 0)) >= 1


@pytest.mark.asyncio
async def test_reconcile_is_idempotent():
  client = redis_state.get_client()
  future = int(time.time()) + 3600
  await create_setup(
    client, setup_id="startup-idempotent", thesis_id="t1", symbol="XAU",
    expires_at=future,
  )

  await reconcile_startup_state(client)
  await reconcile_startup_state(client)

  assert await client.zscore(
    setup_expiry_index_key(), "startup-idempotent",
  ) == float(future)
