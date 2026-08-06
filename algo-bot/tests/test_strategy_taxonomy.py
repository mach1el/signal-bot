"""Exact strategy-family registry — no substring classification."""

from __future__ import annotations

import pytest

from app.autotrade.strategy_taxonomy import (
  CANONICAL_FAMILY_RANGE,
  CANONICAL_FAMILY_REACTION,
  CANONICAL_FAMILY_ZONE,
  RANGE_STRATEGIES,
  REACTION_STRATEGIES,
  ZONE_STRATEGIES,
  bypasses_opposing_structure_gates,
  canonical_family,
  is_range_strategy,
  is_reaction_strategy,
  is_zone_strategy,
)


pytestmark = pytest.mark.no_database


def test_reaction_family_contains_exactly_three_strategies():
  assert REACTION_STRATEGIES == frozenset({
    "Key Level Reaction",
    "Session Level Reaction",
    "Trendline Reaction",
  })
  assert len(REACTION_STRATEGIES) == 3


def test_key_level_reaction_is_reaction():
  assert is_reaction_strategy("Key Level Reaction")
  assert canonical_family("Key Level Reaction") == CANONICAL_FAMILY_REACTION


def test_session_level_reaction_is_reaction():
  assert is_reaction_strategy("Session Level Reaction")
  assert canonical_family("Session Level Reaction") == CANONICAL_FAMILY_REACTION


def test_trendline_reaction_is_reaction():
  assert is_reaction_strategy("Trendline Reaction")
  assert canonical_family("Trendline Reaction") == CANONICAL_FAMILY_REACTION


def test_demand_zone_is_not_reaction():
  assert not is_reaction_strategy("Demand Zone")
  assert not is_reaction_strategy("Demand Zone Reaction")
  assert not is_reaction_strategy("Zone Reaction")
  assert not is_reaction_strategy("Flip Zone")
  assert is_zone_strategy("Demand Zone")
  assert is_zone_strategy("Demand Zone Reaction")
  assert is_zone_strategy("Zone Reaction")
  assert is_zone_strategy("Flip Zone")
  assert canonical_family("Demand Zone") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Demand Zone Reaction") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Zone Reaction") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Flip Zone") == CANONICAL_FAMILY_ZONE


def test_supply_zone_is_not_reaction():
  assert not is_reaction_strategy("Supply Zone")
  assert not is_reaction_strategy("Supply Zone Reaction")
  assert is_zone_strategy("Supply Zone")
  assert is_zone_strategy("Supply Zone Reaction")
  assert canonical_family("Supply Zone") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Supply Zone Reaction") == CANONICAL_FAMILY_ZONE


def test_strategy_names_are_not_classified_by_substring():
  # Exact registry only — containing "Reaction" or "Zone" must not classify.
  assert not is_reaction_strategy("Fake Reaction Setup")
  assert not is_reaction_strategy("Mapped Zone Reaction")
  assert not is_zone_strategy("Mapped Zone Reaction")
  assert "Zone Reaction" in ZONE_STRATEGIES
  assert "Flip Zone" in ZONE_STRATEGIES
  assert "Demand Zone Reaction" in ZONE_STRATEGIES
  assert "Demand Zone Reaction" not in REACTION_STRATEGIES
  assert canonical_family("Something Reaction Something") != CANONICAL_FAMILY_REACTION
  assert canonical_family("Demand Zoneish") != CANONICAL_FAMILY_ZONE
  assert canonical_family("Fake Flip Zone") != CANONICAL_FAMILY_ZONE


def test_range_strategies_bypass_opposing_structure_gates():
  for name in RANGE_STRATEGIES:
    assert is_range_strategy(name)
    assert not bypasses_opposing_structure_gates(name)
    # Ladder floor 15p is enough to open / bypass opposing.
    assert bypasses_opposing_structure_gates(name, full_take_profit_pips=15)
    assert bypasses_opposing_structure_gates(name, full_take_profit_pips=20)
    assert not bypasses_opposing_structure_gates(name, full_take_profit_pips=0)
    assert canonical_family(name) == CANONICAL_FAMILY_RANGE
  assert not bypasses_opposing_structure_gates(
    "Key Level Reaction", full_take_profit_pips=15,
  )
  assert not bypasses_opposing_structure_gates(
    "Zone Reaction", full_take_profit_pips=15,
  )
  assert not bypasses_opposing_structure_gates(
    "Liquidity Sweep", full_take_profit_pips=15,
  )


def test_hfs_strategies_bypass_opposing_when_room_fits():
  from app.autotrade.strategy_taxonomy import (
    CANONICAL_FAMILY_HFS,
    HFS_STRATEGIES,
    is_hfs_strategy,
    match_bypasses_opposing_structure,
  )

  for name in HFS_STRATEGIES:
    assert is_hfs_strategy(name)
    assert not bypasses_opposing_structure_gates(name)
    assert bypasses_opposing_structure_gates(name, full_take_profit_pips=20)
    assert canonical_family(name) == CANONICAL_FAMILY_HFS
  assert is_hfs_strategy("HFS Custom Archetype")
  assert bypasses_opposing_structure_gates(
    "HFS Range Sweep",
    full_take_profit_pips=15,
    family="hfs",
    strategy_mode="hfs_scalp",
  )
  # family/mode alone must still require fitted room
  assert not bypasses_opposing_structure_gates(
    "Unknown", family="hfs", strategy_mode="hfs_scalp",
  )
  assert bypasses_opposing_structure_gates(
    "Unknown",
    full_take_profit_pips=20,
    family="hfs",
    strategy_mode="hfs_scalp",
  )

  class _Match:
    strategy = "HFS Impulse Pullback"
    full_take_profit_pips = 25
    family = "hfs"
    strategy_mode = "hfs_scalp"

  assert match_bypasses_opposing_structure(_Match())
  assert not match_bypasses_opposing_structure(
    type("M", (), {
      "strategy": "HFS Impulse Pullback",
      "full_take_profit_pips": None,
      "family": "hfs",
      "strategy_mode": "hfs_scalp",
    })(),
  )
