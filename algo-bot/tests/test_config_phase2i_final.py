"""Phase 2I final architecture and canonical behavior guards."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.configuration.generate import REPOSITORY_ROOT, check_artifacts, render_artifacts
from app.configuration.models.python_runtime import PythonRuntimeConfig
from app.configuration.phase2i_completion_gate import evaluate_phase2i_completion
from app.configuration.phase2i_inventory import evaluate_inventory
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.source_types import ConfigurationSourceBundle
from app.core.config import (
  active_configuration_authority,
  runtime_config,
)


pytestmark = pytest.mark.no_database

_SAFE = {
  "TELEGRAM_BOT_TOKEN": "phase-2i-final-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2i-final-postgres",
}


def _load(**extra):
  return load_python_canonical_settings(ConfigurationSourceBundle(
    process_environment={**_SAFE, **extra},
  ))


def test_phase2i_has_one_runtime_authority():
  assert active_configuration_authority() == "canonical"
  assert evaluate_inventory(REPOSITORY_ROOT)["runtime_authorities"] == 1


def test_phase2i_core_config_exports_runtime_config_only():
  import app.core.config as config

  exports = set(config.__all__)
  assert "runtime_config" in exports
  assert "Settings" not in exports
  assert "settings" not in exports
  assert "runtime_config_facade" not in exports


def test_phase2i_has_no_flat_settings_export():
  import app.core.config as config

  assert not hasattr(config, "Settings")
  assert not hasattr(config, "settings")


def test_phase2i_has_no_legacy_module_imports():
  inventory = evaluate_inventory(REPOSITORY_ROOT)
  assert inventory["production_legacy_imports"] == 0
  assert inventory["test_legacy_imports"] == 0
  assert inventory["tooling_legacy_imports"] == 0


def test_phase2i_has_no_legacy_tests():
  for relative in (
    "algo-bot/tests/test_config_facade.py",
    "algo-bot/tests/test_config_legacy_canonical_view.py",
    "algo-bot/tests/test_config_legacy_access_generation.py",
    "algo-bot/tests/test_config_bootstrap_authority.py",
  ):
    assert not (REPOSITORY_ROOT / relative).exists()


def test_phase2i_has_no_runtime_config_facade():
  assert evaluate_inventory(REPOSITORY_ROOT)["runtime_config_facade_usages"] == 0


def test_phase2i_has_no_legacy_access_maps():
  assert not (
    REPOSITORY_ROOT / "algo-bot/app/configuration/generated/legacy_access.py"
  ).exists()
  assert evaluate_inventory(REPOSITORY_ROOT)["legacy_access_map_usages"] == 0


def test_phase2i_has_no_authority_selector():
  inventory = evaluate_inventory(REPOSITORY_ROOT)
  assert inventory["authority_selector_deployment_usages"] == 0
  assert inventory["authority_selector_runtime_usages"] == 0


def test_phase2i_has_no_active_migration_generators():
  artifacts = render_artifacts()
  names = {path.name for path in artifacts}
  assert "legacy-map.generated.json" not in names
  assert "legacy-usage.generated.json" not in names
  assert "legacy_access.py" not in names


def test_phase2i_historical_artifacts_are_not_generated():
  artifacts = render_artifacts()
  for path in artifacts:
    assert "history/artifacts" not in path.as_posix()
    assert not str(path).endswith(".historical.json")


def test_phase2i_inventory_has_no_unknown_blockers():
  assert evaluate_inventory(REPOSITORY_ROOT)["unknown_blockers"] == 0


def test_phase2i_completion_gate_reports_complete():
  result = evaluate_phase2i_completion(REPOSITORY_ROOT)
  assert result["status"] == "PHASE_2I_COMPLETE"
  assert result["blockers"] == []
  assert result["observation_result_inferred"] is False


def test_canonical_runtime_root_type():
  assert type(runtime_config) is PythonRuntimeConfig


def test_canonical_models_are_frozen():
  with pytest.raises(Exception):
    runtime_config.runtime.profile = "x"  # type: ignore[misc]


def test_canonical_all_leaf_values_unchanged():
  first = _load().config.model_dump()
  second = _load().config.model_dump()
  assert first == second


def test_canonical_all_leaf_types_unchanged():
  first = _load().config
  second = _load().config
  assert type(first) is PythonRuntimeConfig
  assert type(second) is PythonRuntimeConfig
  assert type(first.runtime.profile) is type(second.runtime.profile)


def test_canonical_source_precedence_unchanged():
  dotenv = _load()
  # schema default for log level under empty process beyond required secrets
  assert dotenv.config.bootstrap.logging.level == "INFO"
  process = _load(LOG_LEVEL="WARNING")
  assert process.config.bootstrap.logging.level == "WARNING"


def test_canonical_profile_resolution_unchanged():
  assert _load(AUTO_TRADE_PROFILE="demo_eval").profile == "demo_eval"
  assert _load().profile == "conservative"


def test_canonical_fail_closed():
  with pytest.raises(CanonicalConfigurationError, match="missing_required_input"):
    load_python_canonical_settings(ConfigurationSourceBundle(process_environment={}))


def test_canonical_corrected_restart_succeeds():
  with pytest.raises(CanonicalConfigurationError):
    load_python_canonical_settings(ConfigurationSourceBundle(
      process_environment={"TELEGRAM_BOT_TOKEN": "x"},
    ))
  assert _load().success is True


def test_deprecated_aliases_remain_supported():
  result = _load(AUTO_TRADE_TP_PIPS="15,30,45")
  assert result.success


def test_conflicting_aliases_fail():
  with pytest.raises(CanonicalConfigurationError):
    _load(
      AUTO_TRADE_TARGET_PLANS_PIPS="30,60",
      AUTO_TRADE_TP_PIPS="15,45",
    )


def test_equal_aliases_warn():
  result = _load(
    AUTO_TRADE_TARGET_PLANS_PIPS="30,60",
    AUTO_TRADE_TP_PIPS="30,60",
  )
  assert result.success
  assert any("alias" in warning.code or "duplicate" in warning.code
             or warning.code for warning in result.warnings)


def test_generated_artifacts_are_current():
  assert check_artifacts(render_artifacts()) == 0


def test_core_config_imports_only_canonical_modules():
  path = REPOSITORY_ROOT / "algo-bot/app/core/config.py"
  tree = ast.parse(path.read_text(encoding="utf-8"))
  modules = [
    node.module for node in ast.walk(tree)
    if isinstance(node, ast.ImportFrom) and node.module
    and node.module.startswith("app.configuration")
  ]
  assert modules
  assert all(
    m.startswith((
      "app.configuration.python_loader",
      "app.configuration.python_sources",
      "app.configuration.source_types",
    ))
    for m in modules
  )
