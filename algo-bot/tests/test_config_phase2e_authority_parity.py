"""Process-isolated parity for the Phase 2E operational read surface."""

from functools import lru_cache
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.no_database

_ALGO_ROOT = Path(__file__).parents[1]
_SAFE = {
  "PYTHONPATH": str(_ALGO_ROOT),
  "TELEGRAM_BOT_TOKEN": "phase-2e-main-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "SIGNAL_PUBLIC_CHANNEL_ID": "-100987654321",
  "TELEGRAM_OWNER_ID": "431",
  "POSTGRES_PASSWORD": "phase-2e-postgres",
  "LOG_LEVEL": "DEBUG",
  "LOG_DIR": "/tmp/phase-2e-logs",
  "LOG_RETENTION_DAYS": "11",
  "LOG_FILE_ENABLED": "false",
  "SIGNAL_PUBLIC_SHOW_PIPS": "false",
  "SEQ_RESET_TZ": "Asia/Ho_Chi_Minh",
  "CALENDAR_CURRENCIES": "USD,EUR",
  "OIL_KEYWORDS": "crude,opec",
  "NEWS_BRIEF_HOUR": "9",
  "WEEKLY_REPORT_ENABLED": "true",
  "WEEKLY_REPORT_DOW": "5",
  "WEEKLY_REPORT_HOUR": "10",
  "WEEKLY_REPORT_SKIP_EMPTY": "true",
  "SESSION_ASIA_START": "23",
  "SESSION_LONDON_START": "8",
  "SESSION_NY_START": "14",
  "WATCHER_CTRADER_STALE_SECONDS": "222",
  "TRACK_INTERVAL": "41",
  "XAU_LOOKBACK_H1_BARS": "401",
  "XAU_LOOKBACK_M15_BARS": "651",
  "XAU_LOOKBACK_M5_BARS": "1001",
  "XAU_LOOKBACK_M1_BARS": "151",
  "SCANNER_WINDOW": "501",
}

_PROBE = r"""
import json
from app.analysis.ohlc_source import window_for_timeframe
from app.core.config import runtime_config, settings

def exact(value):
  return {"type": type(value).__name__, "value": value}

telegram = runtime_config.delivery.telegram
scanner_token = telegram.scanner_telegram_bot_token
main_token = runtime_config.bootstrap.telegram.bot_token
payload = {
  "runtime_type": type(runtime_config).__name__,
  "settings_type": type(settings).__name__,
  "logging": {
    "level": exact(runtime_config.bootstrap.logging.level),
    "directory": exact(runtime_config.bootstrap.logging.directory),
    "retention": exact(runtime_config.bootstrap.logging.retention_days),
    "file_enabled": exact(runtime_config.bootstrap.logging.file_enabled),
  },
  "telegram": {
    "main_token_type": type(main_token).__name__,
    "scanner_falls_back": scanner_token is None,
    "selected_scanner_token_type": type(scanner_token or main_token).__name__,
    "channel": exact(telegram.telegram_channel_id),
    "public_channel": exact(telegram.signal_public_channel_id),
    "owner": exact(telegram.telegram_owner_id),
    "owner_scope_enabled": telegram.telegram_owner_id is not None,
    "default_send_channel": exact(telegram.telegram_channel_id),
  },
  "calendar": {
    "enabled": exact(runtime_config.market_data.calendar.enabled),
    "thisweek": exact(runtime_config.market_data.calendar.feed_thisweek),
    "nextweek": exact(runtime_config.market_data.calendar.feed_nextweek),
    "user_agent": exact(runtime_config.market_data.calendar.user_agent),
    "currencies": exact(runtime_config.market_data.calendar.currencies),
    "oil_keywords": exact(runtime_config.market_data.calendar.oil_keywords),
    "timezone": exact(runtime_config.delivery.presentation.seq_reset_tz),
    "hour": exact(runtime_config.market_data.calendar.news_brief_hour),
  },
  "weekly": {
    "enabled": exact(runtime_config.delivery.reports.weekly.enabled),
    "weekday": exact(runtime_config.delivery.reports.weekly.day_of_week),
    "hour": exact(runtime_config.delivery.reports.weekly.utc_hour),
    "skip_empty": exact(runtime_config.delivery.reports.weekly.skip_empty),
    "asia": exact(runtime_config.market_data.sessions.asia_start),
    "london": exact(runtime_config.market_data.sessions.london_start),
    "ny": exact(runtime_config.market_data.sessions.ny_start),
    "timezone": exact(runtime_config.delivery.presentation.seq_reset_tz),
  },
  "watcher": {
    "public_pips": exact(telegram.public_show_pips),
    "owner": exact(telegram.telegram_owner_id),
    "tiingo_available": runtime_config.market_data.tiingo.api_key is not None,
    "stale": exact(runtime_config.market_data.watcher.ctrader_stale_seconds),
    "interval": exact(runtime_config.market_data.watcher.interval_seconds),
  },
  "ohlc": {
    "H1": exact(window_for_timeframe("H1")),
    "M15": exact(window_for_timeframe("M15")),
    "M5": exact(window_for_timeframe("M5")),
    "M1": exact(window_for_timeframe("M1")),
    "unknown": exact(window_for_timeframe("X2")),
  },
}
print(json.dumps(payload, sort_keys=True))
"""


@lru_cache(maxsize=2)
def _probe(authority: str) -> dict:
  environment = {
    key: value for key, value in os.environ.items() if key in {"PATH", "HOME"}
  }
  environment.update(_SAFE)
  environment["APEXVOID_CONFIG_AUTHORITY"] = authority
  process = subprocess.run(
    [sys.executable, "-c", _PROBE],
    cwd=_ALGO_ROOT,
    env=environment,
    capture_output=True,
    text=True,
  )
  assert process.returncode == 0, process.stderr
  return json.loads(process.stdout)


def _equal_section(name: str) -> None:
  assert _probe("legacy")[name] == _probe("canonical")[name]


def test_runtime_config_is_legacy_view_under_legacy_authority():
  assert _probe("legacy")["runtime_type"] == "LegacyCanonicalConfigView"


def test_runtime_config_is_python_model_under_canonical_authority():
  assert _probe("canonical")["runtime_type"] == "PythonRuntimeConfig"


def test_settings_export_remains_legacy_under_legacy_authority():
  assert _probe("legacy")["settings_type"] == "Settings"


def test_settings_export_remains_facade_under_canonical_authority():
  assert _probe("canonical")["settings_type"] == "CanonicalSettingsFacade"


def test_logging_inputs_equal_under_both_authorities():
  _equal_section("logging")


def test_telegram_wiring_equal_under_both_authorities():
  _equal_section("telegram")


def test_calendar_configuration_equal_under_both_authorities():
  _equal_section("calendar")


def test_weekly_report_configuration_equal_under_both_authorities():
  _equal_section("weekly")


def test_watcher_configuration_equal_under_both_authorities():
  _equal_section("watcher")


def test_ohlc_lookbacks_equal_under_both_authorities():
  _equal_section("ohlc")

