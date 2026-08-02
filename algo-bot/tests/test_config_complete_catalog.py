"""Phase 2B parity tests for the complete inactive typed catalog."""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.configuration.catalog import DERIVED_LEGACY_PROPERTIES
from app.configuration.catalog import CatalogEntry
from app.configuration.catalog import iter_catalog_entries
from app.configuration.generate import REPOSITORY_ROOT
from app.configuration.generate import render_artifacts
from app.configuration.models.actionability import (
  ActionabilityStructuralGuardConfig,
)
from app.configuration.models.actionability import (
  ActionabilityZoneReconciliationConfig,
)
from app.configuration.models.analysis import AnalysisZonesSymbolContractConfig
from app.configuration.models.bootstrap import BootstrapCtraderCredentialsConfig
from app.configuration.models.contract import ContractConfig
from app.configuration.models.execution import ExecutionEntryConfig
from app.configuration.models.execution import ExecutionPolicyConfig
from app.configuration.models.execution import ExecutionRangeConfig
from app.configuration.models.execution import ExecutionReactionConfig
from app.configuration.models.execution import ExecutionStopsConfig
from app.configuration.models.lifecycle import LifecycleRetestConfig
from app.configuration.models.market_data import MarketDataLookbacksConfig
from app.configuration.models.root import ApexVoidConfig
from app.configuration.models.risk import RiskExposureConfig
from app.configuration.models.runtime import RuntimeConfig
from tests.config_characterization import direct_conservative_fixture
from tests.config_characterization import direct_demo_eval_fixture
from tests.config_characterization import root_compose_demo_fixture
from tests.config_characterization import test_conftest_fixture as conftest_fixture


pytestmark = pytest.mark.no_database

PHASE2A_PATH = (
  Path(__file__).parents[2]
  / "docs/configuration/config-catalog-phase-2a-normalized.json"
)
SNAPSHOT_PATH = (
  Path(__file__).parent
  / "fixtures/config-phase-2a-characterization.json"
)
ENTRIES = iter_catalog_entries()
ENTRY_BY_PATH = {entry.path: entry for entry in ENTRIES}
ENTRY_BY_LEGACY = {
  entry.legacy_attr: entry
  for entry in ENTRIES
  if entry.legacy_attr is not None
}


@pytest.fixture(scope="module")
def oracle():
  return json.loads(PHASE2A_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snapshot():
  return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _normalized_default(value: Any, type_name: str) -> Any:
  if value in {"<required>", "<redacted>"}:
    return value
  if type_name == "decimal":
    return str(value)
  if type_name == "list[int]" and isinstance(value, str):
    return [int(item) for item in value.split(",")]
  if type_name == "list[string]" and isinstance(value, str):
    return value.split(",")
  return value


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
  parts = path.split(".")
  current = document
  for part in parts[:-1]:
    current = current.setdefault(part, {})
  current[parts[-1]] = value


def _get_path(model: Any, path: str) -> Any:
  current = model
  for part in path.split("."):
    current = getattr(current, part)
  return current


def _full_nested_fixture(legacy_values: dict[str, Any]) -> dict[str, Any]:
  nested: dict[str, Any] = {}
  for entry in ENTRIES:
    if entry.legacy_attr is not None:
      value = legacy_values[entry.legacy_attr]
      if entry.secret:
        value = "phase2b-test-secret"
    elif entry.secret:
      value = "phase2b-test-secret"
    elif entry.default == "<required>":
      value = 123456 if entry.type in {"int", "long"} else "required-test"
    else:
      value = entry.default
    _set_path(nested, entry.path, value)
  return nested


def _assert_legacy_round_trip(values: dict[str, Any]) -> None:
  config = ApexVoidConfig.model_validate(_full_nested_fixture(values))
  for legacy_attr, expected in values.items():
    entry = ENTRY_BY_LEGACY[legacy_attr]
    actual = _get_path(config, entry.path)
    if entry.secret:
      assert actual == "phase2b-test-secret"
    else:
      assert actual == expected, legacy_attr


def test_complete_model_contains_437_catalog_items():
  assert len(ENTRIES) == 437


def test_complete_model_contains_316_legacy_fields():
  assert len(ENTRY_BY_LEGACY) == 316


def test_complete_model_kind_counts():
  assert sum(entry.configurable for entry in ENTRIES) == 370
  assert sum(entry.protocol_constant for entry in ENTRIES) == 10
  assert sum(entry.algorithm_constant for entry in ENTRIES) == 57


def test_complete_model_shared_count():
  assert sum(entry.shared_with_ctrader for entry in ENTRIES) == 95


def test_complete_model_secret_count():
  assert sum(entry.secret for entry in ENTRIES) == 9


def test_ctrader_required_credentials_remain_required():
  credentials = BootstrapCtraderCredentialsConfig.model_fields
  assert all(
    credentials[name].is_required()
    for name in (
      "client_id",
      "client_secret",
      "access_token",
      "refresh_token",
      "account_id",
    )
  )


def test_no_representative_shell_placeholders_remain():
  assert all(entry.item_id for entry in ENTRIES)
  assert all("representative" not in entry.description.lower() for entry in ENTRIES)
  assert set(ENTRY_BY_PATH) == {entry.path for entry in ENTRIES}


def test_typed_catalog_matches_phase2a_normalized_oracle(oracle, snapshot):
  expected = {item["proposed_path"]: item for item in oracle["items"]}
  inventory = {item["name"]: item for item in snapshot["legacy_inventory"]}
  assert set(ENTRY_BY_PATH) == set(expected)
  for path, entry in ENTRY_BY_PATH.items():
    item = expected[path]
    assert entry.item_id == item["item_id"], path
    assert entry.legacy_attr == item["legacy_attr"], path
    assert entry.canonical_env == item["canonical_env"], path
    assert list(entry.deprecated_aliases) == item["deprecated_aliases"], path
    assert entry.type == item["type"], path
    assert entry.owner == item["owner"], path
    assert entry.kind == item["kind"], path
    assert entry.unit == item["unit"], path
    assert entry.reload_policy == item["reload_policy"], path
    assert entry.runtime_reload_policy == item["runtime_reload_policy"], path
    assert entry.risk_classification == item["risk_classification"], path
    assert entry.secret == item["secret"], path
    assert entry.shared_with_ctrader == item["shared_with_ctrader"], path
    assert entry.mismatch_policy == item["mismatch_policy"], path
    expected_default = (
      inventory[entry.legacy_attr]["default"]
      if entry.legacy_attr is not None
      else item["default"]
    )
    expected_default = _normalized_default(expected_default, item["type"])
    actual_default = entry.default
    if entry.secret:
      actual_default = "<redacted>"
    assert actual_default == expected_default, path


def test_every_item_id_is_unique():
  assert len({entry.item_id for entry in ENTRIES}) == len(ENTRIES)


def test_every_canonical_path_is_unique():
  assert len({entry.path for entry in ENTRIES}) == len(ENTRIES)


def test_every_legacy_attribute_maps_once():
  attrs = [entry.legacy_attr for entry in ENTRIES if entry.legacy_attr]
  assert len(attrs) == len(set(attrs)) == 316


def test_every_canonical_env_has_one_owner():
  envs = [entry.canonical_env for entry in ENTRIES if entry.canonical_env]
  assert len(envs) == len(set(envs))
  aliases = [alias for entry in ENTRIES for alias in entry.deprecated_aliases]
  assert not set(envs).intersection(aliases)
  assert len(aliases) == len(set(aliases))


def test_alias_order_matches_legacy_settings(snapshot):
  inventory = {item["name"]: item for item in snapshot["legacy_inventory"]}
  for legacy_attr, entry in ENTRY_BY_LEGACY.items():
    explicit = inventory[legacy_attr]["validation_alias_order"]
    if explicit:
      assert [entry.canonical_env, *entry.deprecated_aliases] == explicit
    else:
      assert entry.deprecated_aliases == ()


def test_requiredness_matches_legacy_settings(snapshot):
  inventory = {item["name"]: item for item in snapshot["legacy_inventory"]}
  for legacy_attr, entry in ENTRY_BY_LEGACY.items():
    assert entry.required == inventory[legacy_attr]["required"], legacy_attr


def test_python_schema_defaults_match_legacy_settings(snapshot):
  inventory = {item["name"]: item for item in snapshot["legacy_inventory"]}
  for legacy_attr, entry in ENTRY_BY_LEGACY.items():
    contexts = {
      item["context"]: item["value"] for item in entry.default_contexts
    }
    assert "python_schema" in contexts, legacy_attr
    assert contexts["python_schema"] == inventory[legacy_attr]["default"], legacy_attr


def test_ctrader_defaults_match_characterization(snapshot):
  rows = snapshot["csharp"]["ctrader_catalog_rows"]
  by_env = {entry.canonical_env: entry for entry in ENTRIES if entry.canonical_env}
  for row in rows:
    entry = by_env[row["canonical_env"]]
    contexts = {
      item["context"]: item["value"] for item in entry.default_contexts
    }
    expected = "<redacted>" if entry.secret else row["default_ctrader"]
    expected = _normalized_default(expected, entry.type)
    assert contexts["ctrader_from_environment"] == expected, entry.item_id


def test_known_default_conflicts_are_preserved():
  warnings = [entry for entry in ENTRIES if entry.mismatch_policy == "warning"]
  assert len(warnings) == 30
  counter_bias = ENTRY_BY_PATH["actionability.counter_bias.allowed"]
  assert counter_bias.default_contexts == (
    {"context": "python_schema", "value": True},
    {"context": "ctrader_from_environment", "value": False},
  )
  contract_mode = ENTRY_BY_PATH["contract.mode"]
  assert {
    item["context"]: item["value"] for item in contract_mode.default_contexts
  } == {
    "python_schema": "v7_only",
    "ctrader_from_environment": "v7_only",
    "ctrader_constructor": "legacy_v6",
  }


def test_legacy_conservative_fixture_round_trips_through_nested_model():
  _assert_legacy_round_trip(direct_conservative_fixture())


def test_legacy_demo_fixture_round_trips_through_nested_model():
  _assert_legacy_round_trip(direct_demo_eval_fixture())


def test_root_compose_demo_fixture_round_trips_through_nested_model():
  _assert_legacy_round_trip(root_compose_demo_fixture())


def test_test_environment_fixture_round_trips_through_nested_model():
  _assert_legacy_round_trip(conftest_fixture())


def test_all_non_profile_legacy_validation_is_represented():
  with pytest.raises(ValidationError):
    AnalysisZonesSymbolContractConfig(preferred_minimum_width_price=11)
  with pytest.raises(ValidationError):
    ExecutionEntryConfig(maximum_chase_distance_pips=0)
  with pytest.raises(ValidationError):
    ExecutionPolicyConfig(structural_reaction_lookback_bars=0)
  with pytest.raises(ValidationError):
    ExecutionPolicyConfig(execution_zone_max_width_atr=0)
  with pytest.raises(ValidationError):
    ExecutionRangeConfig(box_scale_out_trigger_pips=70)
  with pytest.raises(ValidationError):
    ExecutionReactionConfig(market_fraction=0.8, scale_fraction=0.3)
  with pytest.raises(ValidationError):
    ExecutionStopsConfig(be_buffer_ticks=1000)
  with pytest.raises(ValidationError):
    LifecycleRetestConfig(trigger_validity_bars=6)
  with pytest.raises(ValidationError):
    MarketDataLookbacksConfig(h1_bars=49)
  assert ActionabilityStructuralGuardConfig(
    guard_mode=" STRICT "
  ).guard_mode == "strict"
  assert ActionabilityZoneReconciliationConfig(
    enabled=False,
    mode=" ENFORCE ",
  ).mode == "off"
  assert RiskExposureConfig(
    non_hedged_opposite_policy=" CLOSE_THEN_REVERSE "
  ).non_hedged_opposite_policy == "close_then_reverse"
  assert RuntimeConfig(profile=" DEMO_EVAL ").profile == "demo_eval"
  with pytest.raises(ValidationError):
    ContractConfig(mode="legacy_v6")


def test_generated_artifacts_are_current():
  for relative_path, expected in render_artifacts().items():
    assert (REPOSITORY_ROOT / relative_path).read_bytes() == expected


def test_generated_artifacts_are_deterministic():
  assert render_artifacts() == render_artifacts()


def test_generated_artifacts_redact_secrets():
  artifacts = render_artifacts()
  combined = b"\n".join(artifacts.values())
  assert b"phase2b-test-secret" not in combined
  assert b"postgresql://apexvoid:apexvoid@localhost" not in combined
  catalog = json.loads(
    artifacts[Path("contracts/configuration/config-catalog.generated.json")]
  )
  secrets = [item for item in catalog["items"] if item["secret"]]
  assert len(secrets) == 9
  assert all(item["default"] == "<redacted>" for item in secrets)
  assert all(
    context["value"] == "<redacted>"
    for item in secrets
    for context in item["default_contexts"]
  )


def test_direct_legacy_map_contains_only_fields(snapshot):
  artifact = json.loads(
    (REPOSITORY_ROOT / "contracts/configuration/legacy-map.generated.json")
    .read_text(encoding="utf-8")
  )
  expected = {item["name"] for item in snapshot["legacy_inventory"]}
  assert artifact["count"] == len(artifact["map"]) == 316
  assert set(artifact["map"]) == expected
  assert not expected.intersection(
    item.property_name for item in DERIVED_LEGACY_PROPERTIES
  )


def test_derived_legacy_properties_are_separate():
  artifact = json.loads(
    (REPOSITORY_ROOT / "contracts/configuration/legacy-derived.generated.json")
    .read_text(encoding="utf-8")
  )
  assert artifact["count"] == 4
  assert {item["property_name"] for item in artifact["properties"]} == {
    "telegram_chat_id",
    "signal_vip_channel_id",
    "xau_vip_channel_id",
    "xau_public_channel_id",
  }


def test_shared_descriptor_contains_service_default_contexts():
  artifact = json.loads(
    (REPOSITORY_ROOT / "contracts/configuration/shared-config.generated.json")
    .read_text(encoding="utf-8")
  )
  assert artifact["count"] == 95
  assert artifact["known_conflict_count"] == 30
  evidence_ids = {item["item_id"] for item in artifact["preserved_evidence"]}
  assert {
    "python.settings.auto_trade_allow_counter_bias",
    "python.settings.auto_trade_mapped_zone_enabled",
    "python.settings.auto_trade_market_map_guard_enabled",
    "ctrader.env.CTRADER_TIMEFRAMES",
    "ctrader.env.BARS_CHANNEL",
    "python.settings.manual_trade_command_stream",
  } <= evidence_ids


def test_protocol_constants_have_no_env_bindings():
  constants = [entry for entry in ENTRIES if entry.protocol_constant]
  assert len(constants) == 10
  assert all(entry.canonical_env is None for entry in constants)
  assert all(not entry.deprecated_aliases for entry in constants)


def test_algorithm_constants_have_no_env_bindings():
  constants = [entry for entry in ENTRIES if entry.algorithm_constant]
  assert len(constants) == 57
  assert all(entry.canonical_env is None for entry in constants)
  assert all(not entry.deprecated_aliases for entry in constants)


def test_configuration_package_does_not_import_active_settings():
  package = Path(__file__).parents[1] / "app/configuration"
  for source in package.rglob("*.py"):
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
      if isinstance(node, ast.Import):
        imports.extend(item.name for item in node.names)
      elif isinstance(node, ast.ImportFrom) and node.module:
        imports.append(node.module)
    assert not any(
      module.startswith("app.core.config") for module in imports
    ), source


def test_active_config_module_imports_selectable_runtime_not_full_grouped_root():
  source = (
    Path(__file__).parents[1] / "app/core/config.py"
  ).read_text(encoding="utf-8")
  assert "app.configuration.python_loader" in source
  assert "app.configuration.models.root" not in source
  assert ".generated.json" not in source
  assert "settings = _ACTIVE_CONFIGURATION.settings" in source


def test_application_startup_uses_no_generated_json_runtime_inputs():
  source = (
    Path(__file__).parents[1] / "app/main.py"
  ).read_text(encoding="utf-8")
  assert "ApexVoidConfig" not in source
  assert "app.configuration.generate" not in source
  application = Path(__file__).parents[1] / "app"
  for path in application.rglob("*.py"):
    if "configuration" in path.parts:
      continue
    text = path.read_text(encoding="utf-8")
    assert "config-catalog.generated.json" not in text, path
    assert "shared-config.generated.json" not in text, path
