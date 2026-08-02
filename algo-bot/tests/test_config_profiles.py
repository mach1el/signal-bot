"""Immutable profile-document contract tests."""

from dataclasses import FrozenInstanceError
from typing import Annotated

import pytest
from pydantic import TypeAdapter

from app.configuration.catalog import iter_catalog_entries
from app.configuration.models.root import ApexVoidConfig
from app.configuration.profiles import CONSERVATIVE_PROFILE
from app.configuration.profiles import DEMO_EVAL_PROFILE
from app.configuration.profiles import PROFILES


pytestmark = pytest.mark.no_database


def _field_for_path(path: str):
  model = ApexVoidConfig
  field = None
  for part in path.split("."):
    field = model.model_fields[part]
    annotation = field.annotation
    if isinstance(annotation, type) and hasattr(annotation, "model_fields"):
      model = annotation
  assert field is not None
  return field


def test_conservative_profile_is_immutable():
  with pytest.raises(FrozenInstanceError):
    CONSERVATIVE_PROFILE.name = "changed"
  with pytest.raises(TypeError):
    PROFILES["new"] = CONSERVATIVE_PROFILE


def test_demo_eval_profile_is_immutable():
  with pytest.raises(FrozenInstanceError):
    DEMO_EVAL_PROFILE.assignments[0].value = False


def test_demo_eval_profile_contains_48_assignments():
  assert len(DEMO_EVAL_PROFILE.assignments) == 48
  assert tuple(item.path for item in DEMO_EVAL_PROFILE.assignments) == tuple(
    sorted(item.path for item in DEMO_EVAL_PROFILE.assignments)
  )


def test_profile_paths_exist_in_typed_catalog():
  paths = {entry.path for entry in iter_catalog_entries()}
  for profile in PROFILES.values():
    assert {item.path for item in profile.assignments} <= paths


def test_profile_values_validate_against_target_fields():
  for profile in PROFILES.values():
    for assignment in profile.assignments:
      field = _field_for_path(assignment.path)
      annotation = (
        Annotated[field.annotation, *field.metadata]
        if field.metadata else field.annotation
      )
      adapter = TypeAdapter(annotation)
      assert adapter.validate_python(assignment.value) == assignment.value


def test_profiles_contain_no_secrets():
  entries = {entry.path: entry for entry in iter_catalog_entries()}
  for profile in PROFILES.values():
    assert not any(entries[item.path].secret for item in profile.assignments)
