"""AST and generated-ledger guards for Phase 2G final configuration consumers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.configuration.catalog import iter_catalog_entries
from app.configuration.generate import PHASE_2G_ROOTS, REPOSITORY_ROOT, render_artifacts
from app.configuration.generated.legacy_access import (
  CANONICAL_PATH_TO_LEGACY_ATTR,
  DIRECT_LEGACY_PATHS,
)
from app.configuration.phase2h_gate import evaluate_phase2h_readiness
from app.configuration.usage_audit import audit_legacy_settings_usage


pytestmark = pytest.mark.no_database

_MANIFEST = Path(
  "contracts/configuration/consumer-migration-phase-2g.generated.json"
)
_TARGET_ROOTS = set(PHASE_2G_ROOTS)


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


def test_phase2g_manifest_expected_roots():
  manifest = _manifest()
  assert set(manifest["candidate_roots"]) == _TARGET_ROOTS
  assert set(manifest["root_counts"]) == _TARGET_ROOTS
  assert manifest["root_counts"] == {
    "contract": {"eligible_before": 42, "migrated": 42, "remaining": 0},
    "execution": {"eligible_before": 41, "migrated": 41, "remaining": 0},
    "manual_algo": {"eligible_before": 13, "migrated": 13, "remaining": 0},
    "risk": {"eligible_before": 11, "migrated": 11, "remaining": 0},
    "runtime": {"eligible_before": 35, "migrated": 35, "remaining": 0},
  }


def test_phase2g_manifest_has_no_unknown_blockers():
  assert _manifest()["counts"]["unknown_blockers"] == 0


def test_phase2g_manifest_has_no_eligible_reads_remaining():
  counts = _manifest()["counts"]
  assert counts["eligible_reads_remaining"] == 0
  assert counts["production_flat_reads_remaining"] == 0


def test_no_flat_runtime_settings_reads():
  _assert_no_flat_reads("runtime")


def test_no_flat_contract_settings_reads():
  _assert_no_flat_reads("contract")


def test_no_flat_execution_settings_reads():
  _assert_no_flat_reads("execution")


def test_no_flat_risk_settings_reads():
  _assert_no_flat_reads("risk")


def test_no_flat_manual_algo_settings_reads():
  _assert_no_flat_reads("manual_algo")


def test_no_production_settings_imports():
  result = evaluate_phase2h_readiness()
  assert result["production_settings_imports"] == 0, result[
    "production_settings_import_details"
  ]


def test_no_production_dynamic_settings_lookup():
  assert _audit()["production"]["introspection"] == []


def test_no_production_derived_settings_properties():
  derived = {
    "signal_vip_channel_id",
    "telegram_chat_id",
    "xau_vip_channel_id",
    "xau_public_channel_id",
  }
  for row in _audit()["production"]["attribute_reads"]:
    assert row["attribute"] not in derived, row


def test_no_optional_compatibility_settings_reads():
  optional = {"auto_trade_broker_lot_size", "auto_trade_v7_max_volume"}
  for row in _audit()["production"]["attribute_reads"]:
    assert row["attribute"] not in optional, row
  for row in _audit()["production"]["introspection"]:
    names = row["dynamic_names"] or []
    assert not optional.intersection(names), row


def test_phase2g_migrated_paths_are_legacy_backed():
  for row in _manifest()["reads"]:
    if row["migration_classification"] != "PHASE_2G_MIGRATE":
      continue
    path = tuple(row["canonical_path"].split("."))
    assert CANONICAL_PATH_TO_LEGACY_ATTR[path] == row["legacy_attribute"]
    assert row["authority_neutral_support"] is True


def test_phase2g_modules_do_not_read_nonlegacy_canonical_paths():
  catalog_paths = {entry.path for entry in iter_catalog_entries()}
  for row in _audit()["production"]["canonical_reads"]:
    path = row["canonical_path"]
    root = path.split(".", 1)[0]
    if root not in _TARGET_ROOTS:
      continue
    assert path in catalog_paths, row
    assert tuple(path.split(".")) in CANONICAL_PATH_TO_LEGACY_ATTR, row


def test_phase2h_entry_gate_is_ready():
  result = evaluate_phase2h_readiness()
  assert result["status"] == "READY_FOR_PHASE_2H", result


def test_generated_artifacts_are_current():
  for path, expected in render_artifacts().items():
    assert (REPOSITORY_ROOT / path).read_bytes() == expected, path
