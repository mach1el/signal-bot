import json
import time

import pytest

from app.persistence import redis_state, store
from app.signals import manual_algo_chart as chart


async def _seed_bar(tf: str, ts: int, o: float, h: float, l: float, c: float) -> None:
  client = redis_state.get_client()
  payload = json.dumps({"t": ts, "o": o, "h": h, "l": l, "c": c, "v": 12})
  await client.zadd(f"bars:XAU:{tf}", {payload: ts})


@pytest.mark.asyncio
async def test_issue_fill_close_persist_ohlc_windows(sql):
  await store.init_db()
  issued_at = int(time.time()) - 3600
  await _seed_bar("M1", issued_at - 60, 4338, 4341, 4337, 4340)
  await _seed_bar("M5", issued_at - 300, 4335, 4342, 4334, 4339)
  await _seed_bar("M15", issued_at - 900, 4330, 4345, 4328, 4338)
  await _seed_bar("H1", issued_at - 3600, 4320, 4350, 4318, 4337)

  rec = await store.store_manual_signal(
    issued_at, "SELL", 4388.0, 4391.0, 4397.0, [4385.0],
  )
  issued_rows = await sql.fetch(
    "SELECT event, timeframe, jsonb_array_length(bars) AS n "
    "FROM manual_algo_charts WHERE signal_id = $1 ORDER BY timeframe",
    rec["id"],
  )
  assert {(row["event"], row["timeframe"], row["n"]) for row in issued_rows} == {
    ("issued", "H1", 1),
    ("issued", "M1", 1),
    ("issued", "M15", 1),
    ("issued", "M5", 1),
  }

  now = int(time.time())
  await _seed_bar("M1", now - 60, 4389, 4390, 4388, 4388.5)
  await store.set_execution_fill(rec["id"], broker_position_id=99, broker_fill_price=4388.5)
  filled = await sql.fetchval(
    "SELECT COUNT(*) FROM manual_algo_charts WHERE signal_id = $1 AND event = 'filled'",
    rec["id"],
  )
  assert filled >= 1

  await _seed_bar("M1", now, 4385, 4387, 4384, 4385)
  await store.close_manual_signal(rec["id"], 30)
  closed = await sql.fetchval(
    "SELECT COUNT(*) FROM manual_algo_charts WHERE signal_id = $1 AND event = 'closed'",
    rec["id"],
  )
  assert closed >= 1


@pytest.mark.asyncio
async def test_empty_redis_does_not_fail_signal_insert(sql):
  await store.init_db()
  rec = await store.store_manual_signal(
    10, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )
  assert rec["id"] > 0
  count = await sql.fetchval("SELECT COUNT(*) FROM manual_algo_charts")
  assert count == 0


def test_parse_skips_garbage():
  assert chart.parse_ohlc_payload("not-json") is None
  parsed = chart.parse_ohlc_payload(
    {"t": 100, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3},
  )
  assert parsed == {"t": 100, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 3.0}
