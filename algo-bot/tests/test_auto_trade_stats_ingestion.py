import json

import pytest

from app.autotrade import stats_ingestion
from app.persistence import redis_state, store
from app.signals.reports import build_stats


@pytest.mark.asyncio
async def test_startup_backfill_recovers_retained_algo_results(monkeypatch):
  await store.init_db()
  client = redis_state.get_client()
  stream = "auto_trade:test_stats_events"
  monkeypatch.setattr(stats_ingestion.settings, "auto_trade_event_stream", stream)
  fill = {
    "type": "order_filled",
    "timestamp": 100,
    "position_id": 7001,
    "group_id": "v7:stats-recovery",
    "candidate_id": "v7:stats-recovery",
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
    "group_id": "v7:stats-recovery",
    "candidate_id": "v7:stats-recovery",
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
    "group_id": "v7:stats-recovery-2",
    "candidate_id": "v7:stats-recovery-2",
    "direction": "SELL",
    "price": 4060.0,
    "stop_loss": 4065.0,
  }
  closed_2 = {
    **closed,
    "timestamp": 400,
    "position_id": 7002,
    "group_id": "v7:stats-recovery-2",
    "candidate_id": "v7:stats-recovery-2",
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
