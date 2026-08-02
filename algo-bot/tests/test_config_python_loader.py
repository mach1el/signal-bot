"""Production canonical Python loader behavior and facade parity."""

from unittest.mock import patch

import pytest

from app.configuration.facade import CanonicalSettingsFacade
from app.configuration.generated.legacy_access import (
  DERIVED_LEGACY_PROPERTIES,
  DIRECT_LEGACY_PATHS,
)
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.source_types import ConfigurationSourceBundle
from app.core.config import Settings


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


def _legacy(extra=None):
  environment = {**_SAFE, **(extra or {})}
  with patch.dict("os.environ", environment, clear=True):
    return Settings(_env_file=None)


def test_canonical_python_loader_does_not_require_ctrader_credentials():
  result = _load()
  assert result.success
  assert not any(path.startswith("bootstrap.ctrader") for path in result.provenance.by_path())


@pytest.mark.parametrize("missing", tuple(_SAFE))
def test_canonical_python_loader_requires_python_secrets(missing):
  values = {key: value for key, value in _SAFE.items() if key != missing}
  with pytest.raises(CanonicalConfigurationError, match="missing_required_input"):
    load_python_canonical_settings(ConfigurationSourceBundle(process_environment=values))


def test_canonical_python_loader_preserves_profile_semantics():
  result = _load({"AUTO_TRADE_PROFILE": "demo_eval"})
  assert result.profile == "demo_eval"
  assert result.facade.auto_trade_enabled is True
  assert result.facade.auto_trade_dry_run is False


def test_canonical_python_loader_preserves_alias_conflicts():
  with pytest.raises(CanonicalConfigurationError, match="be_alias_conflict"):
    _load({
      "AUTO_TRADE_BE_BUFFER_TICKS": "3",
      "AUTO_TRADE_BE_BUFFER_PIPS": "4",
    })


def test_canonical_python_loader_preserves_provenance():
  result = _load()
  assert len(result.provenance.fields) == 387
  token = result.provenance.by_path()["bootstrap.telegram.bot_token"]
  assert token.secret and token.explicit


def test_active_canonical_facade_direct_parity():
  facade = _load().facade
  legacy = _legacy()
  assert len(DIRECT_LEGACY_PATHS) == 316
  assert all(getattr(facade, name) == getattr(legacy, name) for name in DIRECT_LEGACY_PATHS)


def test_active_canonical_facade_derived_parity():
  facade = _load().facade
  legacy = _legacy()
  assert len(DERIVED_LEGACY_PROPERTIES) == 4
  assert all(getattr(facade, name) == getattr(legacy, name) for name in DERIVED_LEGACY_PROPERTIES)


def test_active_canonical_facade_exact_types():
  facade = _load().facade
  legacy = _legacy()
  names = (*DIRECT_LEGACY_PATHS, *DERIVED_LEGACY_PROPERTIES)
  assert all(type(getattr(facade, name)) is type(getattr(legacy, name)) for name in names)


def test_active_canonical_facade_is_immutable():
  with pytest.raises(TypeError, match="immutable"):
    _load().facade.log_level = "DEBUG"


def test_active_canonical_facade_repr_is_secret_safe():
  rendered = repr(_load().facade)
  assert isinstance(_load().facade, CanonicalSettingsFacade)
  assert _SAFE["TELEGRAM_BOT_TOKEN"] not in rendered
  assert _SAFE["POSTGRES_PASSWORD"] not in rendered
