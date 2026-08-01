"""Characterization tests for the still-active legacy Settings loader."""

import pytest

from tests.config_characterization import characterization
from tests.config_characterization import csharp_inventory
from tests.config_characterization import direct_conservative_fixture
from tests.config_characterization import direct_demo_eval_fixture
from tests.config_characterization import legacy_inventory
from tests.config_characterization import load_snapshot
from tests.config_characterization import root_compose_demo_fixture
from tests.config_characterization import test_conftest_fixture as conftest_fixture


pytestmark = pytest.mark.no_database


def test_legacy_settings_inventory_is_frozen():
  expected = load_snapshot()["legacy_inventory"]
  assert len(expected) == 316
  assert legacy_inventory() == expected


def test_phase1_catalog_baseline_counts():
  snapshot = load_snapshot()
  assert snapshot["phase1_counts"] == {
    "total_items": 437,
    "python_settings_fields": 316,
    "ctrader_only_options": 47,
    "environment_only_items": 7,
    "hardcoded_config_like_constants": 67,
    "shared_python_ctrader_fields": 95,
    "duplicate_or_conflicting_items": 30,
    "fragmented_source_items": 121,
    "canonical_env_collisions": 0,
    "alias_collisions": 0,
  }
  assert len(snapshot["known_conflict_item_ids"]) == 30


def test_direct_conservative_fixture_is_frozen():
  expected = load_snapshot()["fixtures"]["direct_conservative"]
  assert len(expected) == 316
  assert direct_conservative_fixture() == expected


def test_direct_demo_eval_fixture_is_frozen():
  expected = load_snapshot()["fixtures"]["direct_demo_eval"]
  assert len(expected) == 316
  assert direct_demo_eval_fixture() == expected


def test_root_compose_demo_fixture_is_frozen():
  expected = load_snapshot()["fixtures"]["root_compose_demo_eval"]
  assert len(expected) == 316
  assert root_compose_demo_fixture() == expected


def test_current_test_conftest_environment_is_frozen():
  expected = load_snapshot()["fixtures"]["test_conftest"]
  assert len(expected) == 316
  assert conftest_fixture() == expected


def test_known_demo_compose_divergences_are_preserved():
  direct = direct_demo_eval_fixture()
  compose = root_compose_demo_fixture()
  assert direct["auto_trade_mapped_zone_enabled"] is True
  assert compose["auto_trade_mapped_zone_enabled"] is False
  assert direct["auto_trade_market_map_guard_enabled"] is True
  assert compose["auto_trade_market_map_guard_enabled"] is False


def test_csharp_option_inventory_is_frozen():
  assert csharp_inventory() == load_snapshot()["csharp"]


def test_complete_characterization_is_deterministic():
  assert characterization() == load_snapshot()
