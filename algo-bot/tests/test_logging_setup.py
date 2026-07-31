"""Service-managed daily rotating file logs."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core.logging_setup import configure_logging


pytestmark = pytest.mark.no_database


def test_configure_logging_writes_and_keeps_console(tmp_path: Path):
  info = configure_logging(
    level="INFO",
    log_dir=str(tmp_path),
    log_file_name="algo-bot.log",
    retention_days=7,
    enable_file=True,
  )
  assert info["file"] == str(tmp_path / "algo-bot.log")
  logging.getLogger("test.logger").info("hello-host-log")
  for handler in logging.getLogger().handlers:
    handler.flush()
  text = (tmp_path / "algo-bot.log").read_text(encoding="utf-8")
  assert "hello-host-log" in text
  assert any(isinstance(h, logging.StreamHandler) for h in logging.getLogger().handlers)


def test_configure_logging_console_only_when_file_disabled(tmp_path: Path):
  info = configure_logging(
    level="INFO",
    log_dir=str(tmp_path),
    enable_file=False,
  )
  assert info["file"] is None
  assert not (tmp_path / "algo-bot.log").exists()
