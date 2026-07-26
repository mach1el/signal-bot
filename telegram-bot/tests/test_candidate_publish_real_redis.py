"""Production Lua integration tests.

Set ``REAL_REDIS_URL`` to a disposable Redis database. These tests never use
the FakeRedis compatibility fallback.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.autotrade.candidate_execution_state import (
  parse_candidate_execution_record,
  serialize_candidate_execution_record,
  STATE_PROCESSING,
  STATE_PUBLISHED,
)
from app.autotrade.candidate_publish import (
  autonomous_cycle_owner_key,
  candidate_key,
  candidate_stream_event_key,
  publish_candidate_atomic,
  reconcile_candidate_publication,
)


pytestmark = [pytest.mark.no_database, pytest.mark.real_redis]


@pytest_asyncio.fixture
async def real_redis():
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.fail("REAL_REDIS_URL is required for production Lua tests")
  client = Redis.from_url(url, decode_responses=True)
  await client.ping()
  await client.flushdb()
  try:
    yield client
  finally:
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_real_lua_same_candidate_has_one_stream_event(real_redis):
  results = await asyncio.gather(*[
    publish_candidate_atomic(
      real_redis,
      stream="test:lua:candidate",
      candidate_id="candidate-one",
      payload='{"candidate_id":"candidate-one"}',
      ttl=300,
      maxlen=100,
    )
    for _ in range(12)
  ])

  assert sum(result.published for result in results) == 1
  assert await real_redis.xlen("test:lua:candidate") == 1
  assert not getattr(
    real_redis, "_apexvoid_allow_non_atomic_test_fallback", False,
  )


@pytest.mark.asyncio
async def test_real_lua_cycle_claim_preserves_one_winner(real_redis):
  owner_key = autonomous_cycle_owner_key("XAU", "m1-1000")
  results = await asyncio.gather(*[
    publish_candidate_atomic(
      real_redis,
      stream="test:lua:cycle",
      candidate_id=f"cycle-{index}",
      payload=json.dumps({"candidate_id": f"cycle-{index}"}),
      ttl=300,
      maxlen=100,
      ownership_key=owner_key,
      ownership_payload=f"cycle-{index}",
      ownership_ttl=300,
    )
    for index in range(12)
  ])

  winner = next(result for result in results if result.published)
  event = (await real_redis.xrange(
    "test:lua:cycle", min=winner.event_id, max=winner.event_id,
  ))[0]
  payload = json.loads(event[1]["payload"])
  assert await real_redis.xlen("test:lua:cycle") == 1
  assert await real_redis.get(owner_key) == payload["candidate_id"]


@pytest.mark.asyncio
async def test_real_lua_reaction_and_thesis_match_stream_winner(real_redis):
  owner_key = autonomous_cycle_owner_key("XAU", "claim-cycle")
  results = await asyncio.gather(*[
    publish_candidate_atomic(
      real_redis,
      stream="test:lua:claims",
      candidate_id=f"claims-{index}",
      payload=json.dumps({"candidate_id": f"claims-{index}"}),
      ttl=300,
      maxlen=100,
      reaction_key="test:reaction:one",
      reaction_payload=json.dumps({"candidate_id": f"claims-{index}"}),
      reaction_ttl=300,
      thesis_key="test:thesis:one",
      thesis_payload=json.dumps({"candidate_id": f"claims-{index}"}),
      thesis_ttl=300,
      ownership_key=owner_key,
      ownership_payload=f"claims-{index}",
      ownership_ttl=300,
    )
    for index in range(12)
  ])

  winner = next(
    f"claims-{index}"
    for index, result in enumerate(results)
    if result.published
  )
  assert await real_redis.xlen("test:lua:claims") == 1
  assert json.loads(await real_redis.get(
    "test:reaction:one",
  ))["candidate_id"] == winner
  assert json.loads(await real_redis.get(
    "test:thesis:one",
  ))["candidate_id"] == winner
  assert await real_redis.get(owner_key) == winner


class _EvalProxy:
  def __init__(self, client, result):
    self.client = client
    self.result = result

  def __getattr__(self, name):
    return getattr(self.client, name)

  async def eval(self, *args, **kwargs):
    if isinstance(self.result, Exception):
      raise self.result
    return self.result


@pytest.mark.asyncio
async def test_production_proxy_fails_closed_when_eval_unavailable(real_redis):
  result = await publish_candidate_atomic(
    _EvalProxy(real_redis, RuntimeError("EVAL unavailable")),
    stream="test:lua:unavailable",
    candidate_id="eval-unavailable",
    payload="{}",
    ttl=300,
    maxlen=100,
  )

  assert result.status == "atomic_publish_unavailable"
  assert await real_redis.xlen("test:lua:unavailable") == 0


@pytest.mark.asyncio
async def test_invalid_lua_result_marks_readiness_false(real_redis):
  result = await publish_candidate_atomic(
    _EvalProxy(real_redis, ["not-a-code"]),
    stream="test:lua:invalid",
    candidate_id="invalid-result",
    payload="{}",
    ttl=300,
    maxlen=100,
  )

  assert result.status == "atomic_publish_unavailable"
  readiness = json.loads(await real_redis.get(
    "auto_trade:publication_readiness",
  ))
  assert readiness["ready"] is False
  assert readiness["reason_code"] == "atomic_publish_invalid_response"


@pytest.mark.asyncio
async def test_orphan_event_reconciles_without_second_xadd(real_redis):
  owner_key = autonomous_cycle_owner_key("XAU", "m1-orphan")
  result = await publish_candidate_atomic(
    real_redis,
    stream="test:lua:orphan",
    candidate_id="orphan-candidate",
    payload='{"candidate_id":"orphan-candidate"}',
    ttl=300,
    maxlen=100,
    reaction_key="test:orphan:reaction",
    reaction_payload='{"candidate_id":"orphan-candidate"}',
    reaction_ttl=300,
    thesis_key="test:orphan:thesis",
    thesis_payload='{"candidate_id":"orphan-candidate"}',
    thesis_ttl=300,
    ownership_key=owner_key,
    ownership_payload="orphan-candidate",
    ownership_ttl=300,
  )
  assert result.published
  await real_redis.delete(
    candidate_key("orphan-candidate"),
    candidate_stream_event_key("orphan-candidate"),
    "test:orphan:reaction",
    "test:orphan:thesis",
    owner_key,
  )

  recovered = await reconcile_candidate_publication(
    real_redis,
    stream="test:lua:orphan",
    event_id=result.event_id,
  )

  assert recovered.published
  assert recovered.status == "reconciled"
  assert await real_redis.xlen("test:lua:orphan") == 1
  assert await real_redis.get(
    candidate_stream_event_key("orphan-candidate")
  ) == result.event_id
  assert await real_redis.get(owner_key) == "orphan-candidate"


@pytest.mark.asyncio
async def test_real_lua_publish_writes_structured_candidate_record(real_redis):
  result = await publish_candidate_atomic(
    real_redis,
    stream="test:lua:structured",
    candidate_id="structured-candidate",
    payload='{"candidate_id":"structured-candidate"}',
    ttl=300,
    maxlen=100,
  )
  assert result.published
  record = parse_candidate_execution_record(
    await real_redis.get(candidate_key("structured-candidate"))
  )
  assert record.state == STATE_PUBLISHED
  assert record.candidate_id == "structured-candidate"
  assert record.stream_event_id == result.event_id


@pytest.mark.asyncio
async def test_real_lua_reconcile_preserves_executor_progress(real_redis):
  result = await publish_candidate_atomic(
    real_redis,
    stream="test:lua:progress",
    candidate_id="progress-candidate",
    payload='{"candidate_id":"progress-candidate"}',
    ttl=300,
    maxlen=100,
  )
  assert result.published
  progressed = serialize_candidate_execution_record(
    candidate_id="progress-candidate",
    stream_event_id=result.event_id,
    state=STATE_PROCESSING,
    lease_token="lease-token",
    lease_expires_at=9999999999,
  )
  await real_redis.set(candidate_key("progress-candidate"), progressed, ex=300)
  await real_redis.delete(candidate_stream_event_key("progress-candidate"))

  recovered = await reconcile_candidate_publication(
    real_redis,
    stream="test:lua:progress",
    event_id=result.event_id,
  )

  assert recovered.published
  preserved = parse_candidate_execution_record(
    await real_redis.get(candidate_key("progress-candidate"))
  )
  assert preserved.state == STATE_PROCESSING
  assert preserved.lease_token == "lease-token"
  assert await real_redis.get(
    candidate_stream_event_key("progress-candidate")
  ) == result.event_id
