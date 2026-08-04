"""Canonical Python loader behavior (Phase 2I final)."""

from unittest.mock import patch

import pytest

from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.source_types import ConfigurationSourceBundle


pytestmark = pytest.mark.no_database

_SAFE = {
  "TELEGRAM_BOT_TOKEN": "phase-2d2-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2d2-postgres",
}


def _load(extra=None):
  return load_python_canonical_settings(ConfigurationSourceBundle(
    process_environment={**_SAFE, **(extra or {})},
  ))


def test_canonical_python_loader_does_not_require_ctrader_credentials():
  result = _load()
  assert result.success
  assert isinstance(result.config, PythonRuntimeConfig)
  assert not any(
    path.startswith("bootstrap.ctrader") for path in result.provenance.by_path()
  )


@pytest.mark.parametrize("missing", tuple(_SAFE))
def test_canonical_python_loader_requires_python_secrets(missing):
  values = {key: value for key, value in _SAFE.items() if key != missing}
  with pytest.raises(CanonicalConfigurationError, match="missing_required_input"):
    load_python_canonical_settings(
      ConfigurationSourceBundle(process_environment=values),
    )


def test_canonical_python_loader_preserves_profile_semantics():
  result = _load({"AUTO_TRADE_PROFILE": "demo_eval"})
  assert result.profile == "demo_eval"
  assert result.config.runtime.auto_trade.enabled is True
  assert result.config.runtime.auto_trade.dry_run is False


def test_canonical_python_loader_preserves_alias_conflicts():
  with pytest.raises(CanonicalConfigurationError, match="be_alias_conflict"):
    _load({
      "AUTO_TRADE_BE_BUFFER_TICKS": "3",
      "AUTO_TRADE_BE_BUFFER_PIPS": "4",
    })


def test_canonical_python_loader_preserves_provenance():
  result = _load()
  assert len(result.provenance.fields) == len(
    [entry for entry in __import__('app.configuration.catalog', fromlist=['iter_catalog_entries']).iter_catalog_entries()
     if entry.owner in {'python', 'shared'}]
  )
  token = result.provenance.by_path()["bootstrap.telegram.bot_token"]
  assert token.secret and token.explicit


def test_canonical_models_are_frozen():
  config = _load().config
  with pytest.raises(Exception):
    config.runtime.profile = "demo_eval"  # type: ignore[misc]


def test_canonical_error_is_secret_safe():
  with pytest.raises(CanonicalConfigurationError) as caught:
    load_python_canonical_settings(ConfigurationSourceBundle(process_environment={}))
  rendered = str(caught.value)
  assert _SAFE["TELEGRAM_BOT_TOKEN"] not in rendered
  assert "recovery_action=" in rendered
  assert "APEXVOID_CONFIG_AUTHORITY" not in rendered
