"""One forming card per setup: post-or-edit, delete on terminal (Codex
Prompt P4).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.autotrade import setup_card
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  INVALIDATED,
  create_setup,
  transition_setup,
)
from app.persistence import redis_state


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
  assert card == {"chat_id": 123, "message_id": 9001}


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
  assert card == {"chat_id": 123, "message_id": 8888}


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
async def test_kill_setup_card_deletes_and_clears_storage():
  client = redis_state.get_client()
  await setup_card.save_forming_card(client, "setup-4", chat_id=123, message_id=5555)
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


@pytest.mark.asyncio
async def test_kill_setup_card_falls_back_to_terminal_edit_when_delete_fails():
  client = redis_state.get_client()
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


@pytest.mark.asyncio
async def test_delete_on_terminal_disabled_only_clears_storage(monkeypatch):
  client = redis_state.get_client()
  await setup_card.save_forming_card(client, "setup-6", chat_id=123, message_id=4444)
  monkeypatch.setattr(setup_card.settings, "delivery_delete_on_terminal", False)
  calls = []

  async def delete_fn(chat_id, message_id):
    calls.append("delete")

  async def edit_fn(chat_id, message_id, text):
    calls.append("edit")

  await setup_card.kill_setup_card(
    client, "setup-6", reason_code="structure_broke",
    delete_fn=delete_fn, edit_fn=edit_fn,
  )

  assert calls == []
  assert await setup_card.load_forming_card(client, "setup-6") is None


@pytest.mark.asyncio
async def test_load_forming_card_reads_legacy_scalar_format(monkeypatch):
  client = redis_state.get_client()
  monkeypatch.setattr(setup_card.settings, "telegram_owner_id", 999)
  await client.set(setup_card.forming_message_key("setup-7"), "12345", ex=60)

  card = await setup_card.load_forming_card(client, "setup-7")

  assert card == {"chat_id": 999, "message_id": 12345}
