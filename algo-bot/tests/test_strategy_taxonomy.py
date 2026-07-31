"""Exact strategy-family registry — no substring classification."""

from __future__ import annotations

import pytest

from app.autotrade.strategy_taxonomy import (
  CANONICAL_FAMILY_REACTION,
  CANONICAL_FAMILY_ZONE,
  REACTION_STRATEGIES,
  ZONE_STRATEGIES,
  canonical_family,
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
  assert is_zone_strategy("Demand Zone")
  assert is_zone_strategy("Demand Zone Reaction")
  assert is_zone_strategy("Zone Reaction")
  assert canonical_family("Demand Zone") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Demand Zone Reaction") == CANONICAL_FAMILY_ZONE
  assert canonical_family("Zone Reaction") == CANONICAL_FAMILY_ZONE


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
  assert "Demand Zone Reaction" in ZONE_STRATEGIES
  assert "Demand Zone Reaction" not in REACTION_STRATEGIES
  assert canonical_family("Something Reaction Something") != CANONICAL_FAMILY_REACTION
  assert canonical_family("Demand Zoneish") != CANONICAL_FAMILY_ZONE
