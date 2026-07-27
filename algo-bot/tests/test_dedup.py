import json

import pytest

from app.persistence import store


@pytest.mark.asyncio
async def test_daily_seq_resets_by_trade_date(sql):
  await store.init_db()

  first = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )
  second = await store.store_manual_signal(
    2, "SELL", 2000.0, 2002.0, 2010.0, [1990.0],
  )
  assert first["daily_seq"] == 1
  assert second["daily_seq"] == 2

  await sql.exec("UPDATE manual_signals SET trade_date = '2000-01-01'")

  next_day = await store.store_manual_signal(
    3, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )
  assert next_day["daily_seq"] == 1


@pytest.mark.asyncio
async def test_schema_has_all_columns_and_fill_is_idempotent(sql):
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )

  columns = {
    row["column_name"]
    for row in await sql.fetch(
      "SELECT column_name FROM information_schema.columns "
      "WHERE table_name = 'manual_signals'"
    )
  }
  assert {
    "daily_seq", "trade_date", "fill_state", "filled_at", "legs",
    "parent_id", "setup_type", "confluence", "note", "symbol",
    "visibility",
  } <= columns

  assert await store.mark_filled(rec["id"]) is not None
  assert await store.mark_filled(rec["id"]) is None


@pytest.mark.asyncio
async def test_get_open_signals_auto_cancels_stale_pending(sql):
  await store.init_db()
  stale = await store.store_manual_signal(
    1, "SELL", 4092.0, 4095.0, 4100.0, [4080.0],
  )
  today = await store.store_manual_signal(
    2, "SELL", 4088.0, 4090.0, 4095.0, [4075.0],
  )
  filled = await store.store_manual_signal(
    3, "SELL", 4018.0, 4022.0, 4028.0, [4010.0],
  )
  assert await store.mark_filled(filled["id"]) is not None
  await sql.exec(
    "UPDATE manual_signals SET trade_date = '2000-01-01' "
    "WHERE id = ANY($1::bigint[])",
    [stale["id"], filled["id"]],
  )

  opens = await store.get_open_signals()

  assert [row["id"] for row in opens] == [today["id"], filled["id"]]
  stale_row = await store.get_manual_signal(stale["id"])
  assert stale_row["status"] == "cancelled"
  assert stale_row["fill_state"] == "pending"
  assert stale_row["closed_at"] is not None
  today_row = await store.get_manual_signal(today["id"])
  assert today_row["status"] == "open"
  filled_row = await store.get_manual_signal(filled["id"])
  assert filled_row["status"] == "open"
  assert filled_row["fill_state"] == "filled"


@pytest.mark.asyncio
async def test_mark_filled_auto_cancels_stale_pending(sql):
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "SELL", 4092.0, 4095.0, 4100.0, [4080.0],
  )
  await sql.exec(
    "UPDATE manual_signals SET trade_date = '2000-01-01' WHERE id = $1",
    rec["id"],
  )

  assert await store.mark_filled(rec["id"]) is None

  row = await store.get_manual_signal(rec["id"])
  assert row["status"] == "cancelled"
  assert row["fill_state"] == "pending"
  assert row["closed_at"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("runner_pips", "expected_net"),
  [(90, 70), (-30, 10)],
)
async def test_close_leg_weighted_net(sql, runner_pips, expected_net):
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )

  partial = await store.close_leg(rec["id"], 50, 0.5)
  assert partial["closed"] is False
  assert partial["remaining"] == pytest.approx(0.5)

  final = await store.close_leg(rec["id"], runner_pips)
  assert final["closed"] is True
  assert final["net"] == expected_net

  row = await sql.row(
    "SELECT status, result_pips, legs FROM manual_signals WHERE id = $1",
    rec["id"],
  )
  assert row["status"] == "closed"
  assert row["result_pips"] == expected_net
  assert len(json.loads(row["legs"])) == 2


@pytest.mark.asyncio
async def test_partial_profit_then_breakeven_stop_preserves_profit():
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "SELL", 4026.0, 4029.0, 4034.0, [4023.0, 4017.0],
  )

  partial = await store.close_leg(rec["id"], 90, 0.5)
  final = await store.close_leg(rec["id"], 0)

  assert partial["remaining"] == pytest.approx(0.5)
  assert final["closed"] is True
  assert final["net"] == 45


@pytest.mark.asyncio
async def test_close_leg_rejects_overbook():
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )

  await store.close_leg(rec["id"], 50, 0.5)
  rejected = await store.close_leg(rec["id"], 40, 0.6)

  assert rejected["error"] == "exceeds_remaining"
  assert rejected["remaining"] == pytest.approx(0.5)
  open_signal = (await store.get_open_signals())[0]
  assert len(open_signal["legs"]) == 1


@pytest.mark.asyncio
async def test_undo_last_close_leg_restores_running_signal(sql):
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )

  await store.close_leg(rec["id"], 50, 0.5)
  final = await store.close_leg(rec["id"], 90)
  await store.store_pips("+", final["net"], signal_id=rec["id"])

  restored = await store.undo_last_close_leg(rec["id"])

  assert restored["status"] == "open"
  assert restored["remaining"] == pytest.approx(0.5)
  assert restored["restored_leg"]["pips"] == 90
  row = await store.get_manual_signal(rec["id"])
  assert row["status"] == "open"
  assert row["result_pips"] is None
  assert row["closed_at"] is None
  assert len(row["legs"]) == 1
  assert row["legs"][0]["pips"] == 50

  pips_rows = await sql.val(
    "SELECT COUNT(*) FROM pips_log WHERE signal_id = $1", rec["id"],
  )
  assert pips_rows == 0


@pytest.mark.asyncio
async def test_undo_legacy_close_without_legs(sql):
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )

  await sql.exec(
    "UPDATE manual_signals "
    "SET status = 'closed', result_pips = 80, closed_at = 123 "
    "WHERE id = $1",
    rec["id"],
  )

  restored = await store.undo_last_close_leg(rec["id"])

  assert restored["status"] == "open"
  assert restored["remaining"] == pytest.approx(1.0)
  row = await store.get_manual_signal(rec["id"])
  assert row["status"] == "open"
  assert row["result_pips"] is None
  assert row["closed_at"] is None
  assert row["legs"] == []


@pytest.mark.asyncio
async def test_delete_manual_signal_purges_row_posts_and_pips(sql):
  await store.init_db()
  rec = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )
  await store.insert_signal_post(rec["id"], -100123456789, 555, "vip")
  await store.store_pips("+", 30, signal_id=rec["id"])

  deleted = await store.delete_manual_signal(rec["id"])

  assert deleted["id"] == rec["id"]
  assert deleted["posts"] == [
    {
      "signal_id": rec["id"],
      "channel_id": -100123456789,
      "message_id": 555,
      "tier": "vip",
    }
  ]
  assert await store.get_manual_signal(rec["id"]) is None
  assert await store.get_signal_posts(rec["id"]) == []
  assert await sql.val(
    "SELECT COUNT(*) FROM pips_log WHERE signal_id = $1", rec["id"],
  ) == 0


@pytest.mark.asyncio
async def test_delete_manual_signal_refuses_when_rounds_exist(sql):
  await store.init_db()
  root = await store.store_manual_signal(
    1, "BUY", 2000.0, 2002.0, 1990.0, [2010.0],
  )
  await store.store_manual_signal(
    2, "BUY", 2001.0, 2003.0, 1991.0, [2011.0], parent_id=root["id"],
  )

  result = await store.delete_manual_signal(root["id"])

  assert result == {"error": "has_rounds"}
  assert await store.get_manual_signal(root["id"]) is not None


@pytest.mark.asyncio
async def test_delete_manual_signal_missing_returns_none():
  await store.init_db()
  assert await store.delete_manual_signal(9999) is None
