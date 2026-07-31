"""One forming card per setup: post-or-edit, delete on terminal (Codex
Prompt P4).
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from redis.asyncio import Redis

from app.autotrade import setup_card
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  INVALIDATED,
  create_setup,
  transition_setup,
)
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


async def _confirmed_setup(client, setup_id: str) -> None:
  await create_setup(
    client, setup_id=setup_id, thesis_id=f"thesis-{setup_id}", symbol="XAU",
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, setup_id, state)


@pytest.mark.asyncio
async def test_one_card_per_setup_posts_once_then_edits():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-1")
  sent = []
  edited = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=9001)

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  first_id = await setup_card.post_or_edit_forming_card(
    client, "setup-1", "forming v1", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )
  second_id = await setup_card.post_or_edit_forming_card(
    client, "setup-1", "forming v2", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )
  third_id = await setup_card.post_or_edit_forming_card(
    client, "setup-1", "forming v3", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )

  assert first_id == second_id == third_id == 9001
  assert sent == ["forming v1"]  # posted exactly once
  assert edited == [
    (123, 9001, "forming v2"),
    (123, 9001, "forming v3"),
  ]
  card = await setup_card.load_forming_card(client, "setup-1")
  assert card == {
    "chat_id": 123,
    "message_id": 9001,
    "text": "forming v3",
  }


@pytest.mark.asyncio
async def test_edit_failure_falls_back_to_a_fresh_post():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-2")
  await setup_card.save_forming_card(client, "setup-2", chat_id=123, message_id=7777)

  async def send_fn(text, **kwargs):
    return SimpleNamespace(message_id=8888)

  async def edit_fn(chat_id, message_id, text):
    raise TelegramBadRequest(method=None, message="Bad Request: message to edit not found")

  new_id = await setup_card.post_or_edit_forming_card(
    client, "setup-2", "forming v2", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )

  assert new_id == 8888
  card = await setup_card.load_forming_card(client, "setup-2")
  assert card == {
    "chat_id": 123,
    "message_id": 8888,
    "text": "forming v2",
  }


def test_apply_forming_card_stop_does_not_duplicate_existing_stop():
  """Card already has Stop after Key level — patch must not insert a second."""
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "• <b>Key level:</b> <b>4,034.85</b>",
    "• <b>Stop:</b> <b>4,039.68</b>",
    "",
    "🧭 <b>Context</b>",
  ])
  text = setup_card.apply_forming_card_stop(original, 4039.68)
  assert text.count("• <b>Stop:</b>") == 1
  assert "• <b>Stop:</b> <b>4,039.68</b>" in text


@pytest.mark.asyncio
async def test_apply_forming_card_stop_patches_trade_area_stop_line():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-stop")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor",
    "• <b>Entry zone:</b> <b>4,072.99–4,076.89</b>",
    "• <b>Key level:</b> <b>4,074.94</b>",
    "• <b>Stop:</b> <b>SL</b>",
    "",
    "→ Executor owns mechanical entry and risk enforcement.",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-stop",
    chat_id=123,
    message_id=555,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  assert await setup_card.edit_forming_card_stop(
    client, "setup-stop", 4070.5, edit_fn=edit_fn,
  )
  text = edited[0][2]
  assert "• <b>Stop:</b> <b>4,070.50</b>" in text
  assert "• <b>Stop:</b> <b>SL</b>" not in text
  assert "Copy draft" not in text


@pytest.mark.asyncio
async def test_apply_forming_card_stop_still_patches_legacy_copy_draft_if_present():
  """Older cards may still carry a manual copy draft; keep Stop+draft in sync."""
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-stop-legacy")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor",
    "• <b>Entry zone:</b> <b>4,072.99–4,076.89</b>",
    "• <b>Key level:</b> <b>4,074.94</b>",
    "",
    "📋 <b>Copy draft</b>",
    "<code>gold buy entry zone (4072.99-4076.89) / sl SL / tp TP1/TP2/TP3 / setup key-level-reaction **</code>",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-stop-legacy",
    chat_id=123,
    message_id=556,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  assert await setup_card.edit_forming_card_stop(
    client, "setup-stop-legacy", 4070.5, edit_fn=edit_fn,
  )
  text = edited[0][2]
  assert "• <b>Stop:</b> <b>4,070.50</b>" in text
  assert "/ sl 4070.50 /" in text
  assert "sl SL" not in text

@pytest.mark.asyncio
async def test_lifecycle_status_replaces_only_the_card_status_line():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-status")
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟡 <b>QUEUED</b> · worker acknowledgement pending",
    "🔴 <b>SELL · Supply Zone Reaction</b>",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-status",
    chat_id=123,
    message_id=9876,
    text=original,
  )
  edited = []

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  changed = await setup_card.edit_forming_card_status(
    client,
    "setup-status",
    "🟠 <b>WAITING RETEST</b> · executable quote is outside the zone",
    edit_fn=edit_fn,
  )

  assert changed
  assert edited[0][0:2] == (123, 9876)
  assert "WAITING RETEST" in edited[0][2]
  assert "Supply Zone Reaction" in edited[0][2]
  card = await setup_card.load_forming_card(client, "setup-status")
  assert card is not None
  assert card["text"] == edited[0][2]


@pytest.mark.asyncio
async def test_status_snapshot_wins_when_worker_finishes_before_card_post():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-race")
  await setup_card.save_forming_card_status(
    client,
    "setup-race",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor",
  )
  sent = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=6789)

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("new card should be posted, not edited")

  await setup_card.post_or_edit_forming_card(
    client,
    "setup-race",
    "\n".join([
      "🔎 <b>XAU M5 · SETUP FORMING</b>",
      "🟡 <b>QUEUED</b> · worker acknowledgement pending",
      "🔴 <b>SELL · Supply Zone Reaction</b>",
    ]),
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )

  assert len(sent) == 1
  assert "PLAN PUBLISHED" in sent[0]
  assert "QUEUED" not in sent[0]


@pytest.mark.asyncio
async def test_terminal_setup_is_never_re_carded():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-3")
  await transition_setup(client, "setup-3", INVALIDATED, reason_code="structure_broke")
  calls = []

  async def send_fn(text, **kwargs):
    calls.append(text)
    return SimpleNamespace(message_id=1)

  async def edit_fn(chat_id, message_id, text):
    calls.append(text)

  result = await setup_card.post_or_edit_forming_card(
    client, "setup-3", "forming again?", chat_id=123, send_fn=send_fn, edit_fn=edit_fn,
  )

  assert result is None
  assert calls == []


@pytest.mark.asyncio
async def test_kill_setup_card_deletes_and_clears_storage(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(setup_card.settings, "auto_trade_telegram_single_root_card", False)
  monkeypatch.setattr(setup_card.settings, "delivery_delete_on_terminal", True)
  await setup_card.save_forming_card(client, "setup-4", chat_id=123, message_id=5555)
  await setup_card.save_forming_card_status(
    client,
    "setup-4",
    "🟠 WAITING RETEST",
  )
  deleted = []

  async def delete_fn(chat_id, message_id):
    deleted.append((chat_id, message_id))

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("edit_fn should not be called when delete succeeds")

  await setup_card.kill_setup_card(
    client, "setup-4", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert deleted == [(123, 5555)]
  assert await setup_card.load_forming_card(client, "setup-4") is None
  assert await setup_card.load_forming_card_status(client, "setup-4") is None


@pytest.mark.asyncio
async def test_kill_setup_card_falls_back_to_terminal_edit_when_delete_fails(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(setup_card.settings, "auto_trade_telegram_single_root_card", False)
  monkeypatch.setattr(setup_card.settings, "delivery_delete_on_terminal", True)
  await setup_card.save_forming_card(client, "setup-5", chat_id=123, message_id=6666)
  edited = []

  async def delete_fn(chat_id, message_id):
    raise TelegramBadRequest(method=None, message="Bad Request: message can't be deleted")

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  await setup_card.kill_setup_card(
    client, "setup-5", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert len(edited) == 1
  assert edited[0][:2] == (123, 6666)
  assert "setup closed" in edited[0][2].lower()
  # Redis bookkeeping is cleared either way - no actionable card remains
  # tracked even though the message itself is still in the chat (edited).
  assert await setup_card.load_forming_card(client, "setup-5") is None


@pytest.mark.asyncio
async def test_kill_setup_card_is_a_noop_with_no_stored_card():
  client = redis_state.get_client()
  await setup_card.save_forming_card_status(
    client,
    "setup-does-not-exist",
    "🟡 PREFLIGHT",
  )
  calls = []

  async def delete_fn(chat_id, message_id):
    calls.append("delete")

  async def edit_fn(chat_id, message_id, text):
    calls.append("edit")

  await setup_card.kill_setup_card(
    client, "setup-does-not-exist", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert calls == []
  assert (
    await setup_card.load_forming_card_status(
      client,
      "setup-does-not-exist",
    )
    is None
  )


@pytest.mark.asyncio
async def test_delete_on_terminal_disabled_edits_and_retains_root(monkeypatch):
  client = redis_state.get_client()
  await setup_card.save_forming_card(client, "setup-6", chat_id=123, message_id=4444)
  monkeypatch.setattr(setup_card.settings, "auto_trade_telegram_single_root_card", True)
  monkeypatch.setattr(
    setup_card.settings, "auto_trade_telegram_delete_root_on_terminal", False,
  )
  calls = []

  async def delete_fn(chat_id, message_id):
    calls.append("delete")

  async def edit_fn(chat_id, message_id, text):
    calls.append("edit")

  await setup_card.kill_setup_card(
    client, "setup-6", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert calls == ["edit"]
  card = await setup_card.load_forming_card(client, "setup-6")
  assert card is not None
  assert int(card["message_id"]) == 4444
  # Root mapping retained for late replies/audit under single-root-card.
  assert await setup_card.load_telegram_root_message_id(client, "setup-6") == 4444


@pytest.mark.asyncio
async def test_delete_root_flag_true_still_deletes(monkeypatch):
  client = redis_state.get_client()
  await setup_card.save_forming_card(client, "setup-del", chat_id=123, message_id=3333)
  monkeypatch.setattr(setup_card.settings, "auto_trade_telegram_single_root_card", True)
  monkeypatch.setattr(
    setup_card.settings, "auto_trade_telegram_delete_root_on_terminal", True,
  )
  deleted = []

  async def delete_fn(chat_id, message_id):
    deleted.append((chat_id, message_id))

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("should delete")

  await setup_card.kill_setup_card(
    client, "setup-del", reason_code="expired",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )
  assert deleted == [(123, 3333)]
  assert await setup_card.load_telegram_root_message_id(client, "setup-del") is None


@pytest.mark.asyncio
async def test_load_forming_card_reads_legacy_scalar_format(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(setup_card.settings, "telegram_owner_id", 999)
  await client.set(setup_card.forming_message_key("setup-7"), "12345", ex=60)

  card = await setup_card.load_forming_card(client, "setup-7")

  assert card == {"chat_id": 999, "message_id": 12345}


@pytest.mark.asyncio
async def test_identical_status_edit_is_a_local_successful_noop():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-identical")
  status = "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor"
  text = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    status,
    "🔴 <b>SELL · Trendline Reaction</b>",
  ])
  await setup_card.save_forming_card(
    client,
    "setup-identical",
    chat_id=123,
    message_id=7001,
    text=text,
  )
  await setup_card.save_forming_card_status(
    client,
    "setup-identical",
    status,
    state="plan_published",
  )
  edits = []

  async def edit_fn(chat_id, message_id, updated):
    edits.append((chat_id, message_id, updated))

  assert await setup_card.edit_forming_card_status(
    client,
    "setup-identical",
    status,
    state="plan_published",
    edit_fn=edit_fn,
  )
  assert edits == []


@pytest.mark.asyncio
async def test_not_modified_status_edit_is_treated_as_success():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-not-modified")
  await setup_card.save_forming_card(
    client,
    "setup-not-modified",
    chat_id=123,
    message_id=7002,
    text="\n".join([
      "🔎 <b>XAU M5 · SETUP FORMING</b>",
      "🟡 <b>QUEUED</b> · worker acknowledgement pending",
      "🔴 <b>SELL · Trendline Reaction</b>",
    ]),
  )

  async def edit_fn(chat_id, message_id, updated):
    raise TelegramBadRequest(
      method=None,
      message="Bad Request: message is not modified",
    )

  assert await setup_card.edit_forming_card_status(
    client,
    "setup-not-modified",
    "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor",
    state="plan_published",
    edit_fn=edit_fn,
  )
  card = await setup_card.load_forming_card(client, "setup-not-modified")
  assert card is not None
  assert "PLAN PUBLISHED" in card["text"]


@pytest.mark.asyncio
async def test_card_status_is_monotonic_after_plan_publication():
  client = redis_state.get_client()
  await _confirmed_setup(client, "setup-monotonic")
  await setup_card.save_forming_card(
    client,
    "setup-monotonic",
    chat_id=123,
    message_id=7003,
    text="\n".join([
      "🔎 <b>XAU M5 · SETUP FORMING</b>",
      "🟡 <b>QUEUED</b> · worker acknowledgement pending",
      "🔴 <b>SELL · Trendline Reaction</b>",
    ]),
  )

  async def edit_fn(chat_id, message_id, updated):
    return None

  updates = [
    ("queued", "🟡 <b>QUEUED</b> · worker acknowledgement pending"),
    ("preflight", "🟡 <b>PREFLIGHT</b> · dynamic execution checks in progress"),
    (
      "plan_published",
      "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor",
    ),
    (
      "waiting_retest",
      "🟠 <b>WAITING RETEST</b> · executable quote is outside the zone",
    ),
    ("queued", "🟡 <b>QUEUED</b> · worker acknowledgement pending"),
  ]
  for state, status in updates:
    assert await setup_card.edit_forming_card_status(
      client,
      "setup-monotonic",
      status,
      state=state,
      edit_fn=edit_fn,
    )

  snapshot = await setup_card.load_forming_card_status_snapshot(
    client,
    "setup-monotonic",
  )
  assert snapshot is not None
  assert snapshot.state == "plan_published"
  card = await setup_card.load_forming_card(client, "setup-monotonic")
  assert card is not None
  assert "PLAN PUBLISHED" in card["text"]
  assert "WAITING RETEST" not in card["text"]


@pytest.mark.real_redis
@pytest.mark.asyncio
async def test_real_redis_concurrent_card_status_keeps_highest_priority():
  configured = os.getenv("REAL_REDIS_URL")
  if not configured:
    pytest.skip("REAL_REDIS_URL is required")
  source = configured.rsplit("/", 1)[0]
  client = Redis.from_url(f"{source}/12", decode_responses=True)
  await client.flushdb()
  setup_id = "real-redis-monotonic"
  try:
    await asyncio.gather(
      setup_card.save_forming_card_status(
        client,
        setup_id,
        "🟡 <b>QUEUED</b> · worker acknowledgement pending",
        state="queued",
      ),
      setup_card.save_forming_card_status(
        client,
        setup_id,
        "🟢 <b>PLAN PUBLISHED</b> · TradePlan V7 sent to executor",
        state="plan_published",
      ),
      setup_card.save_forming_card_status(
        client,
        setup_id,
        "🟠 <b>WAITING RETEST</b> · executable quote is outside the zone",
        state="waiting_retest",
      ),
    )

    snapshot = await setup_card.load_forming_card_status_snapshot(
      client,
      setup_id,
    )
    assert snapshot is not None
    assert snapshot.state == "plan_published"
    assert snapshot.priority == 100
  finally:
    await client.flushdb()
    await client.aclose()


def _strategy_match_for_card(setup_id: str = "setup-publish-card") -> object:
  from app.autotrade.strategy_match import StrategyMatch

  return StrategyMatch(
    version=1,
    match_id=setup_id,
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-31T13:03:00+00:00",
    issued_at=1_785_502_980,
    expires_at=1_785_503_400,
    strategy="Key Level Reaction",
    strategy_mode="with_bias",
    direction="BUY",
    key_level=4050.68,
    entry_low=4048.73,
    entry_high=4052.63,
    current_price=4051.6,
    confluence=2,
    reasons=("key reaction x13", "key reaction 4050.68 x13"),
    atr=4.0,
    structure_swing=10.0,
    targets_pips=(20, 40, 60),
    tags=("key_level",),
    structural_source="key_level",
    structural_kind="key_level",
    structural_timeframe="M5",
    reaction_type="sweep_reclaim",
    htf_bias="up (H1)",
  )


@pytest.mark.asyncio
async def test_ensure_plan_published_root_card_creates_missing_card():
  """Direct-publish path must create the first PLAN PUBLISHED root card."""
  client = redis_state.get_client()
  setup_id = "setup-publish-card"
  await _confirmed_setup(client, setup_id)
  match = _strategy_match_for_card(setup_id)
  sent = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=4242)

  async def edit_fn(chat_id, message_id, text):
    raise AssertionError("edit should not run when no card exists yet")

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )

  assert message_id == 4242
  assert len(sent) == 1
  text = sent[0]
  assert "SETUP FORMING" in text
  assert "PLAN PUBLISHED" in text
  assert "Trade area" in text
  assert "Price now" in text
  assert "Entry zone" in text
  assert "Key level" in text
  assert "Stop" in text
  assert "Context" in text
  assert "Copy draft" not in text
  assert "<code>" not in text
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert card["message_id"] == 4242
  # Reply-thread anchors used by take_profit / stop_moved / position_closed.
  assert await client.get(setup_card.forming_message_key(setup_id))
  assert await client.get(setup_card.telegram_root_message_key(setup_id))
  root = await setup_card.load_telegram_root_message_id(client, setup_id)
  assert root == 4242
  status = await setup_card.load_forming_card_status(client, setup_id)
  assert status is not None
  assert "PLAN PUBLISHED" in status


@pytest.mark.asyncio
async def test_ensure_plan_published_root_card_threads_tp_sl_close_replies(monkeypatch):
  """TP archive / trailing SL / close must reply_to the ensured root card."""
  from app.autotrade import delivery

  client = redis_state.get_client()
  setup_id = "setup-publish-thread"
  await _confirmed_setup(client, setup_id)
  match = _strategy_match_for_card(setup_id)

  async def send_fn(text, **kwargs):
    return SimpleNamespace(message_id=6060)

  async def edit_fn(chat_id, message_id, text):
    return None

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )
  assert message_id == 6060

  calls = []
  edited = []

  async def sent(text, **kwargs):
    calls.append((text, kwargs))
    return SimpleNamespace(message_id=7000 + len(calls))

  async def fake_edit(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  monkeypatch.setattr(delivery, "edit_scanner_message_text", fake_edit)

  for event in (
    {
      "type": "take_profit",
      "match_id": setup_id,
      "message": "TP1 +30 pips closed volume 200",
      "position_id": 1,
      "stop_pips": 65,
    },
    {
      "type": "stop_moved",
      "match_id": setup_id,
      "message": "🛡 ApexVoid Algo stop → 4,044.91 (breakeven)",
      "price": 4044.91,
      "position_id": 1,
    },
    {
      "type": "position_closed",
      "match_id": setup_id,
      "message": "Highest TP archived TP3 · +81.0 pips",
      "target_pips": 81.0,
      "position_id": 1,
    },
  ):
    await delivery._deliver_auto_trade_event(
      client,
      event,
      profile="internal",
      chat_id=123,
      send=sent,
    )

  # TP creates the manage reply; BE/trail is head-only; close edits manage.
  assert len(calls) == 1
  assert calls[0][1]["reply_to"] == 6060
  assert "🎯" in calls[0][0] and "TP1" in calls[0][0]
  close_edits = [text for _, _mid, text in edited if "POSITION CLOSED" in text]
  assert close_edits
  assert "TP3" in close_edits[-1] or "+81.0" in close_edits[-1]
  # Trailing / BE must also patch the root Stop line (no BE/trail reply).
  card = await setup_card.load_forming_card(client, setup_id)
  assert card is not None
  assert "4,044.91" in card["text"] or "4044.91" in card["text"]
  assert edited

@pytest.mark.asyncio
async def test_ensure_plan_published_root_card_edits_existing_status_only():
  client = redis_state.get_client()
  setup_id = "setup-publish-existing"
  await _confirmed_setup(client, setup_id)
  match = _strategy_match_for_card(setup_id)
  original = "\n".join([
    "🔎 <b>XAU M5 · SETUP FORMING</b>",
    "🟡 <b>QUEUED</b> · worker acknowledgement pending",
    "🔴 <b>SELL · Key Level Reaction</b>",
  ])
  await setup_card.save_forming_card(
    client, setup_id, chat_id=123, message_id=777, text=original,
  )
  sent = []
  edited = []

  async def send_fn(text, **kwargs):
    sent.append(text)
    return SimpleNamespace(message_id=999)

  async def edit_fn(chat_id, message_id, text):
    edited.append((chat_id, message_id, text))

  message_id = await setup_card.ensure_plan_published_root_card(
    client,
    match,
    chat_id=123,
    send_fn=send_fn,
    edit_fn=edit_fn,
  )

  assert message_id == 777
  assert sent == []
  assert len(edited) == 1
  assert "PLAN PUBLISHED" in edited[0][2]
  assert "Key Level Reaction" in edited[0][2]
  assert "QUEUED" not in edited[0][2]