"""Local authority selection and restart-style rollback rehearsal tests."""

from unittest.mock import patch

import pytest

from app.configuration.activation_rehearsal import rehearse_authority_rollback
from app.configuration.authority import ConfigurationAuthority
from app.configuration.authority import build_configuration_runtime_bundle
from app.configuration.source_types import ConfigurationSourceBundle
from app.core import config as active_config
from tests.test_config_shadow_parity import _fixtures
from tests.test_config_shadow_parity import _legacy


pytestmark = pytest.mark.no_database


def _inputs(name="direct_conservative"):
  environment = _fixtures()[name]
  return ConfigurationSourceBundle(process_environment=environment), _legacy(environment)


def test_authority_defaults_to_legacy():
  source, legacy = _inputs()
  bundle = build_configuration_runtime_bundle(
    source_bundle=source,
    legacy_settings=legacy,
  )
  assert bundle.selected_authority is ConfigurationAuthority.LEGACY
  assert bundle.selected_object is legacy
  assert bundle.authoritative_object is legacy
  assert bundle.selected_is_authoritative is True


def test_canonical_rehearsal_is_non_authoritative():
  source, legacy = _inputs()
  bundle = build_configuration_runtime_bundle(
    source_bundle=source,
    legacy_settings=legacy,
    requested_authority=ConfigurationAuthority.CANONICAL_REHEARSAL,
  )
  assert bundle.selected_object is bundle.canonical_facade
  assert bundle.authoritative_object is legacy
  assert bundle.selected_is_authoritative is False


def test_canonical_authority_selects_python_facade_as_authoritative():
  environment = {
    "TELEGRAM_BOT_TOKEN": "phase-2d2-token",
    "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
    "POSTGRES_PASSWORD": "phase-2d2-postgres",
  }
  legacy = _legacy(environment)
  bundle = build_configuration_runtime_bundle(
    source_bundle=ConfigurationSourceBundle(process_environment=environment),
    legacy_settings=legacy,
    requested_authority=ConfigurationAuthority.CANONICAL,
  )
  assert bundle.selected_object is bundle.canonical_facade
  assert bundle.authoritative_object is bundle.canonical_facade
  assert bundle.selected_is_authoritative is True
  assert type(bundle.canonical_facade.canonical_config).__name__ == "PythonRuntimeConfig"


def test_no_environment_authority_switch_exists():
  source, legacy = _inputs()
  with patch.dict(
    "os.environ", {"CONFIGURATION_AUTHORITY": "canonical_rehearsal"}, clear=False,
  ):
    bundle = build_configuration_runtime_bundle(
      source_bundle=source,
      legacy_settings=legacy,
    )
  assert bundle.selected_authority is ConfigurationAuthority.LEGACY


def test_rollback_rehearsal_preserves_legacy_identity():
  source, legacy = _inputs("direct_demo_eval")
  result = rehearse_authority_rollback(
    source_bundle=source,
    legacy_settings=legacy,
    active_global_settings=active_config.settings,
  )
  assert result.original_legacy_identity_preserved
  assert result.success


def test_rollback_rehearsal_restores_legacy_values():
  source, legacy = _inputs("direct_demo_eval")
  result = rehearse_authority_rollback(
    source_bundle=source,
    legacy_settings=legacy,
    active_global_settings=active_config.settings,
  )
  assert result.canonical_parity_passed
  assert result.rollback_values_equal


def test_rollback_rehearsal_does_not_replace_global_singleton():
  source, legacy = _inputs()
  original = active_config.settings
  result = rehearse_authority_rollback(
    source_bundle=source,
    legacy_settings=legacy,
    active_global_settings=original,
  )
  assert result.global_state_untouched
  assert active_config.settings is original
