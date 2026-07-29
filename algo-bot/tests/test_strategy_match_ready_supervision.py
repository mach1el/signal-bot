"""P0-11: strategy_match_ready_loop must survive a transient failure in
ensure_ready_group instead of dying permanently as a silent fire-and-forget
task, and must persist its health so /auto_status can distinguish a down
consumer from "genuinely nothing to do."
"""

from __future__ import annotations

import asyncio

import pytest

from app.autotrade import worker
from app.autotrade.strategy_match_ready import load_ready_consumer_health
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_transient_ensure_ready_group_failure_recovers_to_ready(
  monkeypatch,
):
  client = redis_state.get_client()
  monkeypatch.setattr(worker.settings, "auto_trade_enabled", True)

  attempts = {"n": 0}
  real_ensure_ready_group = worker.ensure_ready_group

  async def flaky_ensure_ready_group(client_):
    attempts["n"] += 1
    if attempts["n"] == 1:
      raise ConnectionError("simulated transient redis failure")
    await real_ensure_ready_group(client_)

  monkeypatch.setattr(worker, "ensure_ready_group", flaky_ensure_ready_group)

  sleep_calls = []
  real_sleep = asyncio.sleep

  async def fast_sleep(seconds):
    sleep_calls.append(seconds)
    await real_sleep(0)

  monkeypatch.setattr(worker.asyncio, "sleep", fast_sleep)

  async def fake_consume(**_kwargs):
    await real_sleep(0)
    return False

  monkeypatch.setattr(worker, "_consume_strategy_match_ready_once", fake_consume)

  task = asyncio.create_task(worker.strategy_match_ready_loop())
  try:
    for _ in range(500):
      health = await load_ready_consumer_health(client)
      if (
        health is not None
        and health.get("state") == "ready"
        and attempts["n"] >= 2
      ):
        break
      await real_sleep(0)
    else:
      pytest.fail("consumer never recovered to a ready state")
  finally:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task

  assert attempts["n"] >= 2, (
    "ensure_ready_group must have been retried after the transient failure"
  )
  assert sleep_calls, "expected a backoff sleep after the transient failure"

  final_health = await load_ready_consumer_health(client)
  assert final_health is not None
  assert final_health["state"] == "ready"
  assert final_health["consumer"]
  assert final_health["stream"] == worker.READY_STREAM
  assert final_health["group"] == worker.READY_GROUP
