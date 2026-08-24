import json
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import pytest

from app.autotrade import stats_ingestion
from app.persistence import redis_state, store
from app.signals.reports import build_stats


@pytest.mark.asyncio
async def test_no_tp_archived_without_price_records_stop_distance_loss():
  """Bare 'no TP archived' closes used to vanish from /trade_stats."""
  await store.init_db()
  gid = "v8:loss-no-price"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 10,
    "position_id": 99001,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Key Level Reaction",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 20,
    "position_id": 99001,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "message": "PLAN CLOSED · no TP archived",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 50


@pytest.mark.asyncio
async def test_no_tp_archived_parses_losing_pips_from_message():
  await store.init_db()
  gid = "v8:loss-from-message"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 30,
    "position_id": 99002,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "SELL",
    "setup": "Key Level Reaction",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4055.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 40,
    "position_id": 99002,
    "group_id": gid,
    "candidate_id": gid,
    "message": "PLAN CLOSED · no TP archived · losing -47 pips",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 47


@pytest.mark.asyncio
async def test_highest_tp_then_be_exit_is_not_a_loss():
  """TP booked then residual SL/BE must stay a win (highest TP archived)."""
  await store.init_db()
  gid = "v8:tp-then-be"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 50,
    "position_id": 99003,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Key Level Reaction",
    "symbol": "XAU",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 60,
    "position_id": 99003,
    "group_id": gid,
    "candidate_id": gid,
    # Residual exit below entry would look like a loss if we used price alone.
    "price": 4049.0,
    "target_pips": 41,
    "group_realized_pips": -10,
    "message": "PLAN CLOSED · highest TP archived TP1 · @ 4049.00",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "+"
  assert hit[0]["pips"] == 41


@pytest.mark.asyncio
async def test_deferred_tp_stopout_records_loss_not_false_archive(sql):
  await store.init_db()
  gid = "v8:deferred-tp-stopout"
  await store.record_auto_trade_event({
    "type": "order_filled",
    "timestamp": 70,
    "position_id": 99004,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "setup": "Trend Pullback",
    "symbol": "XAU",
    "price": 4636.98,
    "stop_loss": 4631.04,
    "volume": 600,
  })
  await store.record_auto_trade_event({
    "type": "position_closed",
    "timestamp": 80,
    "position_id": 99004,
    "group_id": gid,
    "candidate_id": gid,
    "direction": "BUY",
    "symbol": "XAU",
    "price": 4630.96,
    "stop_loss": 4631.04,
    "target_pips": 31,
    "group_realized_pips": -60,
    "previous_state": "fully_open",
    "reason_code": "manual_or_external_close",
    "message": "PLAN CLOSED · highest TP archived TP1 · @ 4630.96",
  })

  rows = await store.get_pips_records(0, 10**12)
  hit = [row for row in rows if row["trade_key"] == f"algo:{gid}"]
  assert len(hit) == 1
  assert hit[0]["sign"] == "-"
  assert hit[0]["pips"] == 60
  assert await sql.val(
    "SELECT booked_tp_count FROM auto_trade_results WHERE group_id = $1",
    gid,
  ) == 0


@pytest.mark.asyncio
async def test_startup_backfill_recovers_retained_algo_results(monkeypatch):
  await store.init_db()
  client = redis_state.get_client()
  stream = "auto_trade:test_stats_events"
  install_runtime_overrides(monkeypatch, legacy_overrides={"auto_trade_event_stream": stream})
  fill = {
    "type": "order_filled",
    "timestamp": 100,
    "position_id": 7001,
    "group_id": "v8:stats-recovery",
    "candidate_id": "v8:stats-recovery",
    "stream": "algo_auto",
    "symbol": "XAU",
    "setup": "Key Level Reaction",
    "direction": "BUY",
    "price": 4050.0,
    "stop_loss": 4045.0,
    "volume": 800,
  }
  closed = {
    "type": "position_closed",
    "timestamp": 200,
    "position_id": 7001,
    "group_id": "v8:stats-recovery",
    "candidate_id": "v8:stats-recovery",
    "stream": "algo_auto",
    "symbol": "XAU",
    "direction": "BUY",
    "price": 4051.0,
    "target_pips": 90,
    "message": "PLAN CLOSED · highest TP archived TP3",
  }
  await client.xadd(stream, {"payload": json.dumps(fill)})
  last_id = await client.xadd(stream, {"payload": json.dumps(closed)})

  cursor = await stats_ingestion.backfill_retained_auto_trade_stats(client)
  records = await store.get_pips_records(0, 1_000)
  stats = build_stats(records, [], "UTC", 0, 8, 13)

  assert cursor == str(last_id)
  assert len(records) == 1
  assert records[0]["stream"] == "algo_auto"
  assert records[0]["pips"] == 90
  assert stats["trades"] == 1
  assert stats["by_stream"]["algo_auto"]["total_pips"] == 90

  # A restart reuses the independent stats cursor and remains idempotent.
  assert await stats_ingestion.backfill_retained_auto_trade_stats(client) == cursor
  assert len(await store.get_pips_records(0, 1_000)) == 1

  # If the executor kept publishing while the bot was down, startup catches
  # up everything after the stored cursor before /trade_stats is registered.
  fill_2 = {
    **fill,
    "timestamp": 300,
    "position_id": 7002,
    "group_id": "v8:stats-recovery-2",
    "candidate_id": "v8:stats-recovery-2",
    "direction": "SELL",
    "price": 4060.0,
    "stop_loss": 4065.0,
  }
  closed_2 = {
    **closed,
    "timestamp": 400,
    "position_id": 7002,
    "group_id": "v8:stats-recovery-2",
    "candidate_id": "v8:stats-recovery-2",
    "direction": "SELL",
    "target_pips": 60,
    "message": "PLAN CLOSED · highest TP archived TP2",
  }
  await client.xadd(stream, {"payload": json.dumps(fill_2)})
  latest_id = await client.xadd(stream, {"payload": json.dumps(closed_2)})

  assert (
    await stats_ingestion.backfill_retained_auto_trade_stats(client)
    == str(latest_id)
  )
  recovered = await store.get_pips_records(0, 1_000)
  assert [row["pips"] for row in recovered] == [90, 60]
