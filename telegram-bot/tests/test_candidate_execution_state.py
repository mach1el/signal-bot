from app.autotrade.candidate_execution_state import (
  candidate_record_compatible,
  parse_candidate_execution_record,
  published_candidate_record,
  serialize_candidate_execution_record,
  STATE_ORDERED,
  STATE_PUBLISHED,
  STATE_REJECTED,
)


def test_parse_legacy_plain_published():
  record = parse_candidate_execution_record("published")
  assert record.state == STATE_PUBLISHED
  assert record.legacy_status == "published"


def test_parse_legacy_ordered_and_rejected():
  ordered = parse_candidate_execution_record("ordered:123")
  assert ordered.state == STATE_ORDERED
  assert ordered.outcome == "ordered:123"

  rejected = parse_candidate_execution_record("rejected:stale candidate")
  assert rejected.state == STATE_REJECTED
  assert rejected.outcome == "rejected:stale candidate"


def test_round_trip_structured_record():
  raw = published_candidate_record(
    candidate_id="candidate-1",
    stream_event_id="1-0",
    updated_at=1720000000,
  )
  record = parse_candidate_execution_record(raw)
  assert record.candidate_id == "candidate-1"
  assert record.stream_event_id == "1-0"
  assert record.state == STATE_PUBLISHED
  assert candidate_record_compatible(
    record,
    candidate_id="candidate-1",
    stream_event_id="1-0",
  )


def test_serialize_processing_record():
  raw = serialize_candidate_execution_record(
    candidate_id="candidate-2",
    stream_event_id="2-0",
    state="processing",
    lease_token="token",
    lease_expires_at=999,
    updated_at=1720000001,
  )
  record = parse_candidate_execution_record(raw)
  assert record.lease_token == "token"
  assert record.lease_expires_at == 999
