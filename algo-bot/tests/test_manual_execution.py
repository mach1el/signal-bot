import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf
from app.persistence import redis_state, store
from app.signals import broadcast, manual_execution
from app.signals.manual_intent import ManualTradeIntent


def _intent(**overrides) -> ManualTradeIntent:
  base = dict(
    intent_id="manual:47:0",
    manual_signal_id=47,
    revision=0,
    direction="SELL",
    symbol="XAU",
    entry_low=4100.0,
    entry_high=4105.0,
    sl=4110.0,
    tps=(4095.0, 4090.0, 4080.0),
    created_at=1_800_000_000,
    expires_at=None,
    setup_type="golden-fib",
    confluence=2,
    execution_mode="algo",
  )
  base.update(overrides)
  return ManualTradeIntent(**base)


# ---------------------------------------------------------------------------
# _intent_to_candidate_payload
# ---------------------------------------------------------------------------

def test_intent_to_candidate_payload_sell_uses_entry_low_reference_edge():
  payload = manual_execution._intent_to_candidate_payload(_intent())

  assert payload["version"] == 3
  assert payload["candidate_id"] == "manual:47:0"
  assert payload["symbol"] == "XAU"
  assert payload["timeframe"] == "M1"
  assert payload["setup"] == "golden-fib"
  assert payload["mode"] == "manual_algo"
  assert payload["direction"] == "SELL"
  assert payload["entry_zone"] == {"low": 4100.0, "high": 4105.0}
  assert payload["manual_stop_loss"] == 4110.0
  assert payload["manual_expires_at"] is None
  assert payload["confluence"] == 2
  # SELL reference edge = entry_low (4100.0, matches pips_format.rr_entry's
  # own SELL -> entry convention): |4100-4095|=5 -> 50p, |4100-4090|=10 ->
  # 100p, |4100-4080|=20 -> 200p.
  assert payload["targets_pips"] == [50, 100, 200]
  assert payload["manual_take_profits"] == [4095.0, 4090.0, 4080.0]
  assert payload["group_id"] == "manual:47:0"
  assert payload["strategy_family"] == "manual"
  assert payload["parent_group_id"] is None
  assert payload["current_price"] == pytest.approx(4100.0)
  assert payload["key_level"] == pytest.approx(4100.0)


def test_intent_to_candidate_payload_buy_uses_entry_high_reference_edge():
  payload = manual_execution._intent_to_candidate_payload(_intent(
    direction="BUY",
    entry_low=1999.5,
    entry_high=2000.5,
    sl=1994.0,
    tps=(2010.0, 2020.0),
    setup_type=None,
    confluence=None,
  ))

  assert payload["direction"] == "BUY"
  # Untagged manual signals default confluence to 1, exempt from the
  # global MinConfluence gate on the C# side (see AutoTradeEngine.cs).
  assert payload["confluence"] == 1
  # BUY reference edge = entry_high (2000.5): |2000.5-2010|=9.5 -> 95p,
  # |2000.5-2020|=19.5 -> 195p.
  assert payload["targets_pips"] == [95, 195]
  assert payload["current_price"] == pytest.approx(2000.5)
  assert payload["key_level"] == pytest.approx(2000.5)


def test_intent_to_candidate_payload_never_emits_zero_or_negative_pips():
  # A TP exactly at the reference edge would otherwise round to 0, which
  # AutoTradeEngine.cs's manual-algo target-contract validation rejects.
  payload = manual_execution._intent_to_candidate_payload(_intent(
    direction="SELL",
    entry_low=4100.0,
    entry_high=4105.0,
    tps=(4100.02,),
  ))

  assert payload["targets_pips"] == [1]


# ---------------------------------------------------------------------------
# bridge_intents_loop / _process_intent_entries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_intent_entries_publishes_candidate_shaped_payload(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream_maxlen": 100})
  client = redis_state.get_client()
  intent_payload = {
    "intent_id": "manual:5:0",
    "manual_signal_id": 5,
    "revision": 0,
    "direction": "SELL",
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "sl": 4110.0,
    "tps": [4095.0, 4090.0, 4080.0],
    "created_at": 1_800_000_000,
    "expires_at": None,
    "setup_type": "golden-fib",
    "confluence": 2,
    "execution_mode": "algo",
  }
  entries = [("101-0", {"payload": json.dumps(intent_payload)})]

  cursor = await manual_execution._process_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "101-0"
  candidates = await client.xrange("auto_trade:test")
  assert len(candidates) == 1
  candidate = json.loads(candidates[0][1]["payload"])
  assert candidate["candidate_id"] == "manual:5:0"
  assert candidate["mode"] == "manual_algo"
  assert candidate["manual_stop_loss"] == 4110.0
  assert candidate["targets_pips"] == [50, 100, 200]
  assert await client.get(manual_execution._INTENT_BRIDGE_CURSOR_KEY) == "101-0"


@pytest.mark.asyncio
async def test_process_intent_entries_skips_malformed_payload_but_advances_cursor(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test2"})
  client = redis_state.get_client()
  entries = [("55-0", {"payload": "not json"})]

  cursor = await manual_execution._process_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "55-0"
  assert await client.xrange("auto_trade:test2") == []


@pytest.mark.asyncio
async def test_bridge_intents_loop_is_a_no_op_when_disabled():
  # manual_algo_enabled defaults False and conftest doesn't override it.
  await asyncio.wait_for(manual_execution.bridge_intents_loop(), timeout=2)


@pytest.mark.asyncio
async def test_reconcile_events_loop_is_a_no_op_when_disabled():
  await asyncio.wait_for(manual_execution.reconcile_events_loop(), timeout=2)


@pytest.mark.asyncio
@pytest.mark.no_database
async def test_manual_intent_bypasses_worker_strategy_gates(
  monkeypatch,
):
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream": "auto_trade:test3"})
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_stream_maxlen": 100})
  install_runtime_overrides(monkeypatch, legacy_overrides={"telegram_owner_id": 4242})
  sent = AsyncMock()
  monkeypatch.setattr(manual_execution, "send_scanner_with_retry", sent)
  client = redis_state.get_client()
  intent_payload = {
    "intent_id": "manual:9:0",
    "manual_signal_id": 9,
    "revision": 0,
    "direction": "BUY",
    "entry_low": 4116.5,
    "entry_high": 4117.0,
    "sl": 4111.5,
    "tps": [4130.0],
    "created_at": 1_800_000_000,
    "expires_at": None,
    "setup_type": None,
    "confluence": 1,
    "execution_mode": "algo",
  }
  entries = [("201-0", {"payload": json.dumps(intent_payload)})]

  cursor = await manual_execution._process_intent_entries(
    client, entries, cursor="0-0",
  )

  assert cursor == "201-0"
  candidates = await client.xrange("auto_trade:test3")
  assert len(candidates) == 1
  candidate = json.loads(candidates[0][1]["payload"])
  assert candidate["mode"] == "manual_algo"
  assert candidate["bypass_analysis_gates"] is True
  sent.assert_not_awaited()


# ---------------------------------------------------------------------------
# reconcile_events_loop / _handle_event
# ---------------------------------------------------------------------------

async def _algo_signal(**overrides) -> int:
  """Create a real, algo-armed manual_signals row with a VIP post attached
  so fanout_update actually has somewhere to send its update.
  """
  await store.init_db()
  base = dict(
    ts=1_800_000_000,
    action="SELL",
    entry=4100.0,
    entry_end=4105.0,
    sl=4110.0,
    tps=[4095.0, 4090.0, 4080.0],
    execution_mode="algo",
  )
  base.update(overrides)
  rec = await store.store_manual_signal(**base)
  await store.set_execution_intent(
    rec["id"], intent_id=f"manual:{rec['id']}:0", status="armed", revision=0,
  )
  await store.insert_signal_post(
    rec["id"], runtime_config.delivery.telegram.telegram_channel_id, 9000 + rec["id"], "vip",
  )
  return rec["id"]


def _mock_send(monkeypatch) -> AsyncMock:
  send = AsyncMock(return_value=SimpleNamespace(message_id=99999))
  monkeypatch.setattr(broadcast, "_send_message", send)
  return send


@pytest.mark.asyncio
async def test_handle_event_fill_marks_filled_records_broker_fields_and_activates(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_algo_owner_execution_dm_enabled": True})
  sid = await _algo_signal()
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "setup": "Manual Algo",
    "stream": "algo_manual",
    "price": 4100.5,
    "volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  assert positions[555] == sid
  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "filled"
  assert row["broker_position_id"] == "555"
  assert row["broker_fill_price"] == pytest.approx(4100.5)
  assert row["algo_armed"] is True
  assert row["fill_state"] == "filled"
  send.assert_awaited_once()
  truth.assert_awaited_once()


@pytest.mark.asyncio
async def test_limit_placed_event_is_the_first_broker_confirmation(monkeypatch):
  sid = await _algo_signal()
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_algo_owner_execution_dm_enabled": True})
  event = {
    "type": "manual_limit_placed",
    "stream": "algo_manual",
    "candidate_id": f"manual:{sid}:0",
    "direction": "SELL",
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "stop_loss": 4110.0,
    "target_prices": [4095.0, 4090.0, 4080.0],
    "order_id": 777,
  }

  await manual_execution._handle_event(
    redis_state.get_client(), event, {},
  )

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "pending"
  truth.assert_awaited_once()
  text = truth.await_args.args[0]
  assert "LIMIT ORDER PLACED" in text
  assert "777" in text
  assert f"manual:{sid}:0" in text


@pytest.mark.asyncio
async def test_limit_placed_owner_dm_is_off_by_default(monkeypatch):
  """The owner-only "LIMIT ORDER PLACED" debug DM duplicates the real VIP/
  public channel update the executor's own fill event already posts - it
  must stay silent unless explicitly re-enabled."""
  sid = await _algo_signal()
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  event = {
    "type": "manual_limit_placed",
    "stream": "algo_manual",
    "candidate_id": f"manual:{sid}:0",
    "direction": "SELL",
    "entry_low": 4100.0,
    "entry_high": 4105.0,
    "stop_loss": 4110.0,
    "target_prices": [4095.0, 4090.0, 4080.0],
    "order_id": 777,
  }

  await manual_execution._handle_event(
    redis_state.get_client(), event, {},
  )

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "pending"
  truth.assert_not_awaited()


@pytest.mark.asyncio
async def test_fill_event_owner_dm_is_off_by_default(monkeypatch):
  """The owner-only "POSITION OPENED" debug DM is noise on top of the real
  "🟢 active" channel update trade_ops.do_active/post_result already sends
  for the same fill - must stay silent unless explicitly re-enabled."""
  send = _mock_send(monkeypatch)
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  sid = await _algo_signal()
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "setup": "Manual Algo",
    "stream": "algo_manual",
    "price": 4100.5,
    "volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "filled"
  truth.assert_not_awaited()
  # The real subscriber-facing channel update must still fire unchanged.
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_rejection_reports_machine_reason(monkeypatch):
  sid = await _algo_signal()
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  event = {
    "type": "rejected",
    "stream": "algo_manual",
    "candidate_id": f"manual:{sid}:0",
    "reason_code": "broker_account_not_hedged_for_opposite_manual_order",
  }

  await manual_execution._handle_event(
    redis_state.get_client(), event, {},
  )

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "rejected"
  assert row["execution_error"] == (
    "broker_account_not_hedged_for_opposite_manual_order"
  )
  assert "ORDER REJECTED" in truth.await_args.args[0]
  assert "No broker order submitted" in truth.await_args.args[0]


@pytest.mark.asyncio
async def test_handle_event_skips_opened_event_without_manual_algo_setup(monkeypatch):
  send = _mock_send(monkeypatch)
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_opened",
    "position_id": 888,
    "candidate_id": "manual:1:0",
    "setup": "Box Breakout",
  }
  await manual_execution._handle_event(client, event, positions)

  assert positions == {}
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_skips_events_for_unknown_positions(monkeypatch):
  send = _mock_send(monkeypatch)
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "take_profit", "position_id": 777, "price": 4001.0, "target_pips": 30,
  }
  await manual_execution._handle_event(client, event, positions)

  send.assert_not_awaited()
  assert positions == {}


@pytest.mark.asyncio
async def test_handle_event_take_profit_books_equal_weight_partial_leg(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  # Configured targets [50, 100, 200]p; 50 is not the max -> partial 1/3.
  event = {"type": "take_profit", "position_id": 555, "price": 4095.0, "target_pips": 50}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  # _decode_signal already deserializes legs from its stored JSON text.
  legs = row["legs"]
  assert len(legs) == 1
  assert legs[0]["frac"] == pytest.approx(1 / 3, rel=1e-3)
  assert legs[0]["pips"] == 50
  send.assert_awaited_once()
  assert "TP1" in send.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_event_take_profit_closes_in_full_on_last_configured_target(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  # 200 is the max configured target pip distance -> full close (frac=None),
  # even though this is the ladder's FIRST take_profit event for this
  # signal - proving finality is judged against the configured ladder, not
  # an event-count.
  event = {"type": "take_profit", "position_id": 555, "price": 4080.0, "target_pips": 200}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert row["result_pips"] == 200
  send.assert_awaited_once()
  assert "TP3" in send.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_event_position_closed_full_close_defers_to_group_result(
  monkeypatch,
):
  """A leg closing completely (remaining_volume=0) must NOT finalize the
  signal by itself - a manual /algo signal can be several independent
  entry legs sharing one group, and only AutoTradeEngine.cs's own
  group_result event (fired once every leg is done) knows the true,
  correctly volume-weighted final result. See finalize_manual_group.
  """
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # SELL entry=4100/4105 sl=4110
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {
    "type": "position_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4110.0,
    "remaining_volume": 0,
    "leg_realized_pips": -100,
    "volume": 1000,
    "group_initial_volume": 1000,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert 555 not in positions
  send.assert_not_awaited()

  group_event = {
    "type": "group_result",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "group_realized_pips": -100,
  }
  await manual_execution._handle_event(client, group_event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert row["result_pips"] == -100
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_position_closed_partial_close_uses_leg_fields_not_broker_fill(
  monkeypatch,
):
  """A genuine partial close (this leg itself still has volume left, e.g.
  a broker-side liquidity partial-fill on an owner flatten) is still
  booked immediately via the existing close_leg ledger - but from the
  event's OWN leg_realized_pips/volume/group_initial_volume, not the
  whole-signal broker_fill_price (which, for a multi-leg group, is only
  ever the FIRST leg to fill and would give the wrong number here).
  """
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # SELL entry zone 4100-4105
  # Deliberately a DIFFERENT price than the leg's own fill, to prove the
  # partial-close pips come from the event's leg_realized_pips, not a
  # recompute against this stale, whole-signal column.
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4103.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {
    "type": "position_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4110.0,
    "remaining_volume": 400,
    "leg_realized_pips": -70,
    "volume": 600,
    "group_initial_volume": 1000,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  legs = row["legs"]
  assert legs[0]["pips"] == -70
  assert legs[0]["frac"] == pytest.approx(0.6)
  assert 555 in positions
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_take_profit_uses_broker_fill_for_leg_pips(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  sig = await store.get_manual_signal(sid)
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4104.0)
  client = redis_state.get_client()
  positions = {555: sid}
  event = {
    "type": "take_profit",
    "position_id": 555,
    "price": 4095.0,
    "target_pips": 50,
  }
  await manual_execution._handle_event(client, event, positions)
  # From fill 4104 → 4095 = +90 pips (zone-edge math would be +50).
  assert send.await_count >= 1
  text = send.call_args.args[0]
  assert "+90 pips" in text
  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert row["legs"][0]["pips"] == 90



@pytest.mark.asyncio
async def test_handle_event_position_closed_without_price_marks_error_not_silent(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {"type": "position_closed", "position_id": 555, "price": None}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert row["execution_status"] == "error"
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_manual_closed_applies_owner_requested_fraction(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}
  await manual_execution.request_close(sid, f"manual:{sid}:0", frac=0.5)

  # This leg still has volume left (a genuine partial /trade_close 50%) -
  # its own leg_realized_pips/volume/group_initial_volume drive the ledger
  # entry, matching the "actual executed fraction" AutoTradeEngine.cs
  # reports rather than remembering the originally-requested one.
  event = {
    "type": "manual_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4095.0,
    "remaining_volume": 300,
    "leg_realized_pips": 50,
    "volume": 300,
    "group_initial_volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  legs = row["legs"]
  assert legs[0]["frac"] == pytest.approx(0.5)
  assert legs[0]["pips"] == 50
  assert row["status"] == "open"
  assert 555 in positions
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_closed_full_close_defers_to_group_result(
  monkeypatch,
):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}
  await manual_execution.request_close(sid, f"manual:{sid}:0", frac=None)

  event = {
    "type": "manual_closed",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4095.0,
    "remaining_volume": 0,
    "leg_realized_pips": 50,
    "volume": 600,
    "group_initial_volume": 600,
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert 555 not in positions
  send.assert_not_awaited()

  group_event = {
    "type": "group_result",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "group_realized_pips": 50,
  }
  await manual_execution._handle_event(client, group_event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert row["result_pips"] == 50
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_sl_moved_updates_stop(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {"type": "manual_sl_moved", "position_id": 555, "price": 4108.0}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["sl"] == pytest.approx(4108.0)
  send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_event_manual_cancelled_cancels_armed_signal(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()  # armed, never filled
  await store.insert_signal_post(sid, -100987654321, 9900 + sid, "public")
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {"type": "manual_cancelled", "candidate_id": f"manual:{sid}:0"}
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "cancelled"
  assert row["execution_status"] == "cancelled"
  assert row["algo_armed"] is False
  # Broker-confirmed cancel is a real signal lifecycle result and replies to
  # both persisted VIP + public roots.
  assert send.await_count == 2
  texts = [call.args[0] for call in send.await_args_list]
  assert all("cancelled" in text for text in texts)
  assert any(f"#{sid}" in text for text in texts)
  assert any(f"#{sid}" not in text for text in texts)


@pytest.mark.asyncio
async def test_handle_event_manual_cancelled_hard_deletes_when_pending_delete(
  monkeypatch,
):
  # /trade_delete flagged the intent — broker cancel must remove the row
  # and posts (🗑 deleted), not leave ❌ cancelled.
  _mock_send(monkeypatch)
  truth = AsyncMock()
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)
  monkeypatch.setattr(broadcast, "delete_posts", AsyncMock())
  sid = await _algo_signal()
  await store.insert_signal_post(sid, -100987654321, 9900 + sid, "vip")
  await store.insert_signal_post(sid, -100987654322, 9800 + sid, "public")
  intent = f"manual:{sid}:0"
  client = redis_state.get_client()
  await manual_execution.mark_pending_delete(intent)

  await manual_execution._handle_event(
    client,
    {"type": "manual_cancelled", "candidate_id": intent},
    {},
  )

  assert await store.get_manual_signal(sid) is None
  truth.assert_awaited_once()
  assert "deleted" in truth.await_args.args[0]
  assert "cancelled" not in truth.await_args.args[0]
  assert await client.get(f"manual_trade:pending_delete:{intent}") is None


@pytest.mark.asyncio
async def test_handle_event_manual_expired_releases_watcher_ownership(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  client = redis_state.get_client()

  await manual_execution._handle_event(
    client,
    {"type": "manual_expired", "candidate_id": f"manual:{sid}:0"},
    {},
  )

  row = await store.get_manual_signal(sid)
  assert row["status"] == "open"
  assert row["execution_status"] == "expired"
  assert row["algo_armed"] is False
  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_stop_moved_fans_out_manual_algo_be_move(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions = {555: sid}

  event = {
    "type": "stop_moved",
    "position_id": 555,
    "candidate_id": f"manual:{sid}:0",
    "price": 4103.06,
    "message": "🛡 ApexVoid Algo stop → 4,103.06 (BE+6 ticks)",
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["sl"] == pytest.approx(4103.06)
  send.assert_awaited_once()
  text = send.await_args.args[0]
  assert "move SL to" in text


@pytest.mark.asyncio
async def test_handle_event_stop_moved_ignores_autonomous_positions(monkeypatch):
  send = _mock_send(monkeypatch)
  client = redis_state.get_client()

  event = {"type": "stop_moved", "position_id": 999, "price": 4108.0}
  await manual_execution._handle_event(client, event, {})

  send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_event_resolves_signal_via_candidate_id_after_cache_miss(
  monkeypatch,
):
  # Simulates a process restart: the in-memory position_id->signal_id cache
  # is empty, but the event still carries candidate_id, which self-heals
  # the mapping via the persisted broker_position_id on the signal row.
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  await store.set_execution_fill(sid, broker_position_id=555, broker_fill_price=4100.0)
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "take_profit",
    "position_id": 555,
    "price": 4080.0,
    "target_pips": 200,
    "candidate_id": f"manual:{sid}:0",
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["status"] == "closed"
  assert positions[555] == sid


@pytest.mark.asyncio
async def test_handle_event_command_error_marks_execution_status_error(monkeypatch):
  send = _mock_send(monkeypatch)
  sid = await _algo_signal()
  client = redis_state.get_client()
  positions: dict[int, int] = {}

  event = {
    "type": "manual_command_error",
    "candidate_id": f"manual:{sid}:0",
    "message": "cancel requested but no pending order found",
  }
  await manual_execution._handle_event(client, event, positions)

  row = await store.get_manual_signal(sid)
  assert row["execution_status"] == "error"
  assert "no pending order" in row["execution_error"]
  send.assert_not_awaited()


# ---------------------------------------------------------------------------
# request_cancel / request_close / request_move_sl
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_cancel_xadds_cancel_pending_command(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_command_stream": "manual_trade:cmd1",})
  client = redis_state.get_client()

  await manual_execution.request_cancel("manual:9:0")

  entries = await client.xrange("manual_trade:cmd1")
  assert len(entries) == 1
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "cancel_pending", "intent_id": "manual:9:0"}


@pytest.mark.asyncio
async def test_request_close_xadds_close_command_by_intent_id(monkeypatch):
  # Routed by intent_id (the signal's group token), not a single
  # position_id - AutoTradeEngine.cs resolves and closes every open leg in
  # the group, so /trade_close actually flattens a multi-leg manual /algo
  # position instead of leaving two of three legs open on the broker.
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_command_stream": "manual_trade:cmd2",})
  client = redis_state.get_client()

  await manual_execution.request_close(9, "manual:9:0", frac=0.5)

  entries = await client.xrange("manual_trade:cmd2")
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "close", "intent_id": "manual:9:0", "frac": 0.5}


@pytest.mark.asyncio
async def test_request_move_sl_xadds_move_sl_command(monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"manual_trade_command_stream": "manual_trade:cmd3",})
  client = redis_state.get_client()

  await manual_execution.request_move_sl(9, 555, 4108.5)

  entries = await client.xrange("manual_trade:cmd3")
  payload = json.loads(entries[0][1]["payload"])
  assert payload == {"type": "move_sl", "position_id": 555, "price": 4108.5}
