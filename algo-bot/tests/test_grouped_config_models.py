"""Structural tests for Catalog V2 grouped Pydantic models."""

from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel, ValidationError

from app.configuration.catalog import iter_catalog_entries
from app.configuration.metadata import ConfigMetadata
from app.configuration.metadata import ContextDefault
from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import DefaultContext
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.metadata import config_field
from app.configuration.models.base import FrozenConfigModel
from app.configuration.models.bootstrap import BootstrapTelegramConfig
from app.configuration.models.root import ApexVoidConfig
from app.configuration.models.runtime import RuntimeConfig
from app.configuration.traversal import iter_config_metadata


pytestmark = pytest.mark.no_database


def _all_model_types():
  seen = set()
  pending = [ApexVoidConfig]
  while pending:
    model = pending.pop()
    if model in seen:
      continue
    seen.add(model)
    for field in model.model_fields.values():
      annotation = field.annotation
      if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        pending.append(annotation)
  return seen


def _leaf_fields(model=ApexVoidConfig, prefix=()):
  for name, field in model.model_fields.items():
    path = ".".join((*prefix, name))
    metadata = (field.json_schema_extra or {}).get("apexvoid_config")
    if metadata is not None:
      yield path, field, metadata
    annotation = field.annotation
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
      yield from _leaf_fields(annotation, (*prefix, name))


def test_grouped_model_shells_are_frozen():
  for model in _all_model_types():
    assert model.model_config.get("frozen") is True
  runtime = RuntimeConfig()
  with pytest.raises(ValidationError, match="frozen_instance"):
    runtime.profile = "demo_eval"


def test_grouped_model_shells_forbid_extra_fields():
  for model in _all_model_types():
    assert model.model_config.get("extra") == "forbid"
  with pytest.raises(ValidationError, match="extra_forbidden"):
    RuntimeConfig(unknown_switch=True)


def test_root_contains_the_normalized_domains():
  assert tuple(ApexVoidConfig.model_fields) == (
    "bootstrap",
    "runtime",
    "market_data",
    "analysis",
    "strategies",
    "actionability",
    "contract",
    "execution",
    "risk",
    "lifecycle",
    "delivery",
    "manual_algo",
    "instruments",
  )


def test_live_catalog_matches_model_leaves():
  catalog_paths = {entry.path for entry in iter_catalog_entries()}
  model_paths = {path for path, _field, _metadata in _leaf_fields()}
  assert catalog_paths == model_paths
  assert len(catalog_paths) == 441


def test_metadata_is_derived_by_recursive_model_traversal():
  entries = dict(iter_config_metadata(ApexVoidConfig))
  assert entries["runtime.profile"]["canonical_env"] == "AUTO_TRADE_PROFILE"
  profile = entries["runtime.profile"]
  assert "item_id" not in profile
  assert "legacy_attr" not in profile
  assert profile["catalog_version"] == 2
  assert len(entries) == 441


def test_secret_shell_metadata_never_contains_a_value():
  field = BootstrapTelegramConfig.model_fields["bot_token"]
  metadata = field.json_schema_extra["apexvoid_config"]
  assert field.is_required()
  assert metadata["secret"] is True
  assert "default" not in metadata


def test_config_metadata_object_is_frozen():
  metadata = ConfigMetadata(
    canonical_env="EXAMPLE",
    deprecated_env_aliases=(),
    owner=ConfigOwner.PYTHON,
    reload_policy=ReloadPolicy.RESTART,
    runtime_reload_policy=ReloadPolicy.RESTART,
    unit=ConfigUnit.STRING,
    risk_classification=RiskClassification.INFRASTRUCTURE,
    kind=ConfigKind.CONFIGURABLE,
    configurable=True,
    protocol_constant=False,
    algorithm_constant=False,
    secret=False,
    shared_with_ctrader=False,
    mismatch_policy=MismatchPolicy.NOT_REPORTED,
    description="Test-only metadata.",
  )
  with pytest.raises(FrozenInstanceError):
    metadata.description = "changed"


def test_metadata_supports_context_defaults_and_real_constraints():
  class ConstrainedConfig(FrozenConfigModel):
    count: int = config_field(
      3,
      canonical_env="CONSTRAINED_COUNT",
      owner=ConfigOwner.SHARED,
      reload=ReloadPolicy.NEW_SETUP_ONLY,
      runtime_reload=ReloadPolicy.RESTART,
      unit=ConfigUnit.COUNT,
      risk=RiskClassification.EXECUTION_SAFETY,
      shared_with_ctrader=True,
      mismatch_policy=MismatchPolicy.WARNING,
      description="Test-only constrained value.",
      default_contexts=(
        ContextDefault(DefaultContext.PYTHON_SCHEMA, 3),
        ContextDefault(DefaultContext.CTRADER_FROM_ENVIRONMENT, 4),
      ),
      ge=1,
      le=5,
    )

  field = ConstrainedConfig.model_fields["count"]
  metadata = field.json_schema_extra["apexvoid_config"]
  assert metadata["runtime_reload_policy"] == "restart"
  assert metadata["default_contexts"] == [
    {"context": "python_schema", "value": 3},
    {"context": "ctrader_from_environment", "value": 4},
  ]
  with pytest.raises(ValidationError, match="greater_than_equal"):
    ConstrainedConfig(count=0)


def test_secret_context_defaults_must_be_redacted():
  with pytest.raises(ValueError, match="must be redacted"):
    ConfigMetadata(
      canonical_env="SECRET",
      deprecated_env_aliases=(),
      owner=ConfigOwner.PYTHON,
      reload_policy=ReloadPolicy.RESTART,
      runtime_reload_policy=ReloadPolicy.RESTART,
      unit=ConfigUnit.STRING,
      risk_classification=RiskClassification.INFRASTRUCTURE,
      kind=ConfigKind.CONFIGURABLE,
      configurable=True,
      protocol_constant=False,
      algorithm_constant=False,
      secret=True,
      shared_with_ctrader=False,
      mismatch_policy=MismatchPolicy.NOT_REPORTED,
      description="Test secret.",
      default_contexts=(
        ContextDefault(DefaultContext.PYTHON_SCHEMA, "leaked"),
      ),
    )


def test_no_deprecated_legacy_risk_classification():
  assert not hasattr(RiskClassification, "DEPRECATED_LEGACY")
  assert RiskClassification.DEPRECATED_CONFIGURATION.value == (
    "deprecated_configuration"
  )
