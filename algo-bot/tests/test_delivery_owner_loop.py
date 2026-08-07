"""Regression: a long Telegram flood ban must not spam the owner delivery
loop's logs while it waits the ban out.

Live incident 2026-08-07: once app/bot/client.py started raising instead of
sleeping out a long flood ban, _auto_trade_owner_events_loop's generic
except caught the raised TelegramRetryAfter and retried the same stuck
cursor entry every 5s with a full traceback logged each time - thousands of
duplicate error blocks over the life of an ~11-hour ban. Retrying the same
entry forever is correct (the ban is chat-wide, so skipping ahead wouldn't
help, and no notification is silently dropped), so this only asserts the
backoff interval and log level changed, not the retry-forever behavior.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramRetryAfter

from app.autotrade import delivery
from app.core.config import runtime_config
from app.persistence import redis_state

pytestmark = pytest.mark.asyncio


async def test_owner_loop_backs_off_instead_of_retrying_every_5s_on_flood_limit(
  monkeypatch,
):
  client = redis_state.get_client()
  stream = runtime_config.contract.streams.events
  await client.xadd(stream, {"payload": json.dumps({"type": "opened"})})
  # Bootstrap the cursor to the stream's start so xread returns the entry
  # just added, rather than the loop's normal "skip to latest" bootstrap
  # (which would place the cursor after it, waiting for future entries).
  await client.set(delivery._CURSOR_KEY, "0-0")

  async def flood_limited(*args, **kwargs):
    raise TelegramRetryAfter(
      method=SimpleNamespace(chat_id=123),
      message="Too Many Requests: retry after 39856",
      retry_after=39856,
    )

  monkeypatch.setattr(delivery, "_process_owner_entries", flood_limited)

  sleeps: list[float] = []

  async def fake_sleep(seconds):
    sleeps.append(seconds)
    if len(sleeps) >= 2:
      raise asyncio.CancelledError()

  monkeypatch.setattr(delivery.asyncio, "sleep", fake_sleep)

  with pytest.raises(asyncio.CancelledError):
    await delivery._auto_trade_owner_events_loop(chat_id=123)

  # Backs off a full _OWNER_LOOP_FLOOD_BACKOFF_SECONDS each time, not the
  # 5s generic-failure interval - the entire point of the fix.
  assert sleeps == [
    delivery._OWNER_LOOP_FLOOD_BACKOFF_SECONDS,
    delivery._OWNER_LOOP_FLOOD_BACKOFF_SECONDS,
  ]
