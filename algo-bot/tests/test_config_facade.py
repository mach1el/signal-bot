"""Exact compatibility and immutability checks for the inactive facade."""

import pytest

from app.configuration.facade import CanonicalSettingsFacade
from app.configuration.generated.legacy_access import DERIVED_LEGACY_PROPERTIES
from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS
from tests.test_config_shadow_parity import _fixtures
from tests.test_config_shadow_parity import _legacy
from tests.test_config_shadow_parity import _shadow


pytestmark = pytest.mark.no_database


def _facade_fixture(name):
  environment = _fixtures()[name]
  legacy = _legacy(environment)
  shadow = _shadow(environment)
  assert shadow.config is not None
  return environment, legacy, CanonicalSettingsFacade(shadow.config)


def _assert_direct_fixture(name):
  _, legacy, facade = _facade_fixture(name)
  comparisons = [
    (
      attribute,
      getattr(legacy, attribute),
      getattr(facade, attribute),
    )
    for attribute in DIRECT_LEGACY_PATHS
  ]
  assert len(comparisons) == 316
  assert all(legacy_value == facade_value for _, legacy_value, facade_value in comparisons)
  assert all(type(legacy_value) is type(facade_value) for _, legacy_value, facade_value in comparisons)


def test_facade_direct_conservative_parity():
  _assert_direct_fixture("direct_conservative")


def test_facade_direct_demo_parity():
  _assert_direct_fixture("direct_demo_eval")


def test_facade_compose_demo_parity():
  _assert_direct_fixture("root_compose_demo_eval")


def test_facade_test_environment_parity():
  _assert_direct_fixture("test_conftest")


def test_facade_derived_property_parity():
  count = 0
  for name in _fixtures():
    _, legacy, facade = _facade_fixture(name)
    for attribute in DERIVED_LEGACY_PROPERTIES:
      expected = getattr(legacy, attribute)
      actual = getattr(facade, attribute)
      assert actual == expected
      assert type(actual) is type(expected)
      count += 1
  assert count == 16


def test_facade_preserves_exact_python_types():
  _, legacy, facade = _facade_fixture("direct_demo_eval")
  for attribute in (*DIRECT_LEGACY_PATHS, *DERIVED_LEGACY_PROPERTIES):
    assert type(getattr(facade, attribute)) is type(getattr(legacy, attribute))


def test_facade_unknown_attribute_raises_attribute_error():
  _, _, facade = _facade_fixture("direct_conservative")
  with pytest.raises(AttributeError, match="unsupported legacy attribute"):
    facade.not_a_real_setting


def test_facade_rejects_assignment():
  _, _, facade = _facade_fixture("direct_conservative")
  with pytest.raises(TypeError, match="immutable"):
    facade.log_level = "DEBUG"
  with pytest.raises(TypeError, match="immutable"):
    facade._config = facade.canonical_config


def test_facade_rejects_deletion():
  _, _, facade = _facade_fixture("direct_conservative")
  with pytest.raises(TypeError, match="immutable"):
    del facade.log_level


def test_facade_repr_is_secret_safe():
  environment, _, facade = _facade_fixture("direct_conservative")
  rendered = repr(facade)
  assert rendered == (
    "CanonicalSettingsFacade(direct_fields=316, derived_fields=4, immutable=True)"
  )
  for name in (
    "TELEGRAM_BOT_TOKEN",
    "DATABASE_URL",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_REFRESH_TOKEN",
  ):
    if name in environment:
      assert environment[name] not in rendered


def test_facade_dir_contains_supported_legacy_names():
  _, _, facade = _facade_fixture("direct_conservative")
  names = dir(facade)
  assert all(name in names for name in DIRECT_LEGACY_PATHS)
  assert all(name in names for name in DERIVED_LEGACY_PROPERTIES)


def test_facade_exposes_only_immutable_canonical_config():
  _, _, facade = _facade_fixture("direct_conservative")
  with pytest.raises(Exception):
    facade.canonical_config.runtime.auto_trade_enabled = True
