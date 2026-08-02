"""Deterministic source precedence, alias, and provenance tests."""

import pytest

from app.configuration.resolver import resolve_configuration
from app.configuration.source_types import SourceKind


pytestmark = pytest.mark.no_database


def _resolve(**overrides):
  values = {
    "init_values": {},
    "process_environment": {},
    "dotenv_values": {},
    "file_secret_values": {},
  }
  values.update(overrides)
  return resolve_configuration(**values)


def test_source_precedence_schema_profile_dotenv_process_init():
  result = _resolve(
    init_values={"runtime.auto_trade.enabled": False},
    process_environment={"AUTO_TRADE_ENABLED": "true"},
    dotenv_values={
      "AUTO_TRADE_PROFILE": "demo_eval",
      "AUTO_TRADE_ENABLED": "false",
    },
  )
  source = result.trace.by_path()["runtime.auto_trade.enabled"]
  assert result.flat_values["runtime.auto_trade.enabled"] is False
  assert source.source_kind is SourceKind.INIT_VALUE
  assert len(source.overridden_lower_precedence_sources) == 4


def test_alias_resolution_is_per_source_layer():
  result = _resolve(
    process_environment={"AUTO_TRADE_PIP_SIZE": "0.02"},
    dotenv_values={"AUTO_TRADE_XAU_PIP_SIZE": "0.01"},
  )
  path = "contract.instrument.pip_size"
  assert result.flat_values[path] == 0.02
  assert result.trace.by_path()[path].supplied_alias == "AUTO_TRADE_PIP_SIZE"


def test_process_alias_overrides_dotenv_canonical():
  result = _resolve(
    process_environment={"AUTO_TRADE_PIP_SIZE": "0.02"},
    dotenv_values={"AUTO_TRADE_XAU_PIP_SIZE": "0.01"},
  )
  source = result.trace.by_path()["contract.instrument.pip_size"]
  assert source.source_kind is SourceKind.PROCESS_ENV
  assert source.source_name == "AUTO_TRADE_PIP_SIZE"


def test_equal_alias_values_are_accepted_with_warning():
  result = _resolve(process_environment={
    "AUTO_TRADE_XAU_PIP_SIZE": "0.01",
    "AUTO_TRADE_PIP_SIZE": "0.010",
  })
  assert not result.conflicts
  assert {warning.code for warning in result.warnings} >= {
    "duplicate_source_names",
    "deprecated_alias",
  }


def test_conflicting_alias_values_fail():
  result = _resolve(process_environment={
    "AUTO_TRADE_XAU_PIP_SIZE": "0.01",
    "AUTO_TRADE_PIP_SIZE": "0.02",
  })
  assert [item.code for item in result.conflicts] == [
    "source_alias_conflict",
  ]


def test_deprecated_alias_emits_warning():
  result = _resolve(dotenv_values={"AUTO_TRADE_PIP_SIZE": "0.02"})
  warning = next(item for item in result.warnings if item.code == "deprecated_alias")
  assert warning.canonical_env == "AUTO_TRADE_XAU_PIP_SIZE"
  assert warning.supplied_alias == "AUTO_TRADE_PIP_SIZE"
  assert warning.path == "contract.instrument.pip_size"
  assert warning.source_kind is SourceKind.DOTENV


def test_init_value_overrides_process_canonical():
  result = _resolve(
    init_values={"runtime.auto_trade.enabled": False},
    process_environment={"AUTO_TRADE_ENABLED": "true"},
  )
  source = result.trace.by_path()["runtime.auto_trade.enabled"]
  assert source.source_kind is SourceKind.INIT_VALUE
  assert result.flat_values[source.path] is False


def test_raw_list_and_legacy_csv_parsing_remain_distinct():
  result = _resolve(process_environment={
    "AUTO_TRADE_TP_WEIGHTS": "20, 30,50",
    "AUTO_TRADE_TARGET_PLANS_PIPS": "40,80,120",
  })
  assert result.flat_values["execution.targeting.tp_weights"] == [20, 30, 50]
  assert result.flat_values[
    "execution.targeting.default_ladder_pips"
  ] == "40,80,120"


def test_parse_error_is_secret_safe():
  result = _resolve(process_environment={"CTRADER_ACCOUNT_ID": "not-an-id"})
  conflict = result.conflicts[0]
  assert conflict.code == "source_parse_error"
  assert "not-an-id" not in conflict.message


def test_optional_string_preserves_whitespace_and_blank():
  whitespace = _resolve(process_environment={
    "SCANNER_TELEGRAM_BOT_TOKEN": "  token with space  ",
  })
  blank = _resolve(process_environment={"SCANNER_TELEGRAM_BOT_TOKEN": ""})
  path = "delivery.telegram.scanner_telegram_bot_token"
  assert whitespace.flat_values[path] == "  token with space  "
  assert blank.flat_values[path] == ""


def test_optional_integer_accepts_signed_value_and_rejects_blank():
  signed = _resolve(process_environment={"TELEGRAM_OWNER_ID": " -7 "})
  blank = _resolve(process_environment={"TELEGRAM_OWNER_ID": ""})
  assert signed.flat_values["delivery.telegram.telegram_owner_id"] == -7
  assert blank.conflicts[0].code == "source_parse_error"


def test_decimal_and_float_sources_use_declared_types():
  result = _resolve(process_environment={
    "CTRADER_TOKEN_CHECK_INTERVAL_HOURS": " 2.5 ",
    "AUTO_TRADE_XAU_PIP_SIZE": " 0.01 ",
  })
  assert str(result.flat_values[
    "bootstrap.ctrader.token_rotation.check_interval_hours"
  ]) == "2.5"
  assert result.flat_values["contract.instrument.pip_size"] == 0.01
