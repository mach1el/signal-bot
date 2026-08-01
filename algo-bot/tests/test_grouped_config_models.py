"""Structural tests for the inactive grouped Pydantic model shells."""

import json
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

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
from app.configuration.models.bootstrap import BootstrapConfig
from app.configuration.models.bootstrap import BootstrapTelegramConfig
from app.configuration.models.root import ApexVoidConfig
from app.configuration.models.runtime import RuntimeConfig
from app.configuration.traversal import iter_config_metadata


pytestmark = pytest.mark.no_database

_PHASE2A_CATALOG = (
  Path(__file__).parents[2]
  / "docs/configuration/config-catalog-phase-2a-normalized.json"
)


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


def _expected_type(type_name):
  return {
    "bool": bool,
    "decimal": Decimal,
    "float": float,
    "int": int,
    "long": int,
    "str": str,
    "string": str,
    "Optional[int]": int | None,
    "Optional[str]": str | None,
    "list[int]": list[int],
    "list[string]": list[str],
  }[type_name]


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
  )


def test_representative_declarations_match_phase2a_oracle():
  catalog = json.loads(_PHASE2A_CATALOG.read_text(encoding="utf-8"))
  oracle = {item["proposed_path"]: item for item in catalog["items"]}
  for path, field, metadata in _leaf_fields():
    item = oracle[path]
    assert field.annotation == _expected_type(item["type"]), path
    for metadata_key, item_key in (
      ("legacy_attr", "legacy_attr"),
      ("canonical_env", "canonical_env"),
      ("owner", "owner"),
      ("reload_policy", "reload_policy"),
      ("unit", "unit"),
      ("risk_classification", "risk_classification"),
      ("kind", "kind"),
      ("secret", "secret"),
      ("shared_with_ctrader", "shared_with_ctrader"),
      ("mismatch_policy", "mismatch_policy"),
    ):
      assert metadata[metadata_key] == item[item_key], path
    assert metadata["deprecated_aliases"] == item["deprecated_aliases"], path
    if item["default"] not in {"<redacted>", "<required>"}:
      expected_default = item["default"]
      if item["type"] == "decimal":
        expected_default = Decimal(str(expected_default))
      elif item["type"] == "list[int]":
        expected_default = [
          int(value) for value in str(expected_default).split(",")
        ]
      elif item["type"] == "list[string]":
        expected_default = str(expected_default).split(",")
      assert field.default == expected_default, path


def test_metadata_is_derived_by_recursive_model_traversal():
  entries = dict(iter_config_metadata(ApexVoidConfig))
  assert entries["runtime.profile"]["canonical_env"] == "AUTO_TRADE_PROFILE"
  assert entries["contract.versions.trade_plan"] == {
    "item_id": "hardcoded.contract.trade_plan_version",
    "legacy_attr": None,
    "canonical_env": None,
    "deprecated_aliases": [],
    "owner": "shared",
    "reload_policy": "code_release",
    "runtime_reload_policy": "code_release",
    "unit": "version",
    "risk_classification": "cross_service_contract",
    "kind": "protocol_constant",
    "configurable": False,
    "protocol_constant": True,
    "algorithm_constant": False,
    "secret": False,
    "shared_with_ctrader": True,
    "mismatch_policy": "fatal",
    "description": (
      "Config-like hardcoded value proposed at "
      "contract.versions.trade_plan."
    ),
    "default_contexts": [],
    "allowed_values": [],
    "validation_summary": "none; source constant",
    "evidence_notes": [],
    "catalog_version": 1,
    "introduced_in": "config-catalog-v1",
    "deprecated": False,
    "replacement_path": None,
    "terminal_deprecation_reason": None,
  }
  assert len(entries) >= 20


def test_secret_shell_metadata_never_contains_a_value():
  field = BootstrapTelegramConfig.model_fields["bot_token"]
  metadata = field.json_schema_extra["apexvoid_config"]
  assert field.is_required()
  assert metadata["secret"] is True
  assert "default" not in metadata


def test_config_metadata_object_is_frozen():
  metadata = ConfigMetadata(
    item_id="test.example",
    legacy_attr="example",
    canonical_env="EXAMPLE",
    deprecated_aliases=(),
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
      item_id="test.constrained_count",
      legacy_attr="constrained_count",
      env="CONSTRAINED_COUNT",
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
      item_id="test.secret",
      legacy_attr="secret",
      canonical_env="SECRET",
      deprecated_aliases=(),
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
