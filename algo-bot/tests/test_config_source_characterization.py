"""Characterize legacy source precedence and raw alias behavior for Phase 2C."""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.config import Settings
from app.configuration.environment_option_resolution import CanonicalEnvironmentOption
from app.configuration.environment_option_resolution import parse_bool
from app.configuration.environment_option_resolution import parse_int


pytestmark = pytest.mark.no_database

_REQUIRED_ENV = {
  "TELEGRAM_BOT_TOKEN": "phase-2c-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
}


def _settings(
  *,
  environment: dict[str, str] | None = None,
  env_file: Path | None = None,
  **init_values,
) -> Settings:
  values = {**_REQUIRED_ENV, **(environment or {})}
  with patch.dict("os.environ", values, clear=True):
    return Settings(_env_file=env_file, **init_values)


def test_legacy_source_precedence_schema_dotenv_process_init(tmp_path):
  env_file = tmp_path / ".env"
  env_file.write_text("LOG_LEVEL=dotenv\n", encoding="utf-8")

  assert _settings().log_level == "INFO"
  assert _settings(env_file=env_file).log_level == "dotenv"
  assert _settings(
    environment={"LOG_LEVEL": "process"}, env_file=env_file,
  ).log_level == "process"
  assert _settings(
    environment={"LOG_LEVEL": "process"},
    env_file=env_file,
    log_level="init",
  ).log_level == "init"


@pytest.mark.parametrize(
  ("raw", "expected"),
  (("true", True), (" TRUE ", True), ("0", False), (" off ", False)),
)
def test_raw_boolean_parser_trims_and_normalizes(raw, expected):
  assert parse_bool(raw) is expected


def test_alias_contract_prefers_canonical_when_values_are_equal():
  option = CanonicalEnvironmentOption(
    "CANONICAL", ("DEPRECATED",), parse_int,
  ).resolve({"CANONICAL": " 7 ", "DEPRECATED": "7"})

  assert option.resolved_value == 7
  assert option.source_name == "CANONICAL"
  assert option.warnings == ("deprecated_variable:DEPRECATED",)


def test_alias_contract_rejects_conflicting_parsed_values():
  with pytest.raises(ValueError, match="conflicting environment aliases"):
    CanonicalEnvironmentOption(
      "CANONICAL", ("DEPRECATED",), parse_int,
    ).resolve({"CANONICAL": "7", "DEPRECATED": "8"})


def test_deprecated_be_name_is_tick_valued():
  settings = _settings(
    environment={"AUTO_TRADE_BE_BUFFER_PIPS": " 17 "},
  )
  assert settings.auto_trade_be_buffer_ticks == 17


def test_be_names_conflict_on_distinct_raw_tick_values():
  with pytest.raises(ValueError, match="BE_BUFFER_TICKS.*BE_BUFFER_PIPS"):
    _settings(environment={
      "AUTO_TRADE_BE_BUFFER_TICKS": "17",
      "AUTO_TRADE_BE_BUFFER_PIPS": "18",
    })
