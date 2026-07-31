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
    await asyncio.Event().wait()

  async def fast_sleep(seconds):
    sleeps.append(seconds)
    await real_sleep(0)

  async def fake_reset():
    return None

  async def fake_health(**_kwargs):
    return None

  monkeypatch.setattr(redis_state.asyncio, "sleep", fast_sleep)
  monkeypatch.setattr(redis_state, "reset_client", fake_reset)
  monkeypatch.setattr(redis_state, "publish_component_health", fake_health)

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
async def test_run_supervised_does_not_restart_programming_bug(monkeypatch):
  runs = {"n": 0}
  resets = {"n": 0}
  health = []
  alerts = []

  async def buggy_loop():
    runs["n"] += 1
    raise KeyError("missing plan field")

  async def fake_reset():
    resets["n"] += 1

  async def fake_health(**kwargs):
    health.append(kwargs)

  async def fake_alert(**kwargs):
    alerts.append(kwargs)

  monkeypatch.setattr(redis_state, "reset_client", fake_reset)
  monkeypatch.setattr(redis_state, "publish_component_health", fake_health)
  monkeypatch.setattr(redis_state, "_alert_owner_component_fatal", fake_alert)

  with pytest.raises(KeyError, match="missing plan field"):
    await redis_state.run_supervised("buggy_loop", buggy_loop)

  assert runs["n"] == 1
  assert resets["n"] == 0
  assert any(item.get("state") == "fatal" for item in health)
  assert alerts and alerts[0]["component"] == "buggy_loop"


@pytest.mark.asyncio
async def test_fatal_health_has_no_ttl(monkeypatch):
  sets = []

  class FakeClient:
    async def set(self, key, value, ex=None, nx=None):
      sets.append({"key": key, "ex": ex, "nx": nx})
      return True

  monkeypatch.setattr(redis_state, "get_client", lambda: FakeClient())

  await redis_state.publish_component_health(
    component="scanner_loop",
    state="fatal",
    error="KeyError",
  )
  await redis_state.publish_component_health(
    component="scanner_loop",
    state="ready",
  )

  fatal = next(item for item in sets if "fatal" in str(item) or item["ex"] is None)
  # First call is fatal without TTL; second is ready with TTL.
  assert sets[0]["ex"] is None
  assert sets[1]["ex"] == redis_state._COMPONENT_HEALTH_TTL
  assert fatal["ex"] is None


@pytest.mark.asyncio
async def test_run_supervised_does_not_restart_clean_exit(monkeypatch):
  runs = {"n": 0}

  async def disabled_loop():
    runs["n"] += 1

  async def fake_health(**_kwargs):
    return None

  monkeypatch.setattr(redis_state, "publish_component_health", fake_health)

  await redis_state.run_supervised("disabled_loop", disabled_loop)
  assert runs["n"] == 1


def test_is_transient_redis_error_classifies():
  assert redis_state.is_transient_redis_error(
    redis.exceptions.ConnectionError("Name or service not known")
  )
  assert redis_state.is_transient_redis_error(
    redis.exceptions.TimeoutError("Timeout reading from redis")
  )
  assert redis_state.is_transient_redis_error(
    OSError("Error -2 connecting to redis:6379. Name or service not known.")
  )
  assert not redis_state.is_transient_redis_error(KeyError("x"))
  assert not redis_state.is_transient_redis_error(TypeError("bad"))
  assert not redis_state.is_transient_redis_error(
    RuntimeError("broker fill timeout")
  )
  assert not redis_state.is_transient_redis_error(
    ValueError("statement timeout")
  )
  assert not redis_state.is_transient_redis_error(TimeoutError("timed out"))
