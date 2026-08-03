"""AST and generated-ledger guards for Phase 2E operational consumers."""

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
  "contracts/configuration/consumer-migration-phase-2e.generated.json"
)
_TARGET_ROOTS = {"bootstrap", "delivery", "market_data"}


def _manifest() -> dict:
  return json.loads((REPOSITORY_ROOT / _MANIFEST).read_text(encoding="utf-8"))


def _audit() -> dict:
  return audit_legacy_settings_usage(REPOSITORY_ROOT)


def test_phase2e_manifest_has_no_unknown_blockers():
  counts = _manifest()["counts"]
  assert counts["unknown_blockers"] == 0
  assert counts["eligible_reads_remaining"] == 0


def test_phase2e_migrated_paths_are_legacy_backed():
  migrated = [
    row for row in _manifest()["reads"]
    if row["migration_status"] == "migrated"
  ]
  assert len(migrated) == _manifest()["counts"]["migrated_reads"] == 130
  for row in migrated:
    path = tuple(row["canonical_path"].split("."))
    assert CANONICAL_PATH_TO_LEGACY_ATTR[path] == row["legacy_attribute"]
    assert row["authority_neutral_support"] is True


def test_no_new_phase2e_flat_settings_reads():
  production = _audit()["production"]
  for row in production["attribute_reads"]:
    path = DIRECT_LEGACY_PATHS.get(row["attribute"])
    assert path is None or path[0] not in _TARGET_ROOTS, row
  for row in production["introspection"]:
    names = row["dynamic_names"] or (
      [row["attribute"]] if row["attribute"] is not None else []
    )
    for name in names:
      path = DIRECT_LEGACY_PATHS.get(name)
      assert path is None or path[0] not in _TARGET_ROOTS, row


def test_migrated_modules_do_not_read_nonlegacy_canonical_paths():
  catalog_paths = {entry.path for entry in iter_catalog_entries()}
  for row in _audit()["production"]["canonical_reads"]:
    path = row["canonical_path"]
    assert path in catalog_paths, row
    assert tuple(path.split(".")) in CANONICAL_PATH_TO_LEGACY_ATTR, row


def test_migrated_modules_do_not_mutate_runtime_config():
  production = _audit()["production"]
  assert production["canonical_writes"] == []
  assert production["canonical_deletions"] == []


def test_generated_artifacts_are_current():
  for path, expected in render_artifacts().items():
    assert (REPOSITORY_ROOT / path).read_bytes() == expected, path
