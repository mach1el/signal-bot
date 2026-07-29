"""P0-9: finalize_terminal_setup, the shared coordinator for everything
that must happen once a setup reaches a terminal state.
"""

from __future__ import annotations

import time

import pytest

from app.autotrade.setup_lifecycle import (
  EXPIRED,
  INVALIDATED,
  claim_active_thesis,
  create_setup,
  transition_setup,
)
from app.autotrade.terminal_cleanup import finalize_terminal_setup
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_finalize_terminal_setup_releases_a_thesis_claim_it_owns():
  client = redis_state.get_client()
  await create_setup(
    client, setup_id="terminal-thesis-1", thesis_id="thesis-shared", symbol="XAU",
  )
  claimed = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-shared", setup_id="terminal-thesis-1",
  )
  assert claimed is True
  await transition_setup(client, "terminal-thesis-1", INVALIDATED)

  await finalize_terminal_setup(
    client, "terminal-thesis-1",
    terminal_state=INVALIDATED, reason_code="structure_broke", source="test",
  )

  # The thesis is now free for a different setup to claim.
  reclaimed = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-shared", setup_id="terminal-thesis-2",
  )
  assert reclaimed is True


@pytest.mark.asyncio
async def test_finalize_terminal_setup_is_a_noop_for_a_thesis_it_never_claimed():
  client = redis_state.get_client()
  await create_setup(
    client, setup_id="terminal-thesis-3", thesis_id="thesis-owned-elsewhere",
    symbol="XAU",
  )
  await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-owned-elsewhere",
    setup_id="terminal-thesis-real-owner",
  )
  await transition_setup(client, "terminal-thesis-3", INVALIDATED)

  await finalize_terminal_setup(
    client, "terminal-thesis-3",
    terminal_state=INVALIDATED, reason_code="structure_broke", source="test",
  )

  # A setup that never owned the claim must not be able to evict the real
  # owner via finalize_terminal_setup.
  still_owned = await claim_active_thesis(
    client, symbol="XAU", thesis_id="thesis-owned-elsewhere",
    setup_id="terminal-thesis-real-owner",
  )
  assert still_owned is True


@pytest.mark.asyncio
async def test_finalize_terminal_setup_is_idempotent():
  client = redis_state.get_client()
  past = int(time.time()) - 10
  await create_setup(
    client, setup_id="terminal-idempotent", thesis_id="t1", symbol="XAU",
    expires_at=past,
  )
  await transition_setup(client, "terminal-idempotent", EXPIRED)

  await finalize_terminal_setup(
    client, "terminal-idempotent",
    terminal_state=EXPIRED, reason_code="setup_expired_before_execution",
    source="test",
  )
  await finalize_terminal_setup(
    client, "terminal-idempotent",
    terminal_state=EXPIRED, reason_code="setup_expired_before_execution",
    source="test",
  )
