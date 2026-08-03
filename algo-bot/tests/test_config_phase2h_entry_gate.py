"""Phase 2H entry-gate assertions.

The entry gate for Phase 2H requires that the Phase 2G consumer migration is
complete: zero production flat ``Settings`` reads and a clean Phase 2G manifest
(no unknown blockers, no eligible reads remaining).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.configuration.phase2h_gate import evaluate_phase2h_readiness

pytestmark = pytest.mark.no_database

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PHASE_2G_MANIFEST = (
  _REPO_ROOT
  / "contracts/configuration/consumer-migration-phase-2g.generated.json"
)


def test_phase2h_entry_gate_requires_zero_production_flat_reads():
  result = evaluate_phase2h_readiness()
  assert result["production_flat_reads"] == 0, result["blockers"]
  assert result["production_settings_imports"] == 0, result[
    "production_settings_import_details"
  ]
  assert result["status"] == "READY_FOR_PHASE_2H", result["blockers"]


def test_phase2h_entry_gate_requires_phase2g_manifest():
  manifest = json.loads(_PHASE_2G_MANIFEST.read_text(encoding="utf-8"))
  counts = manifest["counts"]
  assert int(counts["unknown_blockers"]) == 0
  assert int(counts["eligible_reads_remaining"]) == 0
  assert int(counts["production_flat_reads_remaining"]) == 0
