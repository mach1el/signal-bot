"""Semantic invariants for Catalog V2 and immutable profiles."""

from dataclasses import replace

import pytest

from app.configuration import resolver
from app.configuration.catalog import iter_catalog_entries
from app.configuration.catalog_validation import catalog_semantic_errors
from app.configuration.catalog_validation import profile_assignment_errors
from app.configuration.catalog_validation import validate_active_catalog
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.profiles import ConfigProfile
from app.configuration.profiles import ProfileAssignment
from app.configuration.source_types import SourceKind


pytestmark = pytest.mark.no_database


def _resolved_with_profile(monkeypatch, profile: ConfigProfile):
  monkeypatch.setattr(resolver, "get_profile", lambda _name: profile)
  return resolver.resolve_configuration(
    init_values={},
    process_environment={},
    dotenv_values={},
    file_secret_values={},
    model=PythonRuntimeConfig,
  )


def test_active_catalog_and_profiles_satisfy_semantic_invariants():
  validate_active_catalog()
  assert profile_assignment_errors() == []


def test_catalog_semantics_require_canonical_env_for_configurable_fields():
  entry = next(item for item in iter_catalog_entries() if item.configurable)
  errors = catalog_semantic_errors((replace(entry, canonical_env=None),))
  assert errors == [f"{entry.path}: configurable field has no canonical ENV"]


def test_catalog_semantics_require_shared_owner_and_flag_to_agree():
  entry = next(item for item in iter_catalog_entries() if item.owner == "shared")
  errors = catalog_semantic_errors((replace(entry, shared_with_ctrader=False),))
  assert any("owner/shared_with_ctrader mismatch" in error for error in errors)


def test_catalog_semantics_reject_mismatch_policy_on_non_shared_field():
  entry = next(item for item in iter_catalog_entries() if item.owner == "python")
  errors = catalog_semantic_errors((replace(entry, mismatch_policy="warning"),))
  assert any("non-shared field has mismatch policy" in error for error in errors)


def test_catalog_semantics_reject_default_outside_allowed_values():
  entry = next(item for item in iter_catalog_entries() if item.allowed_values)
  errors = catalog_semantic_errors((replace(entry, default="not-allowed"),))
  assert any("outside allowed values" in error for error in errors)


def test_unknown_profile_path_fails_closed(monkeypatch):
  profile = ConfigProfile(
    name="broken",
    assignments=(ProfileAssignment("does.not.exist", True),),
  )
  resolved = _resolved_with_profile(monkeypatch, profile)
  assert [conflict.code for conflict in resolved.conflicts] == [
    "unknown_profile_path"
  ]


def test_profile_cannot_override_algorithm_constant(monkeypatch):
  profile = ConfigProfile(
    name="broken",
    assignments=(
      ProfileAssignment("analysis.detectors.scoring.coil", 2.0),
    ),
  )
  resolved = _resolved_with_profile(monkeypatch, profile)
  assert [conflict.code for conflict in resolved.conflicts] == [
    "profile_constant_override"
  ]


def test_invalid_profile_value_fails_before_model_construction(monkeypatch):
  path = "execution.entry.maximum_chase_distance_pips"
  profile = ConfigProfile(
    name="broken",
    assignments=(ProfileAssignment(path, "not-a-number"),),
  )
  resolved = _resolved_with_profile(monkeypatch, profile)
  assert [conflict.code for conflict in resolved.conflicts] == [
    "profile_value_invalid"
  ]
  assert resolved.trace.by_path()[path].source_kind is SourceKind.SCHEMA_DEFAULT


def test_profile_values_use_canonical_typed_parser(monkeypatch):
  path = "actionability.structural_guard.guard_mode"
  profile = ConfigProfile(
    name="typed",
    assignments=(ProfileAssignment(path, " STRICT "),),
  )
  resolved = _resolved_with_profile(monkeypatch, profile)
  assert resolved.conflicts == ()
  assert resolved.flat_values[path] == "strict"
  assert resolved.trace.by_path()[path].source_name == "typed"
