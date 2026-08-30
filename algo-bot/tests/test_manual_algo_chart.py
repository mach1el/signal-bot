import json
import time
from types import SimpleNamespace

import pytest

from app.analysis.ohlc_source import window_for_timeframe
from app.persistence import redis_state, store
from app.signals import manual_algo_chart as chart


async def _seed_bar(
  tf: str,
  ts: int,
  o: float,
  h: float,
  l: float,
  c: float,
  *,
  symbol: str = "XAU",
) -> None:
  client = redis_state.get_client()
  payload = json.dumps({"t": ts, "o": o, "h": h, "l": l, "c": c, "v": 12})
  await client.zadd(f"bars:{symbol}:{tf}", {payload: ts})


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
    "SELECT event, timeframe, jsonb_array_length(bars) AS n, "
    "bars_requested, bars_stored, bars_after_event, capture_version "
    "FROM manual_algo_charts WHERE signal_id = $1 ORDER BY timeframe",
    rec["id"],
  )
  assert {(row["event"], row["timeframe"], row["n"]) for row in issued_rows} == {
    ("issued", "H1", 1),
    ("issued", "M1", 1),
    ("issued", "M15", 1),
    ("issued", "M5", 1),
  }
  for row in issued_rows:
    assert int(row["capture_version"]) == 2
    assert int(row["bars_stored"]) == 1
    assert int(row["bars_requested"]) >= 50
    assert int(row["bars_after_event"]) == 0

  now = int(time.time())
  await _seed_bar("M1", now - 60, 4389, 4390, 4388, 4388.5)
  await store.set_execution_fill(rec["id"], broker_position_id=99, broker_fill_price=4388.5)
  filled = await sql.val(
    "SELECT COUNT(*) FROM manual_algo_charts WHERE signal_id = $1 AND event = 'filled'",
    rec["id"],
  )
  assert filled >= 1

  await _seed_bar("M1", now, 4385, 4387, 4384, 4385)
  await store.close_manual_signal(rec["id"], 30)
  closed = await sql.val(
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
  count = await sql.val("SELECT COUNT(*) FROM manual_algo_charts")
  assert count == 0


@pytest.mark.no_database
def test_parse_skips_garbage():
  assert chart.parse_ohlc_payload("not-json") is None
  parsed = chart.parse_ohlc_payload(
    {"t": 100, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3},
  )
  assert parsed == {"t": 100, "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 3.0}


@pytest.mark.no_database
def test_window_uses_live_lookback_and_exceeds_floor():
  ts = 1_700_000_000
  start, end, bars = chart._window("H1", "issued", ts, symbol="XAU")
  live = window_for_timeframe("H1")
  assert bars == live
  assert end - start == live * 3600
  assert bars >= 50
  m15_bars = chart._window("M15", "issued", ts, symbol="XAU")[2]
  assert m15_bars >= 50
  assert m15_bars == window_for_timeframe("M15")


@pytest.mark.no_database
def test_window_uses_instrument_scoped_lookbacks(monkeypatch):
  view = SimpleNamespace(
    market_data=SimpleNamespace(
      lookbacks=SimpleNamespace(
        h1_bars=120,
        m15_bars=80,
        m5_bars=60,
        m1_bars=55,
      ),
      scanner=SimpleNamespace(window=200),
    ),
  )
  monkeypatch.setattr(
    chart,
    "instrument_runtime_view",
    lambda symbol: view,
  )
  ts = 1_700_000_000
  _start, _end, bars = chart._window("H1", "issued", ts, symbol="XAG")
  assert bars == 120
  assert chart._window("M15", "issued", ts, symbol="XAG")[2] == 80


@pytest.mark.asyncio
async def test_snapshot_records_adequacy_and_lookahead_counts(sql):
  await store.init_db()
  ts = int(time.time()) - 600
  for i in range(5):
    await _seed_bar("M1", ts - (i + 1) * 60, 4300, 4301, 4299, 4300)
  for i in range(1, 4):
    await _seed_bar("M1", ts + i * 60, 4300, 4301, 4299, 4300)
  await _seed_bar("M5", ts - 300, 4300, 4302, 4298, 4301)
  await _seed_bar("M15", ts - 900, 4295, 4305, 4290, 4300)
  await _seed_bar("H1", ts - 3600, 4280, 4310, 4275, 4298)

  signal_id = await _insert_signal(sql, ts)
  stored = await chart.snapshot_manual_algo_chart(
    signal_id=signal_id,
    event="filled",
    ts=ts,
    symbol="XAU",
  )
  assert stored >= 1
  row = await sql.row(
    "SELECT bars_requested, bars_stored, bars_after_event, capture_version, "
    "jsonb_array_length(bars) AS n "
    "FROM manual_algo_charts WHERE event = 'filled' AND timeframe = 'M1' "
    "ORDER BY id DESC LIMIT 1",
  )
  assert int(row["capture_version"]) == 2
  assert int(row["bars_requested"]) >= 50
  assert int(row["bars_stored"]) == int(row["n"])
  assert int(row["bars_after_event"]) >= 1


async def _insert_signal(sql, ts: int) -> int:
  row = await sql.row(
    """
    INSERT INTO manual_signals
      (ts, action, entry, sl, tps, order_type, status, symbol, trade_stream)
    VALUES ($1, 'BUY', 4300, 4290, '[]', 'limit', 'open', 'XAU', 'algo_manual')
    RETURNING id
    """,
    ts,
  )
  return int(row["id"])


@pytest.mark.asyncio
async def test_load_manual_algo_charts_causal_trim(sql):
  await store.init_db()
  ts = int(time.time()) - 300
  signal_id = await _insert_signal(sql, ts)
  await store.upsert_manual_algo_chart(
    signal_id=signal_id,
    event="filled",
    captured_at=ts,
    symbol="XAU",
    timeframe="M1",
    window_start=ts - 600,
    window_end=ts + 300,
    bars=[
      {"t": ts - 60, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1},
      {"t": ts + 60, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1},
    ],
    bars_requested=150,
    bars_stored=2,
    bars_after_event=1,
    capture_version=2,
  )
  causal = await store.load_manual_algo_charts(
    signal_id, event="filled", causal_only=True,
  )
  assert [bar["t"] for bar in causal["M1"]] == [ts - 60]
  full = await store.load_manual_algo_charts(
    signal_id, event="filled", causal_only=False,
  )
  assert [bar["t"] for bar in full["M1"]] == [ts - 60, ts + 60]


@pytest.mark.asyncio
async def test_legacy_rows_default_capture_version_one(sql):
  await store.init_db()
  ts = int(time.time())
  signal_id = await _insert_signal(sql, ts)
  await sql.exec(
    """
    INSERT INTO manual_algo_charts
      (signal_id, event, captured_at, symbol, timeframe,
       window_start, window_end, bars)
    VALUES ($1, 'issued', $2, 'XAU', 'M5', $3, $4, $5::jsonb)
    """,
    signal_id,
    ts,
    ts - 1000,
    ts,
    json.dumps([{"t": ts - 300, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}]),
  )
  row = await sql.row(
    "SELECT bars_requested, bars_stored, bars_after_event, capture_version "
    "FROM manual_algo_charts WHERE signal_id = $1 AND timeframe = 'M5'",
    signal_id,
  )
  assert int(row["capture_version"]) == 1
  assert int(row["bars_requested"]) == 0
  assert int(row["bars_stored"]) == 0
  assert int(row["bars_after_event"]) == 0
