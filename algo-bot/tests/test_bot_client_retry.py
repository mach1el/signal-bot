"""Regression: a Telegram flood-control ban must never freeze a task.

Live incident 2026-08-07: Telegram issued a genuine flood-control ban
(~39856s, ~11 hours) after repeated startup reconciliation passes burst
Telegram with unthrottled edits. _send_message_with_retry used to sleep
the full retry_after unconditionally - freezing whatever task called it
for 11 real hours instead of just failing that one send.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramRetryAfter

from app.bot import client as bot_client


pytestmark = pytest.mark.no_database


class _RetryAfterOnceBot:
  def __init__(self, retry_after: int):
    self.retry_after = retry_after
    self.calls = 0

  async def send_message(self, **kwargs):
    self.calls += 1
    if self.calls == 1:
      raise TelegramRetryAfter(
        method=SimpleNamespace(chat_id=kwargs.get("chat_id")),
        message="Too Many Requests: retry after " + str(self.retry_after),
        retry_after=self.retry_after,
      )
    return SimpleNamespace(message_id=1)


@pytest.mark.asyncio
async def test_short_retry_after_still_sleeps_and_succeeds(monkeypatch):
  # The common, intended case this retry loop exists for: a brief
  # per-second throttle should still be waited out transparently.
  sleeps: list[float] = []

  async def fake_sleep(seconds):
    sleeps.append(seconds)

  monkeypatch.setattr(bot_client.asyncio, "sleep", fake_sleep)
  fake_bot = _RetryAfterOnceBot(retry_after=5)

  result = await bot_client._send_message_with_retry(
    fake_bot, "hello", None, 123, None,
  )

  assert result.message_id == 1
  assert sleeps == [5]
  assert fake_bot.calls == 2


@pytest.mark.asyncio
async def test_long_retry_after_raises_instead_of_freezing_the_task(monkeypatch):
  slept: list[float] = []

  async def fake_sleep(seconds):
    slept.append(seconds)

  monkeypatch.setattr(bot_client.asyncio, "sleep", fake_sleep)
  fake_bot = _RetryAfterOnceBot(retry_after=39856)

  with pytest.raises(TelegramRetryAfter) as excinfo:
    await bot_client._send_message_with_retry(
      fake_bot, "hello", None, 123, None,
    )

  assert excinfo.value.retry_after == 39856
  # Never actually slept - a multi-hour flood ban must fail fast, not
  # block whatever task called this for the full duration.
  assert slept == []
  assert fake_bot.calls == 1
