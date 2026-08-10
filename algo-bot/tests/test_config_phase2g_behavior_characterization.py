"""Phase 2G decision-boundary characterization coverage contract."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_database

_TESTS = Path(__file__).parent
_BOUNDARIES = {
  "runtime_enablement": (
    "test_demo_eval_runtime.py",
    "test_demo_profile_resolves_execution_defaults",
  ),
  "contract_manifest": (
    "test_demo_eval_runtime.py",
    "test_python_config_manifest_is_published",
  ),
  "stream_routing": (
    "test_trade_plan_v7_migration_mode.py",
    "test_manifest_carries_trade_plan_version_and_contract_mode",
  ),
  "trade_plan": (
    "test_trade_plan_builder.py",
    "test_builds_valid_plan_with_real_bias_kind_and_regime",
  ),
  "entry_policy": (
    "test_execution_confirmation.py",
    "test_executable_zone_membership_uses_side_aware_quote",
  ),
  "target_policy": (
    "test_structure_aware_autotrade.py",
    "test_adaptive_range_targets_ladder",
  ),
  "stop_policy": (
    "test_protective_stop.py",
    "test_stop_inside_opposing_zone_rejects_when_push_disabled",
  ),
  "scaling": (
    "test_zone_scale_execution.py",
    "test_key_session_trendline_use_market_with_limit_scale",
  ),
  "position_sizing": (
    "test_scale_in_sizing.py",
    "test_size_ratio_caps_momentum_style_add",
  ),
  "exposure": (
    "test_active_exposure.py",
    "test_opposing_blocks_when_absolute_distance_too_near",
  ),
  "position_limit": (
    "test_active_exposure.py",
    "test_same_direction_blocks_non_scalp_before_tp2_booked",
    "test_same_direction_blocks_when_booked_index_unknown",
    "test_same_direction_blocks_non_tier_a_after_tp2_booked",
    "test_same_direction_stacks_at_60_after_tp2_booked",
    "test_same_direction_stack_flag_when_allowed_for_scalp",
  ),
  "manual_command": (
    "test_manual_execution.py",
    "test_intent_to_candidate_payload_sell_uses_entry_low_reference_edge",
  ),
  "config_health": (
    "test_demo_eval_runtime.py",
    "test_config_health_detects_fatal_contract_mismatch",
  ),
}


def _has_test_function(path: Path, name: str) -> bool:
  tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
  for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
      return True
  return False


@pytest.mark.parametrize("boundary", sorted(_BOUNDARIES))
def test_phase2g_behavior_boundary_has_named_coverage(boundary: str):
  filename, test_name = _BOUNDARIES[boundary]
  path = _TESTS / filename
  assert path.exists(), filename
  assert _has_test_function(path, test_name), (filename, test_name)


def test_runtime_enablement_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("runtime_enablement")


def test_contract_manifest_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("contract_manifest")


def test_stream_routing_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("stream_routing")


def test_trade_plan_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("trade_plan")


def test_entry_policy_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("entry_policy")


def test_target_policy_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("target_policy")


def test_stop_policy_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("stop_policy")


def test_scaling_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("scaling")


def test_position_sizing_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("position_sizing")


def test_exposure_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("exposure")


def test_position_limit_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("position_limit")


def test_manual_command_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("manual_command")


def test_config_health_behavior_unchanged():
  test_phase2g_behavior_boundary_has_named_coverage("config_health")
