"""Phase 2H/2I environment contract and source-policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.configuration import deployment_identity
from app.configuration.env_example_policy import render_env_example
from app.configuration.environment_aliases import (
  detect_environment_alias_conflicts,
  present_deprecated_aliases,
)
from app.configuration.environment_cli import main as environment_cli_main
from app.configuration.environment_contract import (
  environment_entry_for_name,
  environment_entry_for_path,
  iter_environment_contract_entries,
)
from app.configuration.environment_usage_audit import (
  CANONICAL_SOURCE_COLLECTION_ALLOWED,
  DEPLOYMENT_OBSERVABILITY_ALLOWED,
  DIRECT_PRODUCTION_ENV_FORBIDDEN,
  DUPLICATE_ENV_REGISTRY,
  audit_environment_usage,
)
from app.configuration.generate import check_artifacts, render_artifacts
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_policy import (
  PYTHON_SOURCE_POLICY,
  PythonConfigurationSourcePolicy,
)

pytestmark = pytest.mark.no_database

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_source_policy_defaults_match_legacy_contract():
  assert PYTHON_SOURCE_POLICY.env_file == ".env"
  assert PYTHON_SOURCE_POLICY.env_file_encoding == "utf-8"
  assert PYTHON_SOURCE_POLICY.secrets_directory is None


def test_source_bundle_uses_policy_without_settings_model_config():
  bundle = load_python_runtime_source_bundle()
  assert isinstance(bundle.process_environment, dict)
  custom = PythonConfigurationSourcePolicy(env_file="does-not-exist.env")
  assert load_python_runtime_source_bundle(custom).dotenv_values == {}


def test_config_is_canonical_only_composition_root():
  import app.core.config as config

  assert not hasattr(config, "Settings")
  assert not hasattr(config, "settings")
  assert hasattr(config, "runtime_config")
  source = (_REPO_ROOT / "algo-bot/app/core/config.py").read_text("utf-8")
  assert "BaseSettings" not in source
  assert "bootstrap_authority" not in source


def test_composition_root_exposes_diagnostics():
  import app.core.config as config

  for name in (
    "active_configuration_authority",
    "active_configuration_catalog_fingerprint",
    "active_configuration_profile",
    "active_configuration_resolution_trace",
    "active_configuration_warnings",
    "active_configuration_startup_message",
  ):
    assert hasattr(config, name)
  assert config.active_configuration_authority() == "canonical"


def test_environment_usage_audit_has_no_blockers():
  audit = audit_environment_usage(_REPO_ROOT)
  counts = audit["counts"]
  assert audit["unknown_blockers"] == 0, counts
  assert audit["direct_production_env_forbidden"] == 0, counts
  assert counts.get(DIRECT_PRODUCTION_ENV_FORBIDDEN, 0) == 0, counts
  assert counts.get(DUPLICATE_ENV_REGISTRY, 0) == 0, counts


def test_environment_options_module_is_deleted():
  assert not (_REPO_ROOT / "algo-bot/app/core/environment_options.py").exists()


def test_environment_usage_audit_classifies_boundaries():
  audit = audit_environment_usage(_REPO_ROOT)
  by_file: dict[str, set[str]] = {}
  for access in audit["accesses"]:
    by_file.setdefault(access["file"], set()).add(access["classification"])
  assert CANONICAL_SOURCE_COLLECTION_ALLOWED in by_file[
    "algo-bot/app/configuration/python_sources.py"
  ]
  assert by_file["algo-bot/app/configuration/deployment_identity.py"] == {
    DEPLOYMENT_OBSERVABILITY_ALLOWED
  }
  assert "algo-bot/app/configuration/bootstrap_authority.py" not in by_file
  assert "algo-bot/app/configuration/legacy_settings.py" not in by_file
  assert "algo-bot/app/autotrade/config_health.py" not in by_file


def test_environment_contract_entries_all_bind_env():
  entries = iter_environment_contract_entries()
  assert entries
  assert all(entry.canonical_env for entry in entries)


def test_environment_entry_resolves_canonical_and_alias():
  by_alias = environment_entry_for_name("AUTO_TRADE_TP_PIPS")
  assert by_alias is not None
  assert by_alias.canonical_env == "AUTO_TRADE_TARGET_PLANS_PIPS"
  by_path = environment_entry_for_path(by_alias.path)
  assert by_path is not None
  assert by_path.canonical_env == "AUTO_TRADE_TARGET_PLANS_PIPS"


def test_deprecated_alias_detection_and_conflicts():
  ok = {
    "AUTO_TRADE_TARGET_PLANS_PIPS": "30,60",
    "AUTO_TRADE_TP_PIPS": "30,60",
  }
  assert detect_environment_alias_conflicts(ok) == ()
  usages = present_deprecated_aliases(ok)
  assert any(u.deprecated_alias == "AUTO_TRADE_TP_PIPS" for u in usages)
  bad = {
    "AUTO_TRADE_TARGET_PLANS_PIPS": "30,60",
    "AUTO_TRADE_TP_PIPS": "15,45",
  }
  conflicts = detect_environment_alias_conflicts(bad)
  assert conflicts


def test_deployment_identity_defaults(monkeypatch):
  monkeypatch.delenv("GIT_SHA", raising=False)
  assert deployment_identity.git_sha() == "unknown"


def test_env_example_is_secret_safe_and_points_to_reference():
  text = render_env_example()
  assert "APEXVOID_CONFIG_AUTHORITY" not in text
  assert "<required-secret>" in text
  assert "POSTGRES_PASSWORD=" in text
  assert "environment-reference.generated.md" in text


def test_generated_configuration_artifacts_current():
  assert check_artifacts(render_artifacts()) == 0


def test_environment_cli_check_and_reports():
  assert environment_cli_main(["--check"]) == 0
  assert environment_cli_main(["--report-deprecated"]) == 0
  assert environment_cli_main(["--report-unknown"]) == 0
