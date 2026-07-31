"""Redis readiness + supervised restart across Compose recreate blips."""

from __future__ import annotations

import asyncio

import pytest
import redis.exceptions

from app.persistence import redis_state


pytestmark = pytest.mark.no_database


@pytest.mark.asyncio
async def test_wait_until_ready_succeeds_when_ping_works():
  await redis_state.wait_until_ready(timeout_seconds=2)


@pytest.mark.asyncio
async def test_wait_until_ready_retries_then_succeeds(monkeypatch):
  attempts = {"n": 0}

  class Flaky:
    async def ping(self):
      attempts["n"] += 1
      if attempts["n"] < 3:
        raise redis.exceptions.ConnectionError(
          "Error -2 connecting to redis:6379. Name or service not known."
        )
      return True

  monkeypatch.setattr(redis_state, "get_client", lambda: Flaky())

  async def fake_reset():
    return None

  monkeypatch.setattr(redis_state, "reset_client", fake_reset)

  sleeps = []
  real_sleep = asyncio.sleep

  async def fast_sleep(seconds):
    sleeps.append(seconds)
    await real_sleep(0)

  monkeypatch.setattr(redis_state.asyncio, "sleep", fast_sleep)

  await redis_state.wait_until_ready(timeout_seconds=5)

  assert attempts["n"] >= 3
  assert sleeps


@pytest.mark.asyncio
async def test_run_supervised_restarts_after_connection_error(monkeypatch):
  runs = {"n": 0}
  sleeps = []
  real_sleep = asyncio.sleep

  async def flaky_loop():
    runs["n"] += 1
    if runs["n"] == 1:
      raise redis.exceptions.ConnectionError(
        "Error -2 connecting to redis:6379. Name or service not known."
      )
    # Second run stays alive until cancelled.
    await asyncio.Event().wait()

  async def fast_sleep(seconds):
    sleeps.append(seconds)
    await real_sleep(0)

  async def fake_reset():
    return None

  monkeypatch.setattr(redis_state.asyncio, "sleep", fast_sleep)
  monkeypatch.setattr(redis_state, "reset_client", fake_reset)

  task = asyncio.create_task(
    redis_state.run_supervised("flaky_loop", flaky_loop)
  )
  try:
    for _ in range(200):
      if runs["n"] >= 2 and sleeps:
        break
      await real_sleep(0)
    else:
      pytest.fail("supervised loop never restarted after ConnectionError")
  finally:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task

  assert runs["n"] >= 2
  assert sleeps[0] == pytest.approx(1.0)

@pytest.mark.asyncio
async def test_run_supervised_does_not_restart_clean_exit():
  runs = {"n": 0}

  async def disabled_loop():
    runs["n"] += 1

  await redis_state.run_supervised("disabled_loop", disabled_loop)
  assert runs["n"] == 1
