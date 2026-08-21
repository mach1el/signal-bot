import json

import pytest

from app.persistence import redis_state, store
from app.scripts import repair_manual_algo_results as repair_script


def _event_base(incident):
  return {
    "candidate_id": incident.candidate_id,
    "group_id": incident.group_id,
    "symbol": "XAU",
    "direction": "BUY",
    "stream": "algo_manual",
  }


async def _seed_incident(sql, incident, *, event_mutator=None):
  await store.init_db()
  old_leg = json.dumps([{
    "frac": 1.0,
    "pips": incident.old_manual_result,
    "ts": incident.manual_closed_at,
  }])
  await sql.exec(
    """
    INSERT INTO manual_signals (
      id, ts, action, entry, entry_end, sl, original_sl, tps, order_type,
      status, result_pips, closed_at, fill_state, legs, symbol,
      execution_mode, execution_status, execution_intent_id,
      broker_position_id, broker_fill_price, trade_stream
    ) VALUES (
      $1, $2, 'BUY', $3, $4, $5, $5, '[]', 'limit',
      'closed', $6, $7, 'filled', $8, 'XAU',
      'algo', 'filled', $9, $10, $11, 'algo_manual'
    )
    """,
    incident.signal_id, incident.manual_closed_at - 1000,
    float(incident.entry), float(incident.entry_end), float(incident.stop_loss),
    incident.old_manual_result, incident.manual_closed_at, old_leg,
    incident.candidate_id, str(incident.legs[0].position_id),
    float(incident.deep_fill),
  )
  await sql.exec(
    """
    INSERT INTO pips_log (id, ts, sign, pips, message_id, chat_id, signal_id)
    VALUES ($1, $2, '-', $3, $4, '-1001', $5)
    """,
    incident.pips_log_id, incident.manual_closed_at + 26,
    abs(incident.old_manual_result), 1000 + incident.signal_id,
    incident.signal_id,
  )
  await sql.exec(
    """
    INSERT INTO auto_trade_results (
      group_id, trade_key, trade_stream, result_pips, closed_at,
      setup_type, direction, stop_pips, booked_tp_count, symbol
    ) VALUES ($1, $1, 'algo_manual', $2, $3, 'fixture', 'BUY', $4, NULL, 'XAU')
    """,
    incident.group_id, float(incident.old_raw_result),
    incident.result_closed_at, float(incident.old_result_stop_pips),
  )
  for index, leg in enumerate(incident.legs):
    await sql.exec(
      """
      INSERT INTO auto_trade_fills (
        position_id, group_id, trade_key, trade_stream, symbol,
        setup_type, direction, entry_price, stop_pips, volume, filled_at
      ) VALUES ($1, $2, $2, 'algo_manual', 'XAU', 'fixture', 'BUY',
                $3, $4, $5, $6)
      """,
      leg.position_id, incident.group_id, float(leg.fill_price),
      float(leg.old_fill_stop_pips), leg.volume,
      incident.result_closed_at - 200 + index,
    )

  events = [
    {
      **_event_base(incident),
      "type": "executor_received",
      "timestamp": incident.result_closed_at - 1200,
    },
    {
      **_event_base(incident),
      "type": "routing_selected",
      "timestamp": incident.result_closed_at - 1200,
    },
  ]
  for index, leg in enumerate(incident.legs, start=1):
    events.append({
      **_event_base(incident),
      "type": "manual_limit_placed",
      "timestamp": incident.result_closed_at - 1000 + index,
      "order_id": leg.order_id,
      "tranche_index": index,
      "price": float(leg.planned_entry),
      "stop_loss": float(incident.stop_loss),
      "stop_pips": float(abs(leg.planned_entry - incident.stop_loss) * 10),
      "volume": leg.volume,
    })
  for leg in incident.legs:
    events.append({
      **_event_base(incident),
      "type": "manual_opened",
      "timestamp": incident.result_closed_at - 200,
      "position_id": leg.position_id,
      "price": float(leg.fill_price),
      "stop_loss": float(incident.stop_loss),
      "stop_pips": float(leg.old_fill_stop_pips),
      "volume": leg.volume,
    })
  total_volume = sum(leg.volume for leg in incident.legs)
  for leg in incident.legs:
    events.append({
      **_event_base(incident),
      "type": "position_closed",
      "timestamp": incident.result_closed_at,
      "position_id": leg.position_id,
      "price": float(leg.exit_price),
      "volume": leg.volume,
      "remaining_volume": 0,
      "leg_realized_pips": float(leg.leg_result_pips),
      "group_initial_volume": total_volume,
    })
  events.append({
    **_event_base(incident),
    "type": "group_result",
    "timestamp": incident.result_closed_at,
    "position_id": incident.legs[-1].position_id,
    "group_realized_pips": float(incident.old_raw_result),
  })
  if event_mutator is not None:
    event_mutator(events)
  client = redis_state.get_client()
  await client.rpush(
    f"auto_trade:lifecycle:{incident.candidate_id}",
    *(json.dumps(event) for event in events),
  )


def test_parser_is_dry_run_and_collects_repeated_signal_ids():
  args = repair_script.build_parser().parse_args([
    "--signal-id", "101", "--signal-id", "104",
  ])
  assert args.signal_id == [101, 104]
  assert args.apply is False

  applied = repair_script.build_parser().parse_args([
    "--signal-id", "101", "--apply",
  ])
  assert applied.signal_id == [101]
  assert applied.apply is True


@pytest.mark.asyncio
async def test_dry_run_derives_result_without_writing(sql):
  incident = repair_script.INCIDENTS[101]
  await _seed_incident(sql, incident)

  report = await repair_script.repair([101])

  assert report == [{
    "signal_id": 101,
    "status": "would_repair",
    "group_id": "manual:101",
    "position_ids": [40662911, 40662912, 40662913],
    "result_pips_before": -5,
    "result_pips_after": -47,
    "raw_result_pips_after": pytest.approx(-46.516666666666666),
    "stop_pips_after": 60.0,
    "shallow_fill_after": 4472.06,
    "booked_tp_count": None,
    "lifecycle_event_count": 12,
  }]
  signal = await sql.row(
    "SELECT result_pips, broker_fill_price, legs FROM manual_signals WHERE id=101"
  )
  assert signal["result_pips"] == -5
  assert signal["broker_fill_price"] == pytest.approx(4469.99)
  assert json.loads(signal["legs"])[0]["pips"] == -5
  assert await sql.val("SELECT pips FROM pips_log WHERE signal_id=101") == 5
  result = await sql.row(
    "SELECT result_pips, stop_pips, correction_source "
    "FROM auto_trade_results WHERE group_id='manual:101'"
  )
  assert result["result_pips"] == pytest.approx(-5.216666666666667)
  assert result["stop_pips"] == pytest.approx(50.6)
  assert result["correction_source"] is None


@pytest.mark.asyncio
async def test_apply_repairs_all_surfaces_and_is_idempotent(sql):
  for signal_id in (101, 104):
    await _seed_incident(sql, repair_script.INCIDENTS[signal_id])

  first = await repair_script.repair([104, 101], apply=True)
  assert [item["status"] for item in first] == ["repaired", "repaired"]
  assert [item["result_pips_after"] for item in first] == [-47, -68]

  signals = await sql.fetch(
    "SELECT id, result_pips, broker_fill_price, legs FROM manual_signals "
    "WHERE id IN (101,104) ORDER BY id"
  )
  assert [row["result_pips"] for row in signals] == [-47, -68]
  assert [row["broker_fill_price"] for row in signals] == pytest.approx([
    4472.06, 4462.89,
  ])
  assert [json.loads(row["legs"])[0]["pips"] for row in signals] == [-47, -68]
  pips_rows = await sql.fetch(
    "SELECT signal_id, sign, pips FROM pips_log ORDER BY signal_id"
  )
  assert [tuple(row) for row in pips_rows] == [
    (101, "-", 47), (104, "-", 68),
  ]

  results = await sql.fetch(
    "SELECT group_id, result_pips, stop_pips, booked_tp_count, "
    "correction_source, corrected_at FROM auto_trade_results ORDER BY group_id"
  )
  assert [row["result_pips"] for row in results] == pytest.approx([
    -46.516666666666666, -67.6,
  ])
  assert [row["stop_pips"] for row in results] == pytest.approx([60, 60])
  assert all(row["booked_tp_count"] is None for row in results)
  assert all(
    row["correction_source"] == repair_script.CORRECTION_SOURCE
    and row["corrected_at"] is not None
    for row in results
  )
  fills = await sql.fetch(
    "SELECT stop_pips, correction_source, corrected_at FROM auto_trade_fills"
  )
  assert len(fills) == 6
  assert all(row["stop_pips"] == pytest.approx(60) for row in fills)
  assert all(
    row["correction_source"] == repair_script.CORRECTION_SOURCE
    and row["corrected_at"] is not None
    for row in fills
  )

  second = await repair_script.repair([101, 104], apply=True)
  assert [item["status"] for item in second] == [
    "already_repaired", "already_repaired",
  ]


@pytest.mark.asyncio
async def test_corrected_ledger_resists_generic_stats_replay(sql):
  incident = repair_script.INCIDENTS[101]
  await _seed_incident(sql, incident)
  await repair_script.repair([101], apply=True)

  await store.record_auto_trade_event({
    "type": "manual_opened",
    "candidate_id": incident.candidate_id,
    "position_id": incident.legs[0].position_id,
    "group_id": incident.group_id,
    "stream": "algo_manual",
    "symbol": "XAU",
    "direction": "BUY",
    "price": 4000,
    "stop_pips": 1,
    "volume": 1,
    "timestamp": 1,
  })
  await store.record_auto_trade_event({
    "type": "group_result",
    "group_id": incident.group_id,
    "stream": "algo_manual",
    "symbol": "XAU",
    "direction": "BUY",
    "group_realized_pips": -1,
    "stop_pips": 1,
    "timestamp": 1,
  })

  fill = await sql.row(
    "SELECT entry_price, volume, stop_pips, correction_source "
    "FROM auto_trade_fills WHERE position_id=$1",
    incident.legs[0].position_id,
  )
  assert fill["entry_price"] == pytest.approx(4472.06)
  assert fill["volume"] == 600
  assert fill["stop_pips"] == pytest.approx(60)
  assert fill["correction_source"] == repair_script.CORRECTION_SOURCE
  result = await sql.row(
    "SELECT result_pips, stop_pips, correction_source "
    "FROM auto_trade_results WHERE group_id=$1",
    incident.group_id,
  )
  assert result["result_pips"] == pytest.approx(-46.516666666666666)
  assert result["stop_pips"] == pytest.approx(60)
  assert result["correction_source"] == repair_script.CORRECTION_SOURCE


@pytest.mark.asyncio
async def test_refuses_take_profit_and_writes_nothing(sql):
  incident = repair_script.INCIDENTS[101]

  def add_tp(events):
    events.append({
      **_event_base(incident),
      "type": "take_profit",
      "position_id": incident.legs[0].position_id,
    })

  await _seed_incident(sql, incident, event_mutator=add_tp)
  with pytest.raises(repair_script.RepairValidationError, match="TP events exist"):
    await repair_script.repair([101], apply=True)
  assert await sql.val(
    "SELECT result_pips FROM manual_signals WHERE id=101"
  ) == -5


@pytest.mark.asyncio
async def test_refuses_incomplete_three_leg_lifecycle(sql):
  incident = repair_script.INCIDENTS[104]

  def drop_one_close(events):
    index = next(
      index for index, event in enumerate(events)
      if event["type"] == "position_closed"
    )
    events.pop(index)

  await _seed_incident(sql, incident, event_mutator=drop_one_close)
  with pytest.raises(repair_script.RepairValidationError, match="3 closed legs"):
    await repair_script.repair([104], apply=True)
  assert await sql.val(
    "SELECT result_pips FROM manual_signals WHERE id=104"
  ) == -8


@pytest.mark.asyncio
async def test_batch_rolls_back_if_later_repair_fails(sql, monkeypatch):
  for signal_id in (101, 104):
    await _seed_incident(sql, repair_script.INCIDENTS[signal_id])
  real_apply = repair_script._apply_one

  async def fail_after_second(db, incident, **kwargs):
    await real_apply(db, incident, **kwargs)
    if incident.signal_id == 104:
      raise RuntimeError("injected failure")

  monkeypatch.setattr(repair_script, "_apply_one", fail_after_second)
  with pytest.raises(RuntimeError, match="injected failure"):
    await repair_script.repair([101, 104], apply=True)

  signal_rows = await sql.fetch(
    "SELECT id, result_pips FROM manual_signals ORDER BY id"
  )
  assert [tuple(row) for row in signal_rows] == [(101, -5), (104, -8)]
  assert await sql.val(
    "SELECT COUNT(*) FROM auto_trade_results WHERE correction_source IS NOT NULL"
  ) == 0


@pytest.mark.asyncio
async def test_refuses_non_allowlisted_or_diverged_database_state(sql):
  with pytest.raises(repair_script.RepairValidationError, match="unsupported"):
    await repair_script.repair([999], client=redis_state.get_client())

  incident = repair_script.INCIDENTS[104]
  await _seed_incident(sql, incident)
  await sql.exec(
    "UPDATE auto_trade_fills SET volume=999 WHERE position_id=$1",
    incident.legs[0].position_id,
  )
  with pytest.raises(repair_script.RepairValidationError, match="fill volume"):
    await repair_script.repair([104], apply=True)
  assert await sql.val(
    "SELECT result_pips FROM manual_signals WHERE id=104"
  ) == -8
