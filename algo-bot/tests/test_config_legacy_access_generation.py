"""Generated Python legacy-access contract tests."""

from pathlib import Path

import pytest

from app.configuration.catalog import iter_catalog_entries
from app.configuration.generate import REPOSITORY_ROOT
from app.configuration.generate import render_artifacts
from app.configuration.generated.legacy_access import DERIVED_LEGACY_PROPERTIES
from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS
from app.configuration.generated.legacy_access import SECRET_LEGACY_FIELDS


pytestmark = pytest.mark.no_database

_ACCESS_PATH = Path("algo-bot/app/configuration/generated/legacy_access.py")


def test_generated_legacy_access_contains_316_direct_fields():
  assert len(DIRECT_LEGACY_PATHS) == 316
  assert set(DIRECT_LEGACY_PATHS) == {
    entry.legacy_attr
    for entry in iter_catalog_entries()
    if entry.legacy_attr is not None
  }


def test_generated_legacy_access_contains_four_derived_properties():
  assert len(DERIVED_LEGACY_PROPERTIES) == 4
  assert set(DERIVED_LEGACY_PROPERTIES) == {
    "signal_vip_channel_id",
    "telegram_chat_id",
    "xau_public_channel_id",
    "xau_vip_channel_id",
  }


def test_generated_legacy_access_is_current():
  assert (REPOSITORY_ROOT / _ACCESS_PATH).read_bytes() == render_artifacts()[_ACCESS_PATH]


def test_generated_legacy_access_contains_no_secret_values():
  content = render_artifacts()[_ACCESS_PATH].decode("utf-8")
  assert SECRET_LEGACY_FIELDS
  for entry in iter_catalog_entries():
    if entry.secret and entry.default not in {"<redacted>", "<required>"}:
      assert str(entry.default) not in content
  assert "os.environ" not in content
  assert "json.load" not in content
  assert "eval(" not in content


def test_generated_legacy_mappings_are_immutable():
  with pytest.raises(TypeError):
    DIRECT_LEGACY_PATHS["log_level"] = ("other",)  # type: ignore[index]
