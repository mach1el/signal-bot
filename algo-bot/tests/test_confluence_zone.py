"""Confluence-merge zone (Codex Prompt P3, Part 1).

Co-located structures (key level, demand/supply, order block, FVG, breaker)
must collapse into ONE ConfluenceZone with the union of their tags, so they
produce one thesis and one order - never several strategies each ordering on
the same or a nearby band.
"""

from __future__ import annotations

import pytest

from app.analysis.confluence_zone import (
  ConfluenceMember,
  claim_confluence_zone,
  merge_confluence_zones,
  release_confluence_zone,
  resolve_confluence_zone_id,
)
from app.persistence import redis_state


def _member(member_id, side, low, high, kind, score=5.0) -> ConfluenceMember:
  return ConfluenceMember(member_id, side, low, high, kind, score)


def test_merge_co_located_confluence_becomes_one_zone_with_union_tags():
  members = [
    _member("key-1", "buy", 4102.0, 4102.5, "key_level"),
    _member("demand-1", "buy", 4102.3, 4103.8, "demand"),
    _member("ob-1", "buy", 4103.0, 4104.2, "ob"),
    _member("fvg-1", "buy", 4103.8, 4105.0, "fvg"),
  ]

  zones = merge_confluence_zones(
    members, symbol="XAU", atr=2.0, pip_size=0.1, max_width=3.0, gap=1.0,
  )

  assert len(zones) == 1
  zone = zones[0]
  assert zone.tags == ("demand", "fvg", "key_level", "ob")
  assert zone.low == 4102.0
  assert zone.high == 4105.0
  assert zone.high - zone.low <= 3.0
  assert zone.confluence == 4
  assert set(zone.provenance) == {"key-1", "demand-1", "ob-1", "fvg-1"}


@pytest.mark.asyncio
async def test_second_strategy_on_same_merged_zone_is_rejected():
  client = redis_state.get_client()
  members = [
    _member("key-1", "buy", 4102.0, 4102.5, "key_level"),
    _member("demand-1", "buy", 4102.3, 4103.8, "demand"),
    _member("ob-1", "buy", 4103.0, 4104.2, "ob"),
    _member("fvg-1", "buy", 4103.8, 4105.0, "fvg"),
  ]
  zone = merge_confluence_zones(
    members, symbol="XAU", atr=2.0, pip_size=0.1, max_width=3.0, gap=1.0,
  )[0]

  # Strategy A confirms off the key_level member alone; strategy B confirms
  # off the FVG member alone. Both resolve onto the same merged zone because
  # their individual bands overlap the same structural cluster.
  zone_id_a = resolve_confluence_zone_id(
    4102.0, 4102.5, "buy", ["key_level"],
    other_members=[m for m in members if m.member_id != "key-1"],
    symbol="XAU", atr=2.0, pip_size=0.1, candidate_id="setup-a",
  )
  zone_id_b = resolve_confluence_zone_id(
    4103.8, 4105.0, "buy", ["fvg"],
    other_members=[m for m in members if m.member_id != "fvg-1"],
    symbol="XAU", atr=2.0, pip_size=0.1, candidate_id="setup-b",
  )
  assert zone_id_a == zone_id_b == zone.zone_id

  first_claim = await claim_confluence_zone(
    client, zone_id=zone_id_a, owner_id="setup-a",
  )
  second_claim = await claim_confluence_zone(
    client, zone_id=zone_id_b, owner_id="setup-b",
  )

  assert first_claim is True
  assert second_claim is False

  # Releasing as the non-owner is a no-op; releasing as the owner frees it.
  await release_confluence_zone(client, zone_id=zone_id_a, owner_id="setup-b")
  still_blocked = await claim_confluence_zone(
    client, zone_id=zone_id_b, owner_id="setup-b",
  )
  assert still_blocked is False

  await release_confluence_zone(client, zone_id=zone_id_a, owner_id="setup-a")
  now_free = await claim_confluence_zone(
    client, zone_id=zone_id_b, owner_id="setup-b",
  )
  assert now_free is True


def test_merge_respects_gap():
  # 4100-4101 and 4101.5-4102.5: 0.5 gap, within zone_merge_gap=1.0 -> merge.
  members = [
    _member("a", "buy", 4100.0, 4101.0, "demand"),
    _member("b", "buy", 4101.5, 4102.5, "ob"),
  ]
  zones = merge_confluence_zones(
    members, symbol="XAU", atr=2.0, pip_size=0.1, max_width=3.0, gap=1.0,
  )
  assert len(zones) == 1
  assert zones[0].low == 4100.0
  assert zones[0].high == 4102.5

  # Same bands, gap tightened to 0.2 -> 0.5 gap no longer merges.
  zones_tight_gap = merge_confluence_zones(
    members, symbol="XAU", atr=2.0, pip_size=0.1, max_width=3.0, gap=0.2,
  )
  assert len(zones_tight_gap) == 2


def test_merge_respects_max_width_keeps_tighter_cluster():
  # a+b overlap tightly (union 4100-4101.2); c is close enough to gap-merge
  # with b alone, but absorbing it into the a+b cluster would push the union
  # width past max_width=3.0, so c must stay its own zone.
  members = [
    _member("a", "buy", 4100.0, 4100.6, "demand"),
    _member("b", "buy", 4100.6, 4101.2, "ob"),
    _member("c", "buy", 4103.0, 4103.5, "fvg"),
  ]
  zones = merge_confluence_zones(
    members, symbol="XAU", atr=2.0, pip_size=0.1, max_width=3.0, gap=2.0,
  )
  # a+b merge (union width 1.2 <= 3.0); c would push a+b+c to 3.5 > 3.0, so
  # it is kept separate rather than absorbed.
  by_provenance = {frozenset(zone.provenance): zone for zone in zones}
  assert frozenset({"a", "b"}) in by_provenance
  assert frozenset({"c"}) in by_provenance
  merged = by_provenance[frozenset({"a", "b"})]
  assert merged.high - merged.low <= 3.0


def test_no_opposing_merge_demand_and_supply_stay_distinct():
  members = [
    _member("demand-1", "buy", 4100.0, 4101.0, "demand"),
    _member("supply-1", "sell", 4100.2, 4101.2, "supply"),
  ]

  zones = merge_confluence_zones(
    members, symbol="XAU", atr=2.0, pip_size=0.1, max_width=3.0, gap=1.0,
  )

  assert len(zones) == 2
  sides = {zone.side for zone in zones}
  assert sides == {"buy", "sell"}
  for zone in zones:
    assert len(zone.provenance) == 1
