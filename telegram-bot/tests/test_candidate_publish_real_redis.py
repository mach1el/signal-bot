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
  STATE_BROKER_OUTCOME_UNKNOWN,
  STATE_ORDERED,
  STATE_PROCESSING,
  STATE_PUBLISHED,
  STATE_RETRYABLE_ERROR,
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


async def _publish_then_overwrite_record(
  real_redis,
  *,
  stream: str,
  candidate_id: str,
  record,
) -> str:
  """Publish a candidate, then replace the record with executor progress.

  ``record`` is either a literal marker or a callable taking the assigned
  stream event id. The stream-event key is deleted so reconciliation has
  something to restore.
  """

  result = await publish_candidate_atomic(
    real_redis,
    stream=stream,
    candidate_id=candidate_id,
    payload=json.dumps({"candidate_id": candidate_id}),
    ttl=300,
    maxlen=100,
  )
  assert result.published
  body = record(result.event_id) if callable(record) else record
  await real_redis.set(candidate_key(candidate_id), body, ex=300)
  await real_redis.delete(candidate_stream_event_key(candidate_id))
  return result.event_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("state", "lease_token", "outcome", "last_error"),
  [
    (STATE_RETRYABLE_ERROR, None, None, "redis_timeout"),
    (STATE_BROKER_OUTCOME_UNKNOWN, None, None, "broker_response_lost"),
    (STATE_ORDERED, "lease-token", "ordered:pos-1", None),
  ],
)
async def test_real_lua_reconcile_never_rewrites_executor_state(
  real_redis, state, lease_token, outcome, last_error,
):
  candidate_id = f"reconcile-{state}"
  event_id = await _publish_then_overwrite_record(
    real_redis,
    stream="test:lua:states",
    candidate_id=candidate_id,
    record=lambda assigned: serialize_candidate_execution_record(
      candidate_id=candidate_id,
      stream_event_id=assigned,
      state=state,
      lease_token=lease_token,
      lease_expires_at=9999999999 if lease_token else 0,
      outcome=outcome,
      attempt=2,
      last_error=last_error,
    ),
  )

  recovered = await reconcile_candidate_publication(
    real_redis,
    stream="test:lua:states",
    event_id=event_id,
  )

  assert recovered.published
  preserved = parse_candidate_execution_record(
    await real_redis.get(candidate_key(candidate_id))
  )
  assert preserved.state == state
  assert preserved.outcome == outcome
  assert preserved.attempt == 2
  assert preserved.last_error == last_error
  # Only the missing identity key is restored.
  assert await real_redis.get(
    candidate_stream_event_key(candidate_id)
  ) == event_id


@pytest.mark.asyncio
async def test_real_lua_reconcile_refuses_foreign_candidate_identity(
  real_redis,
):
  candidate_id = "identity-candidate"
  event_id = await _publish_then_overwrite_record(
    real_redis,
    stream="test:lua:identity",
    candidate_id=candidate_id,
    record=serialize_candidate_execution_record(
      candidate_id="someone-else",
      stream_event_id="9999-0",
      state=STATE_PROCESSING,
      lease_token="foreign-token",
      lease_expires_at=9999999999,
    ),
  )

  recovered = await reconcile_candidate_publication(
    real_redis,
    stream="test:lua:identity",
    event_id=event_id,
  )

  assert not recovered.published
  assert recovered.status == "reconciliation_conflict"
  # No partial write: the foreign record and the missing key are untouched.
  foreign = parse_candidate_execution_record(
    await real_redis.get(candidate_key(candidate_id))
  )
  assert foreign.candidate_id == "someone-else"
  assert foreign.lease_token == "foreign-token"
  assert await real_redis.get(
    candidate_stream_event_key(candidate_id)
  ) is None


@pytest.mark.asyncio
async def test_real_lua_reconcile_refuses_unknown_legacy_marker(real_redis):
  candidate_id = "legacy-candidate"
  event_id = await _publish_then_overwrite_record(
    real_redis,
    stream="test:lua:legacy",
    candidate_id=candidate_id,
    record="processing_v2",
  )

  recovered = await reconcile_candidate_publication(
    real_redis,
    stream="test:lua:legacy",
    event_id=event_id,
  )

  assert not recovered.published
  assert recovered.status == "reconciliation_conflict"
  assert await real_redis.get(candidate_key(candidate_id)) == "processing_v2"
  assert await real_redis.get(
    candidate_stream_event_key(candidate_id)
  ) is None


@pytest.mark.asyncio
async def test_real_lua_reconcile_decodes_records_carrying_json_punctuation(
  real_redis,
):
  candidate_id = "quoted-candidate"
  # A pattern-matching parser can be fooled by an error string that looks
  # like another field; cjson.decode cannot.
  hostile_error = '","state":"published","candidate_id":"someone-else'
  event_id = await _publish_then_overwrite_record(
    real_redis,
    stream="test:lua:quoted",
    candidate_id=candidate_id,
    record=lambda assigned: serialize_candidate_execution_record(
      candidate_id=candidate_id,
      stream_event_id=assigned,
      state=STATE_RETRYABLE_ERROR,
      last_error=hostile_error,
      attempt=3,
    ),
  )

  recovered = await reconcile_candidate_publication(
    real_redis,
    stream="test:lua:quoted",
    event_id=event_id,
  )

  assert recovered.published
  preserved = parse_candidate_execution_record(
    await real_redis.get(candidate_key(candidate_id))
  )
  assert preserved.state == STATE_RETRYABLE_ERROR
  assert preserved.candidate_id == candidate_id
  assert preserved.last_error == hostile_error
