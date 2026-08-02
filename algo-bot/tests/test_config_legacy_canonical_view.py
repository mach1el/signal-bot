"""Read-only legacy-backed canonical configuration view tests."""

from types import SimpleNamespace

import pytest

from app.configuration.legacy_canonical_view import LegacyCanonicalConfigView


pytestmark = pytest.mark.no_database


def _view() -> LegacyCanonicalConfigView:
  legacy = SimpleNamespace(
    log_level="TRACE",
    log_retention_days=9,
    log_file_enabled=False,
    telegram_owner_id=431,
    calendar_currencies="USD,EUR",
    telegram_bot_token="secret-view-probe",
  )
  return LegacyCanonicalConfigView(legacy)


def test_legacy_canonical_view_returns_exact_values():
  view = _view()
  assert view.bootstrap.logging.level == "TRACE"
  assert view.delivery.telegram.telegram_owner_id == 431
  assert view.market_data.calendar.currencies == "USD,EUR"


def test_legacy_canonical_view_preserves_exact_types():
  view = _view()
  assert type(view.bootstrap.logging.retention_days) is int
  assert type(view.bootstrap.logging.file_enabled) is bool
  assert type(view.delivery.telegram.telegram_owner_id) is int


def test_legacy_canonical_view_is_immutable():
  view = _view()
  with pytest.raises(TypeError):
    view.delivery.telegram.telegram_owner_id = 123
  with pytest.raises(TypeError):
    del view.market_data.calendar.enabled
  assert not hasattr(view, "__dict__")


def test_legacy_canonical_view_unknown_path_raises():
  view = _view()
  with pytest.raises(AttributeError):
    _ = view.delivery.telegram.not_a_real_field
  with pytest.raises(AttributeError):
    _ = view.bootstrap.build.service_version


def test_legacy_canonical_view_does_not_run_resolver(monkeypatch):
  from app.configuration import python_loader

  def forbidden(*args, **kwargs):
    raise AssertionError("canonical resolver must not run")

  monkeypatch.setattr(python_loader, "load_python_canonical_settings", forbidden)
  assert _view().bootstrap.logging.level == "TRACE"


def test_legacy_canonical_view_repr_is_secret_safe():
  view = _view()
  rendered = f"{view!r} {view.delivery.telegram!r}"
  assert "secret-view-probe" not in rendered
  assert "telegram_bot_token" not in rendered
