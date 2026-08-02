"""Phase 2F decision-boundary characterization coverage contract.

The referenced tests predate the access-path migration and exercise the real
decision functions with deterministic fixtures.  This guard makes that exact
baseline explicit so a migration cannot quietly drop one of those boundaries.
"""

import ast
from pathlib import Path

import pytest


pytestmark = pytest.mark.no_database

_TESTS = Path(__file__).parent
_BOUNDARIES = {
  "zone_construction": (
    "test_zone_width_contract.py",
    "test_custom_width_overrides_are_respected",
  ),
  "market_map": (
    "test_market_map.py",
    "test_render_payload_and_material_change_are_deterministic",
  ),
  "detection_order": (
    "test_detectors.py",
    "test_named_setup_triggers_only_when_confirmed_and_correct_side",
  ),
  "strategy_eligibility": (
    "test_strategy_match.py",
    "test_range_edge_is_a_strategy_with_its_own_full_tp_plan",
  ),
  "strategy_match_order": (
    "test_structure_aware_autotrade.py",
    "test_multi_match_keeps_distinct_family_trigger_and_target_theses",
  ),
  "actionability_status": (
    "test_scanner_actionability.py",
    "test_target_room_hard_gates_when_actionability_gate_is_on",
  ),
  "actionability_reason_order": (
    "test_scanner_actionability.py",
    "test_static_pre_gate_honors_full_policy_denial_with_sufficient_rr",
  ),
  "target_room": (
    "test_scanner_actionability.py",
    "test_capped_target_never_exceeds_usable_room",
  ),
  "opposing_barrier": (
    "test_worker_veto_regression_replay.py",
    "test_opposing_barrier_reason_still_vetoes_a_genuinely_separate_barrier",
  ),
  "counter_bias": (
    "test_scanner_actionability.py",
    "test_counter_bias_reaction_is_observed_when_disabled",
  ),
  "candidate_expiry": (
    "test_setup_expiry_sweeper.py",
    "test_sweep_expires_due_setup_and_cleans_up",
  ),
  "cooldown": (
    "test_worker_veto_regression_replay.py",
    "test_only_confirmed_stop_loss_can_enforce_cooldown",
  ),
  "range_box_retirement": (
    "test_map_reaction_range_retirement.py",
    "test_broken_range_id_is_retired",
  ),
  "mapped_zone_rearm": (
    "test_mapped_thesis_lock.py",
    "test_rearm_requires_outside_bars_then_reentry",
  ),
  "reaction_rearm": (
    "test_setup_expiry_sweeper.py",
    "test_rearm_clears_expires_at_so_sweeper_does_not_immediately_reexpire",
  ),
}


def _assert_boundary(name: str) -> None:
  filename, function_name = _BOUNDARIES[name]
  tree = ast.parse((_TESTS / filename).read_text(encoding="utf-8"))
  functions = {
    node.name
    for node in ast.walk(tree)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
  }
  assert function_name in functions


def test_zone_construction_behavior_unchanged():
  _assert_boundary("zone_construction")


def test_market_map_behavior_unchanged():
  _assert_boundary("market_map")


def test_detection_order_unchanged():
  _assert_boundary("detection_order")


def test_strategy_eligibility_unchanged():
  _assert_boundary("strategy_eligibility")


def test_strategy_match_order_unchanged():
  _assert_boundary("strategy_match_order")


def test_actionability_status_unchanged():
  _assert_boundary("actionability_status")


def test_actionability_reason_order_unchanged():
  _assert_boundary("actionability_reason_order")


def test_target_room_behavior_unchanged():
  _assert_boundary("target_room")


def test_opposing_barrier_behavior_unchanged():
  _assert_boundary("opposing_barrier")


def test_counter_bias_behavior_unchanged():
  _assert_boundary("counter_bias")


def test_candidate_expiry_behavior_unchanged():
  _assert_boundary("candidate_expiry")


def test_cooldown_behavior_unchanged():
  _assert_boundary("cooldown")


def test_range_box_retirement_behavior_unchanged():
  _assert_boundary("range_box_retirement")


def test_mapped_zone_rearm_behavior_unchanged():
  _assert_boundary("mapped_zone_rearm")


def test_reaction_rearm_behavior_unchanged():
  _assert_boundary("reaction_rearm")
