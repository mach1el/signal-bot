"""Phase 2I-A canonical-cutover static surface tests.

Covers the compatibility-surface audit, the removal-gate static + observation
checks, the deployment-default cutover, and the absence of production facade
calls / dynamic flat lookups / compatibility-class imports. These are all
in-process static checks (no live services), marked ``no_database``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.configuration.compatibility_surface_audit import (
  CLASSIFICATIONS,
  audit_compatibility_surface,
)
from app.configuration.generate import check_artifacts, render_artifacts
from app.configuration.phase2h_gate import (
  _production_settings_imports,
  evaluate_phase2h_readiness,
)
from app.configuration.phase2i_removal_gate import (
  evaluate_observation_evidence,
  evaluate_static_readiness,
)
from app.configuration.usage_audit import audit_legacy_settings_usage

pytestmark = pytest.mark.no_database

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPAT_ARTIFACT = (
  _REPO_ROOT
  / "contracts/configuration/compatibility-surface-phase-2i-a.generated.json"
)


# --- baseline (Phase 2H still green) ----------------------------------------

def test_phase2h_baseline_still_ready():
  result = evaluate_phase2h_readiness()
  assert result["status"] == "READY_FOR_PHASE_2H", result["blockers"]
  assert result["production_flat_reads"] == 0
  assert result["production_settings_imports"] == 0
  assert result["unknown_blockers"] == 0


# --- compatibility surface --------------------------------------------------

def test_compatibility_surface_has_no_unknown_blockers():
  audit = audit_compatibility_surface(_REPO_ROOT)
  counts = audit["counts"]
  assert counts["unknown_blockers"] == 0, audit["unknown_blockers"]
  assert counts["production_facade_calls"] == 0, counts
  assert set(counts["by_classification"]) == set(CLASSIFICATIONS)


def test_compatibility_surface_retains_rollback_and_tooling():
  audit = audit_compatibility_surface(_REPO_ROOT)
  by_classification = audit["counts"]["by_classification"]
  # The facade definition is retained for tooling/tests until 2I-B.
  assert by_classification["REMOVE_2I_B"] >= 1
  # Legacy rollback types + settings remain wired at the composition root.
  assert by_classification["LEGACY_ROLLBACK_RETAIN_2I_A"] >= 1
  # Tests still exercise flat SimpleNamespace/getattr compatibility.
  assert by_classification["TEST_COMPATIBILITY_RETAIN_2I_A"] >= 1


def test_compatibility_surface_artifact_matches_live_audit():
  disk = json.loads(_COMPAT_ARTIFACT.read_text(encoding="utf-8"))
  live = audit_compatibility_surface(_REPO_ROOT)
  assert disk["source_fingerprint"] == live["source_fingerprint"]
  assert disk["counts"] == live["counts"]


# --- production is facade / flat / compat-class free ------------------------

def test_no_production_runtime_config_facade_calls():
  audit = audit_compatibility_surface(_REPO_ROOT)
  production_facade = [
    usage for usage in audit["usages"]
    if usage["classification"] == "PRODUCTION_REMOVE_2I_A"
  ]
  assert production_facade == []


def test_no_production_dynamic_flat_lookups():
  usage = audit_legacy_settings_usage(_REPO_ROOT)
  assert usage["production"]["introspection"] == []


def test_no_production_compatibility_class_imports():
  # Reuses the Phase 2H import guard: Settings / CanonicalSettingsFacade /
  # LegacyCanonicalConfigView imports outside the composition root are banned.
  assert _production_settings_imports() == []


# --- deployment default cutover ---------------------------------------------

def test_compose_default_is_canonical():
  text = (_REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
  assert "APEXVOID_CONFIG_AUTHORITY: ${APEXVOID_CONFIG_AUTHORITY:-canonical}" in text


def test_template_default_is_canonical():
  text = (
    _REPO_ROOT / "deployment-template/docker-compose.yml.j2"
  ).read_text(encoding="utf-8")
  assert "'APEXVOID_CONFIG_AUTHORITY': 'canonical'" in text


def test_env_example_default_is_canonical():
  text = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
  assert "APEXVOID_CONFIG_AUTHORITY=canonical" in text


# --- generated artifacts current --------------------------------------------

def test_generated_artifacts_include_compatibility_surface():
  artifacts = render_artifacts()
  assert any(
    "compatibility-surface-phase-2i-a" in str(path) for path in artifacts
  )
  assert check_artifacts(artifacts) == 0


# --- removal gate: static ---------------------------------------------------

def test_static_gate_ready_for_canonical_observation():
  result = evaluate_static_readiness()
  assert result["status"] == "READY_FOR_CANONICAL_OBSERVATION", result["blockers"]
  assert result["blockers"] == []
  assert result["production_facade_calls"] == 0
  assert result["production_flat_reads"] == 0
  assert result["legacy_still_startable"] is True


def test_static_gate_never_claims_delete_legacy():
  result = evaluate_static_readiness()
  # Static analysis must never certify legacy deletion — only observation can.
  assert result["status"] != "READY_TO_DELETE_LEGACY"
  assert result["status"] in {"READY_FOR_CANONICAL_OBSERVATION", "NOT_READY"}


# --- removal gate: observation evidence -------------------------------------

def _complete_evidence() -> dict:
  return {
    "phase": "2I-A",
    "authority": "canonical",
    "observation_window": {
      "start_date": "2026-08-03",
      "end_date": "2026-08-10",
      "trading_days": [
        "2026-08-03", "2026-08-04", "2026-08-05",
        "2026-08-06", "2026-08-07",
      ],
    },
    "sessions_observed": ["asia", "london", "new_york"],
    "restarts": [
      {"timestamp": "2026-08-05T06:00:00Z", "authority": "canonical",
       "outcome": "clean"},
    ],
    "config_health_checks": [
      {"timestamp": "2026-08-03T00:05:00Z", "status": "pass"},
    ],
    "incidents": [],
    "sign_off": {"approved_by": "operator", "date": "2026-08-11"},
  }


def test_observation_gate_requires_evidence(tmp_path: Path):
  path = tmp_path / "missing.json"
  result = evaluate_observation_evidence(path)
  assert result["status"] == "NOT_READY"
  assert result["problems"]


def test_observation_gate_rejects_incomplete_evidence(tmp_path: Path):
  evidence = _complete_evidence()
  evidence["observation_window"]["trading_days"] = ["2026-08-03"]
  path = tmp_path / "incomplete.json"
  path.write_text(json.dumps(evidence), encoding="utf-8")
  result = evaluate_observation_evidence(path)
  assert result["status"] == "NOT_READY"
  assert any("trading_days" in problem for problem in result["problems"])


def test_observation_gate_accepts_complete_evidence(tmp_path: Path):
  path = tmp_path / "complete.json"
  path.write_text(json.dumps(_complete_evidence()), encoding="utf-8")
  result = evaluate_observation_evidence(path)
  assert result["status"] == "READY_FOR_PHASE_2I_B_REVIEW", result["problems"]
  assert result["problems"] == []
