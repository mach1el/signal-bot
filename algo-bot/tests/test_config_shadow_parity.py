"""Four-fixture field, derived-property, and invalid-input shadow parity."""

from unittest.mock import patch

import pytest

from app.configuration.parity import compare_derived_properties
from app.configuration.parity import compare_legacy_settings
from app.configuration.shadow_loader import load_shadow_configuration
from app.configuration.source_types import ConfigurationSourceBundle
from app.core.config import Settings
from app.configuration.environment_option_resolution import resolve_environment_options
from tests.config_characterization import REQUIRED_ENV
from tests.config_characterization import root_compose_environment


pytestmark = pytest.mark.no_database

_CTRADER_SAFE = {
  "POSTGRES_PASSWORD": "phase-2c-postgres-sentinel",
  "CTRADER_ACCESS_TOKEN": "phase-2c-access-sentinel",
  "CTRADER_ACCOUNT_ID": "123456",
  "CTRADER_CLIENT_ID": "phase-2c-client-id",
  "CTRADER_CLIENT_SECRET": "phase-2c-client-secret-sentinel",
  "CTRADER_REFRESH_TOKEN": "phase-2c-refresh-sentinel",
}


def _complete(environment):
  return {**environment, **_CTRADER_SAFE}


def _legacy(environment):
  with patch.dict("os.environ", environment, clear=True):
    return Settings(_env_file=None)


def _shadow(environment):
  return load_shadow_configuration(ConfigurationSourceBundle(
    process_environment=environment,
  ))


def _fixtures():
  return {
    "direct_conservative": _complete(dict(REQUIRED_ENV)),
    "direct_demo_eval": _complete({
      **REQUIRED_ENV,
      "AUTO_TRADE_PROFILE": "demo_eval",
    }),
    "root_compose_demo_eval": _complete({
      **root_compose_environment(),
      **REQUIRED_ENV,
    }),
    "test_conftest": _complete({
      "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
      "TELEGRAM_CHAT_ID": "-100123456789",
      "DATABASE_URL": (
        "postgresql://apexvoid:apexvoid@localhost:55432/signals"
      ),
    }),
  }


@pytest.mark.parametrize("fixture_name", tuple(_fixtures()))
def test_shadow_fixture_parity_316_of_316(fixture_name):
  environment = _fixtures()[fixture_name]
  report = compare_legacy_settings(_legacy(environment), _shadow(environment))
  mismatches = [row for row in report.rows if row.status.value != "equal"]
  assert (report.equal_count, report.total_count) == (316, 316), mismatches


def _assert_named_fixture(name):
  environment = _fixtures()[name]
  report = compare_legacy_settings(_legacy(environment), _shadow(environment))
  assert (report.equal_count, report.total_count) == (316, 316)


def test_shadow_direct_conservative_parity_316_of_316():
  _assert_named_fixture("direct_conservative")


def test_shadow_direct_demo_parity_316_of_316():
  _assert_named_fixture("direct_demo_eval")


def test_shadow_compose_demo_parity_316_of_316():
  _assert_named_fixture("root_compose_demo_eval")


def test_shadow_test_environment_parity_316_of_316():
  _assert_named_fixture("test_conftest")


def test_named_four_fixture_parity_contracts_exist():
  names = tuple(_fixtures())
  assert names == (
    "direct_conservative",
    "direct_demo_eval",
    "root_compose_demo_eval",
    "test_conftest",
  )


def test_shadow_derived_property_parity_16_of_16():
  comparisons = {}
  for name, environment in _fixtures().items():
    comparisons[name] = compare_derived_properties(
      _legacy(environment), _shadow(environment),
    )
  assert sum(
    value for fixture in comparisons.values() for value in fixture.values()
  ) == 16
  assert all(all(fixture.values()) for fixture in comparisons.values())


def test_known_cross_fixture_divergences_are_preserved():
  fixtures = _fixtures()
  direct = _shadow(fixtures["direct_demo_eval"]).config
  compose = _shadow(fixtures["root_compose_demo_eval"]).config
  assert direct is not None and compose is not None
  assert direct.strategies.mapped_zone.enabled is True
  assert compose.strategies.mapped_zone.enabled is False
  assert direct.actionability.gates.market_map_guard_enabled is True
  assert compose.actionability.gates.market_map_guard_enabled is False


def test_provenance_exists_for_every_legacy_field_in_every_fixture():
  for environment in _fixtures().values():
    result = _shadow(environment)
    report = compare_legacy_settings(_legacy(environment), result)
    sources = result.trace.by_path()
    assert len(report.rows) == 316
    assert all(row.canonical_path in sources for row in report.rows)


def test_profile_value_provenance():
  result = _shadow(_fixtures()["direct_demo_eval"])
  source = result.trace.by_path()["strategies.mapped_zone.enabled"]
  assert source.source_kind.value == "profile"
  assert source.profile_name == "demo_eval"


def test_process_env_value_provenance():
  result = _shadow(_fixtures()["root_compose_demo_eval"])
  source = result.trace.by_path()["strategies.mapped_zone.enabled"]
  assert source.source_kind.value == "process_environment"
  assert source.source_name == "AUTO_TRADE_MAPPED_ZONE_ENABLED"


def test_derived_rule_provenance():
  result = _shadow(_fixtures()["direct_conservative"])
  source = result.trace.by_path()[
    "actionability.gates.market_map_guard_enabled"
  ]
  assert source.source_kind.value == "derived_compatibility_rule"
  assert source.compatibility_rule == "market_map_guard_inherits_mapped_zone"


def test_secret_provenance_is_redacted():
  environment = _fixtures()["direct_conservative"]
  result = _shadow(environment)
  source = result.trace.by_path()["bootstrap.telegram.bot_token"]
  assert source.secret is True
  assert environment["TELEGRAM_BOT_TOKEN"] not in repr(source)
  assert not hasattr(source, "value")


def _legacy_rejects(environment):
  try:
    with patch.dict("os.environ", environment, clear=True):
      resolve_environment_options(environment)
      Settings(_env_file=None)
  except ValueError:
    return True
  return False


@pytest.mark.parametrize("invalid", (
  {"AUTO_TRADE_PROFILE": "unsupported"},
  {
    "AUTO_TRADE_PROFILE": "demo_eval",
    "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT": "false",
  },
  {
    "XAU_ZONE_MIN_WIDTH_PRICE": "8",
    "XAU_ZONE_PREFERRED_MIN_WIDTH_PRICE": "4",
  },
  {
    "AUTO_TRADE_REACTION_MARKET_FRACTION": "0.8",
    "AUTO_TRADE_REACTION_SCALE_FRACTION": "0.3",
  },
  {
    "AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS": "50",
    "AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS": "60",
  },
  {"AUTO_TRADE_BE_BUFFER_TICKS": "1000"},
  {
    "AUTO_TRADE_BE_BUFFER_TICKS": "17",
    "AUTO_TRADE_BE_BUFFER_PIPS": "18",
  },
  {
    "AUTO_TRADE_XAU_PIP_SIZE": "0.01",
    "AUTO_TRADE_PIP_SIZE": "0.02",
  },
  {"AUTO_TRADE_CONTRACT_MODE": "legacy_v6"},
))
def test_invalid_input_semantic_parity(invalid):
  environment = _complete({**REQUIRED_ENV, **invalid})
  assert _legacy_rejects(environment)
  assert not _shadow(environment).success
