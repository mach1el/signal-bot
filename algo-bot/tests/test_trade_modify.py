""" /trade_modify — pending-only level rewrite + new channel cards. """

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.persistence import redis_state, store
from app.signals import manual_execution, trade_ops
from app.signals.parsing import _parse_modify_body, _seq_token


def test_seq_token_accepts_trailing_modify_body():
  # Owner-reported: "/trade_modify xau #14 4636-33" hit Usage because
  # _seq_token required the entire remainder to be digits, so "#14 4636-33"
  # never resolved the daily seq even though bare-zone body parse was fine.
  assert _seq_token("#14 4636-33") == 14
  assert _seq_token("14 sl 4384") == 14
  assert _seq_token("#12") == 12
  assert _seq_token("12") == 12
  assert _seq_token("4636-33") is None
  assert _seq_token("") is None


async def _pending_notify(**overrides) -> dict:
  await store.init_db()
  kwargs = dict(
    ts=1,
    action="BUY",
    entry=4388.0,
    entry_end=4391.0,
    sl=4385.0,
    tps=[4394.0, 4397.0, 4401.0],
    execution_mode="notify",
  )
  kwargs.update(overrides)
  rec = await store.store_manual_signal(**kwargs)
  return await store.get_manual_signal(rec["id"])


async def _pending_algo(**overrides) -> dict:
  row = await _pending_notify(execution_mode="algo", **overrides)
  await store.set_execution_intent(
    row["id"],
    intent_id=f"manual:{row['id']}:0",
    status="pending",
    revision=0,
  )
  return await store.get_manual_signal(row["id"])


def test_parse_modify_body_entry_sl_tp():
  fields = _parse_modify_body(
    "entry 4388-4391 sl 4385 tp 4394/4397/4401",
    action="BUY",
    entry=4380.0,
    entry_end=4383.0,
  )
  assert fields is not None
  assert fields["entry"] == pytest.approx(4388.0)
  assert fields["entry_end"] == pytest.approx(4391.0)
  assert fields["sl"] == pytest.approx(4385.0)
  assert fields["tps"] == [4394.0, 4397.0, 4401.0]


def test_parse_modify_body_accepts_bare_zone_without_entry_keyword():
  # Owner-reported: "/trade_modify xau #19 4586-83" hit the Usage fallback
  # because the literal "entry" keyword was required here even though the
  # primary signal parser already accepts a bare zone the same way.
  fields = _parse_modify_body(
    "4586-83",
    action="BUY",
    entry=4580.0,
    entry_end=4583.0,
  )
  assert fields is not None
  assert fields["entry"] == pytest.approx(4583.0)
  assert fields["entry_end"] == pytest.approx(4586.0)


def test_parse_modify_body_ignores_stray_direction_word_before_bare_zone():
  # "/trade_modify xau #19 buy 4586-83" - modify never changes direction,
  # so a leading buy/sell the owner typed out of habit must be harmless,
  # not treated as an unrecognized field that fails the whole parse.
  fields = _parse_modify_body(
    "buy 4586-83",
    action="BUY",
    entry=4580.0,
    entry_end=4583.0,
  )
  assert fields is not None
  assert fields["entry"] == pytest.approx(4583.0)
  assert fields["entry_end"] == pytest.approx(4586.0)


def test_parse_modify_body_partial_sl_only():
  fields = _parse_modify_body(
    "sl 4384",
    action="BUY",
    entry=4388.0,
    entry_end=4391.0,
  )
  assert fields == {"sl": 4384.0}


def test_parse_modify_body_empty_returns_none():
  assert _parse_modify_body(
    "noop",
    action="BUY",
    entry=4388.0,
    entry_end=4391.0,
  ) is None


@pytest.mark.asyncio
async def test_do_modify_notify_replaces_posts(monkeypatch):
  row = await _pending_notify()
  replace = AsyncMock(return_value=[])
  monkeypatch.setattr(trade_ops, "replace_entry_posts", replace)

  result = await trade_ops.do_modify({
    "sid": row["id"],
    "symbol": "XAU",
    "entry": 4390.0,
    "entry_end": 4392.0,
    "sl": 4387.0,
    "tps": [4395.0, 4398.0],
  })

  assert result["ok"] is True
  assert result["action"] == "modify"
  assert result.get("pending") is None
  replace.assert_awaited_once()
  updated = await store.get_manual_signal(row["id"])
  assert updated["entry"] == pytest.approx(4390.0)
  assert updated["sl"] == pytest.approx(4387.0)
  assert updated["tps"] == [4395.0, 4398.0]
  assert "modified" in trade_ops.render_result(result, "XAU", "vip")


@pytest.mark.asyncio
async def test_do_modify_rejects_filled(monkeypatch):
  row = await _pending_notify()
  await store.mark_filled(row["id"])
  replace = AsyncMock()
  monkeypatch.setattr(trade_ops, "replace_entry_posts", replace)

  result = await trade_ops.do_modify({
    "sid": row["id"],
    "symbol": "XAU",
    "sl": 4384.0,
  })

  assert result["ok"] is False
  assert result["error"] == "not_pending"
  replace.assert_not_awaited()


@pytest.mark.asyncio
async def test_do_modify_algo_pending_defers_cancel(monkeypatch):
  row = await _pending_algo()
  request_cancel = AsyncMock()
  mark_pending = AsyncMock()
  replace = AsyncMock()
  monkeypatch.setattr(manual_execution, "request_cancel", request_cancel)
  monkeypatch.setattr(manual_execution, "mark_pending_modify", mark_pending)
  monkeypatch.setattr(trade_ops, "replace_entry_posts", replace)

  result = await trade_ops.do_modify({
    "sid": row["id"],
    "symbol": "XAU",
    "sl": 4384.0,
  })

  assert result["ok"] is True
  assert result.get("pending") is True
  mark_pending.assert_awaited_once_with(f"manual:{row['id']}:0")
  request_cancel.assert_awaited_once_with(f"manual:{row['id']}:0")
  replace.assert_not_awaited()
  updated = await store.get_manual_signal(row["id"])
  assert updated["sl"] == pytest.approx(4384.0)
  assert updated["status"] == "open"
  assert "modify requested" in trade_ops.render_result(result, "XAU", "vip")


@pytest.mark.asyncio
async def test_manual_cancelled_pending_modify_rearms_and_replaces(monkeypatch):
  row = await _pending_algo(sl=4385.0)
  await store.update_pending_levels(row["id"], sl=4384.0)
  intent = f"manual:{row['id']}:0"
  await manual_execution.mark_pending_modify(intent)

  publish = AsyncMock()
  replace = AsyncMock(return_value=[])
  truth = AsyncMock()
  monkeypatch.setattr("app.signals.manual_intent.publish_intent", publish)
  monkeypatch.setattr(trade_ops, "replace_entry_posts", replace)
  monkeypatch.setattr(manual_execution, "_send_executor_truth", truth)

  await manual_execution._handle_event(
    redis_state.get_client(),
    {"type": "manual_cancelled", "candidate_id": intent},
    {},
  )

  fresh = await store.get_manual_signal(row["id"])
  assert fresh is not None
  assert fresh["status"] == "open"
  assert fresh["sl"] == pytest.approx(4384.0)
  assert fresh["execution_revision"] == 1
  assert fresh["execution_intent_id"] == f"manual:{row['id']}:1"
  assert fresh["execution_status"] == "requested"
  publish.assert_awaited_once()
  replace.assert_awaited_once()
  truth.assert_awaited_once()
  assert "modified" in truth.await_args.args[0]
  assert await redis_state.get_client().get(
    f"manual_trade:pending_modify:{intent}",
  ) is None
