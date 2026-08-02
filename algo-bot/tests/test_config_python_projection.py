"""Mechanical Python-owned/shared canonical projection tests."""

from pydantic import ValidationError
import pytest

from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.models.root import ApexVoidConfig


pytestmark = pytest.mark.no_database


def _by_path(model):
  return {entry.path: entry for entry in iter_catalog_entries(model)}


def test_python_projection_excludes_all_ctrader_only_fields():
  projected = _by_path(PythonRuntimeConfig)
  ctrader = {e.path for e in iter_catalog_entries() if e.owner == "ctrader"}
  assert len(ctrader) == 50
  assert not ctrader & projected.keys()


def test_python_projection_includes_all_python_fields():
  full = {e.path for e in iter_catalog_entries() if e.owner == "python"}
  assert len(full) == 292
  assert full <= _by_path(PythonRuntimeConfig).keys()


def test_python_projection_includes_all_shared_fields():
  full = {e.path for e in iter_catalog_entries() if e.owner == "shared"}
  assert len(full) == 95
  assert full <= _by_path(PythonRuntimeConfig).keys()


def test_python_projection_contains_316_legacy_fields():
  assert sum(e.legacy_attr is not None for e in iter_catalog_entries(PythonRuntimeConfig)) == 316


def test_python_projection_preserves_catalog_paths():
  full = _by_path(ApexVoidConfig)
  projected = _by_path(PythonRuntimeConfig)
  assert len(projected) == 387
  assert all(projected[path].as_dict() == full[path].as_dict() for path in projected)


def test_python_projection_preserves_constraints():
  full = _by_path(ApexVoidConfig)
  for path, entry in _by_path(PythonRuntimeConfig).items():
    assert entry.constraints == full[path].constraints
    assert entry.allowed_values == full[path].allowed_values
    assert entry.validation_summary == full[path].validation_summary


def test_python_projection_is_frozen():
  config = PythonRuntimeConfig.model_construct()
  with pytest.raises(ValidationError, match="frozen"):
    config.runtime = None


def test_python_projection_forbids_extra_fields():
  with pytest.raises(ValidationError, match="extra_forbidden"):
    PythonRuntimeConfig.model_validate({"not_catalogued": True})
