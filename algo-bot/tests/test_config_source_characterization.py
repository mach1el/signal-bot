"""Characterize canonical source precedence (Phase 2I final)."""

from pathlib import Path

import pytest

from app.configuration.environment_option_resolution import (
  CanonicalEnvironmentOption,
  parse_bool,
  parse_int,
)
from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.source_types import ConfigurationSourceBundle


pytestmark = pytest.mark.no_database

_REQUIRED_ENV = {
  "TELEGRAM_BOT_TOKEN": "phase-2c-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2c-postgres",
}


def _load(
  *,
  environment: dict[str, str] | None = None,
  dotenv_values: dict[str, str | None] | None = None,
  init_values: dict[str, object] | None = None,
):
  return load_python_canonical_settings(ConfigurationSourceBundle(
    process_environment={**_REQUIRED_ENV, **(environment or {})},
    dotenv_values=dotenv_values or {},
    init_values=init_values or {},
  ))


def test_canonical_source_precedence_schema_dotenv_process_init():
  assert _load().config.bootstrap.logging.level == "INFO"
  assert _load(
    dotenv_values={"LOG_LEVEL": "dotenv"},
  ).config.bootstrap.logging.level == "dotenv"
  assert _load(
    environment={"LOG_LEVEL": "process"},
    dotenv_values={"LOG_LEVEL": "dotenv"},
  ).config.bootstrap.logging.level == "process"
  assert _load(
    environment={"LOG_LEVEL": "process"},
    dotenv_values={"LOG_LEVEL": "dotenv"},
    init_values={"bootstrap.logging.level": "init"},
  ).config.bootstrap.logging.level == "init"


@pytest.mark.parametrize(
  ("raw", "expected"),
  [("true", True), ("false", False), ("1", True), ("0", False)],
)
def test_bool_parser_preserved(raw, expected):
  assert parse_bool(raw) is expected


@pytest.mark.parametrize(("raw", "expected"), [("7", 7), ("0", 0)])
def test_int_parser_preserved(raw, expected):
  assert parse_int(raw) == expected


def test_canonical_environment_option_dataclass_shape():
  option = CanonicalEnvironmentOption(
    canonical_name="DEMO",
    deprecated_aliases=(),
    parser=parse_bool,
    resolved_value=True,
    source_name="process_environment",
    conflict=False,
    warnings=(),
    aliases_present=(),
  )
  assert option.canonical_name == "DEMO"
