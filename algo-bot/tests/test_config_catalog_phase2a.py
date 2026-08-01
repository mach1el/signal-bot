"""Integrity tests for the normalized Phase 2A configuration catalog."""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from app.configuration.catalog_validation import CatalogValidationError
from app.configuration.catalog_validation import validate_catalog
from app.configuration.metadata import ConfigKind
from app.configuration.metadata import ConfigMetadata
from app.configuration.metadata import ConfigOwner
from app.configuration.metadata import ConfigUnit
from app.configuration.metadata import MismatchPolicy
from app.configuration.metadata import ReloadPolicy
from app.configuration.metadata import RiskClassification


pytestmark = pytest.mark.no_database
CATALOG_PATH = (
  Path(__file__).resolve().parents[2]
  / "docs/configuration/config-catalog-phase-2a-normalized.json"
)


@pytest.fixture(scope="module")
def catalog():
  return json.loads(CATALOG_PATH.read_text())


def test_normalized_catalog_has_unique_item_ids(catalog):
  values = [item["item_id"] for item in catalog["items"]]
  assert len(values) == len(set(values)) == 437


def test_normalized_catalog_has_unique_legacy_attributes(catalog):
  values = [
    item["legacy_attr"] for item in catalog["items"]
    if item["legacy_attr"] is not None
  ]
  assert len(values) == len(set(values)) == 316


def test_normalized_catalog_has_unique_canonical_env_owners(catalog):
  values = [
    item["canonical_env"] for item in catalog["items"]
    if item["canonical_env"] is not None
  ]
  assert len(values) == len(set(values))


def test_normalized_catalog_has_no_alias_collisions(catalog):
  canonical = {
    item["canonical_env"] for item in catalog["items"]
    if item["canonical_env"] is not None
  }
  aliases = [
    alias
    for item in catalog["items"]
    for alias in item["deprecated_aliases"]
  ]
  assert len(aliases) == len(set(aliases))
  assert canonical.isdisjoint(aliases)


def test_normalized_catalog_has_unique_paths(catalog):
  paths = [item["proposed_path"] for item in catalog["items"]]
  assert len(paths) == len(set(paths)) == 437


def test_numeric_trading_fields_have_semantic_units(catalog):
  trading = {"analysis", "strategies", "actionability", "execution", "risk"}
  invalid = [
    item["item_id"]
    for item in catalog["items"]
    if any(token in item["type"].lower() for token in ("int", "float", "decimal"))
    and item["proposed_path"].split(".")[0] in trading
    and item["unit"] == "string"
  ]
  assert invalid == []


def test_suffixes_match_unit_metadata(catalog):
  validate_catalog(catalog)


def test_protocol_constants_are_not_configurable(catalog):
  constants = [
    item for item in catalog["items"] if item["protocol_constant"]
  ]
  assert len(constants) == 10
  assert all(not item["configurable"] for item in constants)


def test_algorithm_constants_are_not_configurable(catalog):
  constants = [
    item for item in catalog["items"] if item["algorithm_constant"]
  ]
  assert len(constants) == 57
  assert all(not item["configurable"] for item in constants)


def test_constants_have_no_env_binding(catalog):
  constants = [
    item for item in catalog["items"]
    if item["protocol_constant"] or item["algorithm_constant"]
  ]
  assert all(item["canonical_env"] is None for item in constants)
  assert all(item["deprecated_aliases"] == [] for item in constants)
  assert all(item["reload_policy"] == "code_release" for item in constants)


def test_secret_fields_are_classified(catalog):
  secrets = [item for item in catalog["items"] if item["secret"]]
  assert len(secrets) == 9
  assert {item["canonical_env"] for item in secrets} == {
    "ANTHROPIC_API_KEY",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_REFRESH_TOKEN",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "SCANNER_TELEGRAM_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TIINGO_API_KEY",
  }


def test_secret_defaults_are_redacted(catalog):
  assert all(
    item["default"] == "<redacted>"
    for item in catalog["items"] if item["secret"]
  )


def test_shared_fields_have_mismatch_policy(catalog):
  allowed = {policy.value for policy in MismatchPolicy}
  assert all(
    item["mismatch_policy"] in allowed
    for item in catalog["items"] if item["shared_with_ctrader"]
  )


def test_deprecated_paths_require_replacement_or_reason(catalog):
  assert all(
    not item["deprecated"]
    or item["replacement_path"]
    or item["terminal_deprecation_reason"]
    for item in catalog["items"]
  )


def test_runtime_controls_are_not_under_contract(catalog):
  controls = {
    "auto_trade_profile",
    "auto_trade_enabled",
    "auto_trade_dry_run",
    "scanner_enabled",
  }
  selected = {
    item["legacy_attr"]: item["proposed_path"]
    for item in catalog["items"] if item["legacy_attr"] in controls
  }
  assert selected == {
    "auto_trade_profile": "runtime.profile",
    "auto_trade_enabled": "runtime.auto_trade.enabled",
    "auto_trade_dry_run": "runtime.auto_trade.dry_run",
    "scanner_enabled": "runtime.scanner.enabled",
  }


def test_ctrader_credentials_are_not_under_analysis(catalog):
  sensitive_prefixes = (
    "bootstrap.ctrader.connection.",
    "bootstrap.ctrader.credentials.",
    "bootstrap.ctrader.token_rotation.",
  )
  selected = [
    item for item in catalog["items"]
    if (item["canonical_env"] or "").startswith("CTRADER_")
    and any(token in (item["canonical_env"] or "") for token in (
      "HOST", "PORT", "REQUEST_TIMEOUT", "CLIENT_ID", "CLIENT_SECRET",
      "ACCESS_TOKEN", "REFRESH_TOKEN", "ACCOUNT_ID",
    ))
  ]
  assert selected
  assert all(
    item["proposed_path"].startswith(sensitive_prefixes)
    for item in selected
  )


def test_catalog_validator_rejects_path_reuse(catalog):
  duplicate = json.loads(json.dumps(catalog))
  duplicate["items"][1]["proposed_path"] = duplicate["items"][0]["proposed_path"]
  with pytest.raises(CatalogValidationError, match="duplicate proposed_path"):
    validate_catalog(duplicate)


def test_metadata_serialization_is_deterministic():
  metadata = ConfigMetadata(
    item_id="python.settings.auto_trade_enabled",
    legacy_attr="auto_trade_enabled",
    canonical_env="AUTO_TRADE_ENABLED",
    deprecated_aliases=(),
    owner=ConfigOwner.SHARED,
    reload_policy=ReloadPolicy.RESTART,
    runtime_reload_policy=ReloadPolicy.RESTART,
    unit=ConfigUnit.BOOLEAN,
    risk_classification=RiskClassification.CROSS_SERVICE_CONTRACT,
    kind=ConfigKind.CONFIGURABLE,
    configurable=True,
    protocol_constant=False,
    algorithm_constant=False,
    secret=False,
    shared_with_ctrader=True,
    mismatch_policy=MismatchPolicy.FATAL,
    description="Autonomous execution master switch.",
  )
  encoded = json.dumps(metadata.as_dict(), sort_keys=True, separators=(",", ":"))
  assert encoded == json.dumps(metadata.as_dict(), sort_keys=True, separators=(",", ":"))
  with pytest.raises(ValueError, match="kind flags"):
    replace(metadata, configurable=False)
