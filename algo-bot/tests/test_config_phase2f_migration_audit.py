"""AST and generated-ledger guards for Phase 2F trading consumers."""

import json
from pathlib import Path

import pytest

from app.configuration.catalog import iter_catalog_entries
from app.configuration.generate import REPOSITORY_ROOT, render_artifacts
from app.configuration.generated.legacy_access import (
  CANONICAL_PATH_TO_LEGACY_ATTR,
  DIRECT_LEGACY_PATHS,
)
from app.configuration.usage_audit import audit_legacy_settings_usage


pytestmark = pytest.mark.no_database

_MANIFEST = Path(
  "contracts/configuration/consumer-migration-phase-2f.generated.json"
)
_TARGET_ROOTS = {"analysis", "strategies", "actionability", "lifecycle"}
_DEFERRED_ROOTS = {"contract", "execution", "manual_algo", "risk", "runtime"}


def _manifest() -> dict:
  return json.loads((REPOSITORY_ROOT / _MANIFEST).read_text(encoding="utf-8"))


def _audit() -> dict:
  return audit_legacy_settings_usage(REPOSITORY_ROOT)


def _assert_no_flat_reads(root: str) -> None:
  production = _audit()["production"]
  for row in production["attribute_reads"]:
    path = DIRECT_LEGACY_PATHS.get(row["attribute"])
    assert path is None or path[0] != root, row
  for row in production["introspection"]:
    names = row["dynamic_names"] or (
      [row["attribute"]] if row["attribute"] is not None else []
    )
    for name in names:
      path = DIRECT_LEGACY_PATHS.get(name)
      assert path is None or path[0] != root, row


def test_phase2f_manifest_expected_target_roots():
  manifest = _manifest()
  assert set(manifest["candidate_roots"]) == _TARGET_ROOTS
  assert set(manifest["root_counts"]) == _TARGET_ROOTS
  assert manifest["root_counts"] == {
    "actionability": {"eligible_before": 47, "migrated": 47, "remaining": 0},
    "analysis": {"eligible_before": 15, "migrated": 15, "remaining": 0},
    "lifecycle": {"eligible_before": 41, "migrated": 41, "remaining": 0},
    "strategies": {"eligible_before": 37, "migrated": 37, "remaining": 0},
  }


def test_phase2f_manifest_has_no_unknown_blockers():
  assert _manifest()["counts"]["unknown_blockers"] == 0


def test_phase2f_manifest_has_no_eligible_reads_remaining():
  counts = _manifest()["counts"]
  assert counts["eligible_production_reads_before"] == 140
  assert counts["migrated_reads"] == 140
  assert counts["eligible_reads_remaining"] == 0


def test_phase2f_all_migrated_paths_are_legacy_backed():
  migrated = [
    row for row in _manifest()["reads"]
    if row["migration_status"] == "migrated"
  ]
  assert len(migrated) == 140
  for row in migrated:
    path = tuple(row["canonical_path"].split("."))
    assert CANONICAL_PATH_TO_LEGACY_ATTR[path] == row["legacy_attribute"]
    assert row["authority_neutral_support"] is True


def test_no_flat_analysis_settings_reads():
  _assert_no_flat_reads("analysis")


def test_no_flat_strategy_settings_reads():
  _assert_no_flat_reads("strategies")


def test_no_flat_actionability_settings_reads():
  _assert_no_flat_reads("actionability")


def test_no_flat_lifecycle_settings_reads():
  _assert_no_flat_reads("lifecycle")


def test_no_dynamic_phase2f_legacy_lookup():
  for row in _audit()["production"]["introspection"]:
    names = row["dynamic_names"] or (
      [row["attribute"]] if row["attribute"] is not None else []
    )
    for name in names:
      path = DIRECT_LEGACY_PATHS.get(name)
      assert path is None or path[0] not in _TARGET_ROOTS, row


def test_phase2f_modules_do_not_mutate_runtime_config():
  production = _audit()["production"]
  assert production["canonical_writes"] == []
  assert production["canonical_deletions"] == []


def test_phase2f_modules_do_not_read_nonlegacy_canonical_paths():
  catalog_paths = {entry.path for entry in iter_catalog_entries()}
  for row in _audit()["production"]["canonical_reads"]:
    path = row["canonical_path"]
    assert path in catalog_paths, row
    assert tuple(path.split(".")) in CANONICAL_PATH_TO_LEGACY_ATTR, row


def test_phase2g_roots_remain_deferred():
  deferred = [
    row for row in _manifest()["reads"]
    if row["migration_status"] == "deferred"
  ]
  assert len(deferred) == _manifest()["counts"]["deferred_reads"] == 147
  assert {row["root_domain"] for row in deferred} >= _DEFERRED_ROOTS
  for row in deferred:
    if row["root_domain"] in _DEFERRED_ROOTS:
      assert row["migration_classification"] in {
        "PHASE_2G_DEFER", "RUNTIME_DEFER",
      }
      assert row["deferred_reason"]


def test_generated_artifacts_are_current():
  for path, expected in render_artifacts().items():
    assert (REPOSITORY_ROOT / path).read_bytes() == expected, path
