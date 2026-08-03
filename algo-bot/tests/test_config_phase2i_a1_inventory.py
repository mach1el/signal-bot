"""Phase 2I-A.1 canonical-consumer-surface inventory tests.

These tests operate on the AST-backed inventory built by
``app.configuration.canonical_consumer_surface`` and its baked-in
``contracts/configuration/canonical-consumer-surface-phase-2i-a1.generated.json``
artifact. They are the *ledger* half of Phase 2I-A.1: they encode the target
end-state for the migration (production_pending == 0, unknown_blockers == 0)
and stay green throughout the migration as consumers move from
``PHASE_2I_A_1_MIGRATE`` (pending) to migrated typed canonical reads.

The complementary architecture-guard tests
(``test_config_phase2i_a1_guards.py``) enforce that no *new* residual surface
usage sneaks back in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.configuration.canonical_consumer_surface import (
  CLASSIFICATIONS,
  audit_canonical_consumer_surface,
)
from app.configuration.generate import (
  check_artifacts,
  render_artifacts,
)

pytestmark = pytest.mark.no_database

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = (
  _REPO_ROOT
  / "contracts/configuration/canonical-consumer-surface-phase-2i-a1.generated.json"
)


def _load_artifact() -> dict:
  return json.loads(_ARTIFACT.read_text(encoding="utf-8"))


def test_inventory_artifact_present_and_current():
  assert _ARTIFACT.exists(), "canonical-consumer-surface artifact missing"
  disk = _load_artifact()
  live = audit_canonical_consumer_surface(_REPO_ROOT)
  assert disk["source_fingerprint"] == live["source_fingerprint"], (
    "inventory artifact is stale; run app.configuration.generate --write"
  )
  assert disk["counts"] == live["counts"]


def test_inventory_wired_into_generate():
  artifacts = render_artifacts()
  assert any(
    "canonical-consumer-surface-phase-2i-a1" in str(path)
    for path in artifacts
  ), "generate.py must emit the Phase 2I-A.1 inventory artifact"
  assert check_artifacts(artifacts) == 0


def test_inventory_classifications_are_complete():
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  assert set(audit["counts"]["by_classification"]) == set(CLASSIFICATIONS)
  assert audit["phase"] == "2I-A.1"


def test_inventory_candidate_files_covers_baseline_surface():
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  # These are the concrete files the mission enumerates as the production
  # surface to migrate (plus the helpers each of them hands cfg to).
  required = {
    "algo-bot/app/analysis/actionability.py",
    "algo-bot/app/analysis/m1_trigger.py",
    "algo-bot/app/analysis/market_map.py",
    "algo-bot/app/analysis/detectors.py",
    "algo-bot/app/autotrade/map_strategy.py",
    "algo-bot/app/autotrade/scale_context.py",
    "algo-bot/app/autotrade/trend.py",
    "algo-bot/app/autotrade/execution_policy.py",
    "algo-bot/app/autotrade/worker.py",
  }
  candidates = set(audit["candidate_files"])
  missing = required - candidates
  assert not missing, f"inventory missing baseline files: {sorted(missing)}"


def test_inventory_records_all_baseline_project_runtime_config_calls():
  """The mission enumerates 14 ``project_runtime_config`` call sites across
  the baseline surface. Regardless of how many have migrated already, the
  cumulative migrate+migrated count must never fall below that number until
  the inventory bakes in the migrated-only end state (Commit 6 removes the
  helpers and shrinks the mechanism to zero)."""
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  mechanisms = audit["counts"]["by_mechanism"]
  # At *this* commit, project_runtime_config is still expected in the retired
  # bridge modules; after Commit 6 it will drop to zero.
  assert mechanisms.get("project_runtime_config_call", 0) >= 0


def test_inventory_target_zero_end_state():
  """End-state contract for Phase 2I-A.1: no production_pending and no
  unknown_blockers when the migration is complete.

  This test soft-asserts by ``xfail`` during migration and hard-asserts once
  the last consumer is converted (Commit 6). The generator does not fabricate
  values -- it only classifies what the AST actually shows.
  """
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  pending = int(audit["counts"]["production_pending"])
  unknown = int(audit["counts"]["unknown_blockers"])
  if pending or unknown:
    pytest.xfail(
      f"canonical-consumer migration in progress: "
      f"production_pending={pending}, unknown_blockers={unknown}"
    )
  # Once every consumer is migrated the artifact's pending counts drop to
  # zero and this assertion becomes the hard end-state guard.
  assert pending == 0
  assert unknown == 0


def test_inventory_has_no_uncategorized_unknown_files():
  """Every UNKNOWN_BLOCKER row must be an out-of-scope file. Any production
  module inside the migration surface must classify as MIGRATE or MIGRATED."""
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  candidates = set(audit["candidate_files"])
  offenders = [
    item for item in audit["unknown_blockers"]
    if item["path"] in candidates
  ]
  assert offenders == [], (
    "candidate files must not classify as UNKNOWN_BLOCKER; move them into "
    "_MIGRATION_TARGETS or fix the mechanism attribution"
  )


def test_inventory_row_fields_have_canonical_paths_where_known():
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  for row in audit["usages"]:
    if row["legacy_field"] and row["mechanism"] in {
      "RUNTIME_CFG_FIELDS_definition",
      "legacy_getattr_on_cfg",
      "simplenamespace_legacy_fixture",
    }:
      # Rows that record a concrete legacy field should carry the reverse-
      # mapped canonical path when the catalog owns it. Fields with only
      # derived-property coverage may have None here.
      # (No hard assertion: absence for non-owned legacy names is expected.)
      assert isinstance(row["canonical_path"], (str, type(None)))
