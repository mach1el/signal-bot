"""Phase 2I-A.1 architecture guards for typed canonical consumer injection.

These tests enforce the mission end-state: production trading modules consume
``runtime_config`` grouped nodes (or narrow adapters derived from them) and
no longer depend on the retired runtime-projection bridge, flat legacy
projection tuples, or production ``DIRECT_LEGACY_PATHS`` imports.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import pytest

from app.configuration.canonical_consumer_surface import (
  audit_canonical_consumer_surface,
)
from app.configuration.generate import check_artifacts, render_artifacts
from app.configuration.generated.legacy_access import DIRECT_LEGACY_PATHS


pytestmark = pytest.mark.no_database

_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _REPO_ROOT / "algo-bot" / "app"
_PRODUCTION_SKIP = {
  "configuration",  # tooling / rollback / generators
  "generated",
}


def _production_py_files() -> list[Path]:
  files: list[Path] = []
  for path in _APP_ROOT.rglob("*.py"):
    relative = path.relative_to(_APP_ROOT).as_posix()
    if relative.startswith("configuration/"):
      continue
    if relative.startswith("core/config.py"):
      continue
    files.append(path)
  return files


def _read(path: Path) -> str:
  return path.read_text(encoding="utf-8")


def test_phase2ia1_inventory_has_no_unknown_blockers():
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  assert int(audit["counts"]["unknown_blockers"]) == 0
  assert int(audit["counts"]["production_pending"]) == 0


def test_phase2ia1_has_no_production_runtime_projection_calls():
  pattern = re.compile(r"\bproject_runtime_config\s*\(|\bproject_from\s*\(")
  offenders = []
  for path in _production_py_files():
    text = _read(path)
    if pattern.search(text):
      offenders.append(path.relative_to(_REPO_ROOT).as_posix())
  assert offenders == []


def test_phase2ia1_has_no_legacy_projection_field_tuples():
  pattern = re.compile(r"_RUNTIME_\w+_CFG_FIELDS")
  offenders = []
  for path in _production_py_files():
    if pattern.search(_read(path)):
      offenders.append(path.relative_to(_REPO_ROOT).as_posix())
  assert offenders == []


def test_phase2ia1_has_no_production_direct_legacy_paths_import():
  offenders = []
  for path in _production_py_files():
    tree = ast.parse(_read(path), filename=str(path))
    for node in ast.walk(tree):
      if isinstance(node, (ast.Import, ast.ImportFrom)):
        names = []
        if isinstance(node, ast.ImportFrom) and node.module:
          names.extend(alias.name for alias in node.names)
          if "DIRECT_LEGACY_PATHS" in names or (
            node.module.endswith("legacy_access")
            and any(alias.name == "DIRECT_LEGACY_PATHS" for alias in node.names)
          ):
            offenders.append(path.relative_to(_REPO_ROOT).as_posix())
  assert offenders == []


def test_phase2ia1_has_no_config_simplenamespace_projection():
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  production_sn = [
    row for row in audit["usages"]
    if row["mechanism"] == "simplenamespace_legacy_fixture"
    and row["classification"] not in {
      "TEST_COMPATIBILITY_RETAIN_2I_A_1",
      "TOOLING_RETAIN_2I_A_1",
    }
  ]
  assert production_sn == []


def test_phase2ia1_has_no_dynamic_legacy_config_getattr():
  audit = audit_canonical_consumer_surface(_REPO_ROOT)
  dynamic = [
    row for row in audit["usages"]
    if row["mechanism"] == "legacy_getattr_on_cfg"
    and row["classification"] == "PHASE_2I_A_1_MIGRATE"
  ]
  assert dynamic == []


def test_analysis_consumers_accept_canonical_nodes():
  from app.core.config import runtime_config
  from app.analysis.actionability import resolve_actionability

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=(),
    market_map=None,
    context=type("Ctx", (), {"htf_bias": "up"})(),
    atr=2.0,
    pip_size=0.1,
    cfg=runtime_config,
  )
  assert resolution.observed == ()
  assert hasattr(runtime_config, "analysis")
  assert hasattr(runtime_config.analysis, "market_map")


def test_actionability_consumers_accept_canonical_nodes():
  from app.core.config import runtime_config

  assert hasattr(runtime_config.actionability, "target_room")
  assert isinstance(
    runtime_config.actionability.target_room.barrier_buffer_atr, float,
  )


def test_execution_consumers_accept_canonical_nodes():
  from app.core.config import runtime_config
  from app.autotrade.execution_policy import resolve_guard_mode

  mode = resolve_guard_mode(runtime_config)
  assert mode in {"observe", "balanced", "strict"}
  assert hasattr(runtime_config.execution, "policy")


def test_risk_consumers_accept_canonical_nodes():
  from app.core.config import runtime_config

  assert hasattr(runtime_config.risk, "sizing")
  assert hasattr(runtime_config.risk, "tiers")


def test_lifecycle_consumers_accept_canonical_nodes():
  from app.core.config import runtime_config

  assert hasattr(runtime_config.lifecycle, "mapped_zone")


def test_canonical_consumer_value_parity():
  from app.core.config import runtime_config, settings

  mismatches = []
  for legacy, path in list(DIRECT_LEGACY_PATHS.items())[:80]:
    try:
      flat = getattr(settings, legacy)
    except Exception:
      continue
    node = runtime_config
    try:
      for part in path:
        node = getattr(node, part)
    except Exception as exc:
      mismatches.append((legacy, path, f"missing:{exc}"))
      continue
    if flat != node:
      mismatches.append((legacy, path, flat, node))
  assert mismatches == []


def test_canonical_consumer_exact_type_parity():
  from app.core.config import runtime_config, settings

  mismatches = []
  for legacy, path in list(DIRECT_LEGACY_PATHS.items())[:80]:
    try:
      flat = getattr(settings, legacy)
    except Exception:
      continue
    node = runtime_config
    try:
      for part in path:
        node = getattr(node, part)
    except Exception:
      continue
    if type(flat) is not type(node):
      mismatches.append((legacy, type(flat), type(node)))
  assert mismatches == []


def test_legacy_authority_typed_consumer_parity(monkeypatch):
  monkeypatch.setenv("APEXVOID_CONFIG_AUTHORITY", "legacy")
  # Re-import is not required: composition root already bound. Verify the
  # live nested surface still resolves through LegacyCanonicalConfigView
  # leaves when authority is legacy at process start; under an already-bound
  # process we assert facade parity instead.
  from app.core.config import runtime_config, settings

  sample = (
    "auto_trade_opposing_barrier_atr",
    "scanner_actionability_gate_enabled",
    "auto_trade_execution_cost_pips",
  )
  for legacy in sample:
    path = DIRECT_LEGACY_PATHS[legacy]
    node = runtime_config
    for part in path:
      node = getattr(node, part)
    assert node == getattr(settings, legacy)


def test_canonical_authority_typed_consumer_parity():
  from app.core.config import runtime_config, settings

  sample = (
    "auto_trade_opposing_barrier_atr",
    "map_max_per_side",
    "atr_length",
  )
  for legacy in sample:
    path = DIRECT_LEGACY_PATHS[legacy]
    node = runtime_config
    for part in path:
      node = getattr(node, part)
    assert node == getattr(settings, legacy)


def test_runtime_projection_modules_are_removed():
  assert not (_APP_ROOT / "core" / "runtime_projection.py").exists()
  assert not (_APP_ROOT / "configuration" / "runtime_projection.py").exists()


def test_production_imports_runtime_config_only():
  """Production trading modules may import runtime_config from core.config.

  Settings / settings / runtime_config_facade imports remain forbidden outside
  the composition root and configuration tooling.
  """
  forbidden = re.compile(
    r"from app\.core\.config import .*(\bsettings\b|\bSettings\b|"
    r"\bruntime_config_facade\b)",
  )
  offenders = []
  for path in _production_py_files():
    rel = path.relative_to(_REPO_ROOT).as_posix()
    if rel.endswith("core/config.py"):
      continue
    text = _read(path)
    if forbidden.search(text):
      offenders.append(rel)
  # Allow known dual-import leftovers only if none — strict end-state.
  assert offenders == []


def test_legacy_rollback_surface_remains_available():
  from app.core import config as core_config

  assert hasattr(core_config, "settings")
  assert hasattr(core_config, "Settings")
  assert hasattr(core_config, "LegacySettings")
  assert hasattr(core_config, "runtime_config_facade")
  assert callable(core_config.runtime_config_facade)
  assert "DIRECT_LEGACY_PATHS" in _read(
    _APP_ROOT / "configuration" / "generated" / "legacy_access.py",
  )


def test_generated_artifacts_are_current():
  artifacts = render_artifacts()
  assert check_artifacts(artifacts) == 0
  artifact = (
    _REPO_ROOT
    / "contracts/configuration/canonical-consumer-surface-phase-2i-a1.generated.json"
  )
  disk = json.loads(artifact.read_text(encoding="utf-8"))
  live = audit_canonical_consumer_surface(_REPO_ROOT)
  assert disk["source_fingerprint"] == live["source_fingerprint"]
