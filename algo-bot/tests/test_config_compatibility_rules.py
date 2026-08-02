"""Source-aware legacy compatibility rule tests."""

import pytest

from app.configuration.resolver import resolve_configuration
from app.configuration.source_types import SourceKind


pytestmark = pytest.mark.no_database


def _resolve(**overrides):
  bundle = {
    "init_values": {},
    "process_environment": {},
    "dotenv_values": {},
    "file_secret_values": {},
  }
  bundle.update(overrides)
  return resolve_configuration(**bundle)


def test_demo_explicit_false_demo_requirement_fails():
  result = _resolve(process_environment={
    "AUTO_TRADE_PROFILE": " demo_EVAL ",
    "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT": "false",
  })
  assert result.profile == "demo_eval"
  assert any(
    conflict.code == "demo_account_requirement"
    for conflict in result.conflicts
  )


def test_conservative_non_demo_derives_strict_guard():
  result = _resolve(process_environment={
    "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT": "false",
  })
  path = "actionability.structural_guard.guard_mode"
  assert result.flat_values[path] == "strict"
  source = result.trace.by_path()[path]
  assert source.source_kind is SourceKind.DERIVED_COMPATIBILITY_RULE
  assert source.compatibility_rule == "conservative_live_structural_guard"


def test_explicit_structural_guard_is_preserved():
  result = _resolve(process_environment={
    "AUTO_TRADE_REQUIRE_DEMO_ACCOUNT": "false",
    "AUTO_TRADE_STRUCTURAL_GUARD_MODE": " observe ",
  })
  path = "actionability.structural_guard.guard_mode"
  assert result.flat_values[path] == "observe"
  assert result.trace.by_path()[path].source_kind is SourceKind.PROCESS_ENV


def test_market_map_guard_follows_mapped_zone_when_implicit():
  result = _resolve(process_environment={
    "AUTO_TRADE_MAPPED_ZONE_ENABLED": "true",
  })
  path = "actionability.gates.market_map_guard_enabled"
  assert result.flat_values[path] is True
  assert result.trace.by_path()[path].compatibility_rule == (
    "market_map_guard_inherits_mapped_zone"
  )


def test_explicit_market_map_guard_is_preserved():
  result = _resolve(process_environment={
    "AUTO_TRADE_MAPPED_ZONE_ENABLED": "true",
    "AUTO_TRADE_MARKET_MAP_GUARD_ENABLED": "false",
  })
  path = "actionability.gates.market_map_guard_enabled"
  assert result.flat_values[path] is False
  assert result.trace.by_path()[path].source_kind is SourceKind.PROCESS_ENV


def test_disabled_reconciliation_forces_off():
  result = _resolve(process_environment={
    "AUTO_TRADE_ZONE_RECONCILE_ENABLED": "false",
    "AUTO_TRADE_ZONE_RECONCILE_MODE": "enforce",
  })
  path = "actionability.zone_reconciliation.mode"
  assert result.flat_values[path] == "off"
  assert result.trace.by_path()[path].compatibility_rule == (
    "disabled_zone_reconciliation_forces_off"
  )


def test_be_ticks_alias_conflict_is_detected():
  result = _resolve(process_environment={
    "AUTO_TRADE_BE_BUFFER_TICKS": "17",
    "AUTO_TRADE_BE_BUFFER_PIPS": "18",
  })
  conflict = result.conflicts[0]
  assert conflict.path == "execution.stops.be_buffer_ticks"
  assert conflict.code == "source_alias_conflict"
  assert "17" not in conflict.message
  assert "18" not in conflict.message


def test_profile_and_policy_strings_keep_normalization_for_model_validation():
  result = _resolve(process_environment={
    "AUTO_TRADE_PROFILE": " DEMO_EVAL ",
    "AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY": " REJECT ",
  })
  assert result.profile == "demo_eval"
  assert result.flat_values[
    "risk.exposure.non_hedged_opposite_policy"
  ] == "reject"
