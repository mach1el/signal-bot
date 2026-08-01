"""Structural tests for the inactive grouped Pydantic model shells."""

from dataclasses import FrozenInstanceError

import pytest
from pydantic import BaseModel, ValidationError

from app.configuration.metadata import ConfigMetadata
from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification
from app.configuration.models.bootstrap import BootstrapConfig
from app.configuration.models.bootstrap import TelegramBootstrapConfig
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


def test_metadata_is_derived_by_recursive_model_traversal():
  entries = dict(iter_config_metadata(ApexVoidConfig))
  assert entries["runtime.profile"]["canonical_env"] == "AUTO_TRADE_PROFILE"
  assert entries["contract.versions.trade_plan"] == {
    "legacy_attr": None,
    "canonical_env": None,
    "deprecated_aliases": [],
    "owner": "shared",
    "reload_policy": "code_release",
    "unit": "version",
    "risk_classification": "cross_service_contract",
    "kind": "protocol_constant",
    "configurable": False,
    "protocol_constant": True,
    "algorithm_constant": False,
    "secret": False,
    "shared_with_ctrader": True,
    "mismatch_policy": "fatal",
    "description": "TradePlan protocol version implemented by both services.",
    "catalog_version": 1,
    "introduced_in": "config-catalog-v1",
    "deprecated": False,
    "replacement_path": None,
    "terminal_deprecation_reason": None,
  }
  assert len(entries) >= 20


def test_secret_shell_metadata_never_contains_a_value():
  field = TelegramBootstrapConfig.model_fields["bot_token"]
  metadata = field.json_schema_extra["apexvoid_config"]
  assert field.is_required()
  assert metadata["secret"] is True
  assert "default" not in metadata


def test_config_metadata_object_is_frozen():
  metadata = ConfigMetadata(
    legacy_attr="example",
    canonical_env="EXAMPLE",
    deprecated_aliases=(),
    owner=ConfigOwner.PYTHON,
    reload_policy=ReloadPolicy.RESTART,
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
