import pytest

from app.autotrade.candidate_execution_state import (
  candidate_record_compatible,
  candidate_record_is_progressed,
  parse_candidate_execution_record,
  published_candidate_record,
  serialize_candidate_execution_record,
  STATE_BROKER_OUTCOME_UNKNOWN,
  STATE_BROKER_RECONCILING,
  STATE_INTEGRITY_ERROR,
  STATE_ORDERED,
  STATE_PUBLISHED,
  STATE_REJECTED,
  STATE_RETRYABLE_ERROR,
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


def test_retry_record_keeps_identity_attempt_and_last_error():
  # The executor writes this record when it hands a candidate back, and the
  # publisher must read it without mistaking it for a fresh publication.
  raw = serialize_candidate_execution_record(
    candidate_id="candidate-3",
    stream_event_id="3-0",
    state=STATE_RETRYABLE_ERROR,
    attempt=2,
    last_error="redis_timeout",
    updated_at=1720000002,
  )
  record = parse_candidate_execution_record(raw)

  assert record.state == STATE_RETRYABLE_ERROR
  assert record.attempt == 2
  assert record.last_error == "redis_timeout"
  assert record.is_retryable
  assert not record.requires_recovery
  assert not record.is_terminal
  assert candidate_record_is_progressed(record)
  assert candidate_record_compatible(
    record,
    candidate_id="candidate-3",
    stream_event_id="3-0",
  )


def test_broker_unknown_record_is_recovery_required_not_retryable():
  record = parse_candidate_execution_record(
    serialize_candidate_execution_record(
      candidate_id="candidate-4",
      stream_event_id="4-0",
      state=STATE_BROKER_OUTCOME_UNKNOWN,
      attempt=1,
      last_error="broker_response_lost",
      updated_at=1720000003,
    )
  )

  assert record.requires_recovery
  # Never retryable: a plain retry could place a second broker order.
  assert not record.is_retryable
  assert not record.is_terminal
  assert candidate_record_is_progressed(record)


def test_integrity_error_record_is_terminal():
  record = parse_candidate_execution_record(
    serialize_candidate_execution_record(
      candidate_id="candidate-5",
      stream_event_id="5-0",
      state=STATE_INTEGRITY_ERROR,
      updated_at=1720000004,
    )
  )

  assert record.is_terminal
  assert not record.is_retryable
  assert not record.requires_recovery


def test_identity_mismatch_is_never_compatible():
  record = parse_candidate_execution_record(
    serialize_candidate_execution_record(
      candidate_id="candidate-6",
      stream_event_id="6-0",
      state=STATE_RETRYABLE_ERROR,
      updated_at=1720000005,
    )
  )

  assert not candidate_record_compatible(
    record,
    candidate_id="candidate-other",
    stream_event_id="6-0",
  )
  assert not candidate_record_compatible(
    record,
    candidate_id="candidate-6",
    stream_event_id="7-0",
  )


def test_structured_record_missing_candidate_id_is_incompatible():
  raw = (
    '{"version":1,"candidate_id":"","stream_event_id":"8-0",'
    '"state":"processing","updated_at":1720000006}'
  )
  record = parse_candidate_execution_record(raw)
  assert not record.is_legacy
  assert not candidate_record_compatible(
    record,
    candidate_id="candidate-8",
    stream_event_id="8-0",
  )


def test_structured_record_missing_stream_event_id_is_incompatible():
  raw = (
    '{"version":1,"candidate_id":"candidate-9","stream_event_id":"",'
    '"state":"processing","updated_at":1720000007}'
  )
  record = parse_candidate_execution_record(raw)
  assert not record.is_legacy
  assert not candidate_record_compatible(
    record,
    candidate_id="candidate-9",
    stream_event_id="9-0",
  )


def test_legacy_published_remains_compatible():
  record = parse_candidate_execution_record("published")
  assert record.is_legacy
  assert candidate_record_compatible(
    record,
    candidate_id="any",
    stream_event_id="1-0",
  )


@pytest.mark.parametrize(
  "raw",
  ["half-written", "processing_v2", "ordered", "{not json"],
)
def test_unknown_marker_is_rejected_rather_than_treated_as_retryable(raw):
  with pytest.raises(ValueError):
    parse_candidate_execution_record(raw)


def test_broker_reconciling_is_recovery_required_not_retryable():
  # The recovery-active state is structurally separate from `processing`: a
  # crashed recovery worker must never decay into a normal retry.
  record = parse_candidate_execution_record(
    serialize_candidate_execution_record(
      candidate_id="candidate-10",
      stream_event_id="10-0",
      state=STATE_BROKER_RECONCILING,
      attempt=2,
      last_error=STATE_BROKER_OUTCOME_UNKNOWN,
      updated_at=1720000008,
    )
  )

  assert record.requires_recovery
  assert not record.is_retryable
  assert not record.is_terminal
  assert candidate_record_is_progressed(record)


# The shared malformed-record matrix: Python, C# and Redis Lua must all fail
# these closed. A structured record never becomes legacy and never defaults a
# missing version or state.
@pytest.mark.parametrize(
  "raw",
  [
    # missing version
    '{"candidate_id":"candidate-1","stream_event_id":"1-0","state":"published"}',
    # version 0 must not default to 1
    (
      '{"version":0,"candidate_id":"candidate-1","stream_event_id":"1-0",'
      '"state":"published"}'
    ),
    # unsupported future version
    (
      '{"version":2,"candidate_id":"candidate-1","stream_event_id":"1-0",'
      '"state":"published"}'
    ),
    # non-integer version
    (
      '{"version":"one","candidate_id":"candidate-1","stream_event_id":"1-0",'
      '"state":"published"}'
    ),
    # boolean version is not an integer
    (
      '{"version":true,"candidate_id":"candidate-1","stream_event_id":"1-0",'
      '"state":"published"}'
    ),
    # missing state must not default to published
    '{"version":1,"candidate_id":"candidate-1","stream_event_id":"1-0"}',
    # empty state
    (
      '{"version":1,"candidate_id":"candidate-1","stream_event_id":"1-0",'
      '"state":""}'
    ),
    # unknown state
    (
      '{"version":1,"candidate_id":"candidate-1","stream_event_id":"1-0",'
      '"state":"unknown"}'
    ),
    # missing identity keys entirely
    '{"version":1,"state":"published"}',
    # non-string identity types
    (
      '{"version":1,"candidate_id":7,"stream_event_id":"1-0",'
      '"state":"published"}'
    ),
    (
      '{"version":1,"candidate_id":"candidate-1","stream_event_id":7,'
      '"state":"published"}'
    ),
  ],
)
def test_malformed_structured_records_fail_closed(raw):
  with pytest.raises(ValueError):
    parse_candidate_execution_record(raw)


def test_valid_structured_record_parses_with_exact_identity():
  raw = (
    '{"version":1,"candidate_id":"candidate-1","stream_event_id":"1-0",'
    '"state":"published"}'
  )
  record = parse_candidate_execution_record(raw)
  assert not record.is_legacy
  assert record.state == STATE_PUBLISHED
  assert candidate_record_compatible(
    record,
    candidate_id="candidate-1",
    stream_event_id="1-0",
  )


@pytest.mark.parametrize(
  "raw",
  [
    # empty identity strings parse (for classification) but can never be
    # compatible with any concrete candidate.
    (
      '{"version":1,"candidate_id":"","stream_event_id":"1-0",'
      '"state":"published"}'
    ),
    (
      '{"version":1,"candidate_id":"candidate-1","stream_event_id":"",'
      '"state":"published"}'
    ),
    (
      '{"version":1,"candidate_id":null,"stream_event_id":"1-0",'
      '"state":"published"}'
    ),
  ],
)
def test_structured_record_with_blank_identity_is_a_conflict(raw):
  record = parse_candidate_execution_record(raw)
  assert not record.is_legacy
  assert not candidate_record_compatible(
    record,
    candidate_id="candidate-1",
    stream_event_id="1-0",
  )
