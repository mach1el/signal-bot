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
  build_confluence_bands,
  claim_confluence_zone,
  confluence_zone_id,
  merge_confluence_zones,
  release_confluence_zone,
  resolve_confluence_zone_id,
)
from app.analysis.technique_geometry import (
  TECHNIQUE_CRT,
  TECHNIQUE_FVG,
  TECHNIQUE_IFVG,
  TECHNIQUE_OB,
  TECHNIQUE_SD,
  TechniqueInstance,
)
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


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


def test_zone_id_survives_width_jitter_across_a_rounding_boundary():
  """Live production incident: the same resistance level (key_level
  4035.63, map SELL 4033-4042) was re-detected twice with a slightly
  different measured entry zone - 4032.9-4038.35 then 4032.84-4038.41 -
  and each publish got its own zone_id, so the SAME structural level
  published two separate TradePlan V8s (and two live PLAN PUBLISHED
  cards) a few minutes apart.

  Both bands share the exact same bucketed midpoint (4035.625 -> 4036.0
  at bucket=1.0), but their widths (5.45 vs 5.57) straddle the 5.5
  rounding boundary at that same bucket size, which used to flip the old
  width_bucket component by a full unit and change the id. Width must
  never be part of zone identity for exactly this reason.
  """
  id_a = confluence_zone_id(
    "XAU", "sell", 4032.9, 4038.35, ("key_level",), atr=1.0, pip_size=0.1,
  )
  id_b = confluence_zone_id(
    "XAU", "sell", 4032.84, 4038.41, ("key_level",), atr=1.0, pip_size=0.1,
  )
  assert id_a == id_b


def test_zone_id_survives_ordinary_atr_drift():
  """Live production incident: the same SELL key-level band near 4079
  ($4077.09-4081.54 across four separate detections, mid always ~4079.2-
  4079.3) rehashed into a brand-new zone_id roughly every five minutes for
  over an hour - each rehash reset its ZoneWatch to touch_count=0, so the
  zone kept cycling discovered -> touched x3 -> exhausted and never
  survived long enough to publish a TradePlan, despite price genuinely and
  repeatedly reacting at that level.

  Root cause: bucket size itself was `max(pip*10, atr*0.25, 1.0)`, and ATR
  is a live rolling M5 indicator that drifts every bar even when the zone's
  own coordinates haven't moved - atr=1.0 and atr=5.0 already produced
  different ids for an identical band. Real M5 ATR for XAU routinely
  fluctuates within a single-digit range intraday, so this fired
  constantly. ATR must be quantized before it can affect bucket size.
  """
  low, high = 4077.088124726152, 4081.535208607181
  ids = {
    confluence_zone_id(
      "XAU", "sell", low, high, ("key_level",), atr=atr, pip_size=0.1,
    )
    for atr in (0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 5.0, 5.9)
  }
  assert len(ids) == 1

  # Four independently-recorded detections of the same real band (from the
  # incident) must also collapse to one id regardless of their own minor
  # coordinate jitter, holding atr fixed at a typical M5 reading.
  bands = [
    (4077.1911656434213, 4081.4321676899117),
    (4076.843895009136, 4081.531819276578),
    (4076.9418949594537, 4081.43381932626),
    (4077.088124726152, 4081.535208607181),
  ]
  band_ids = {
    confluence_zone_id(
      "XAU", "sell", band_low, band_high, ("key_level",), atr=2.5,
      pip_size=0.1,
    )
    for band_low, band_high in bands
  }
  assert len(band_ids) == 1


def test_build_confluence_bands_bonus_scores_extra_techniques():
  member_score = 3.0
  two_technique = [
    TechniqueInstance(
      TECHNIQUE_SD, "buy", 4100.0, 4101.0, None, ("supply_demand",),
      measured={"score": member_score, "touches": 0},
      origin_index=1,
    ),
    TechniqueInstance(
      TECHNIQUE_OB, "buy", 4100.5, 4101.5, None, ("order_block",),
      measured={"score": member_score, "touches": 1},
      origin_index=2,
    ),
  ]
  five_technique = [
    *two_technique,
    TechniqueInstance(
      TECHNIQUE_FVG, "buy", 4100.6, 4101.4, None, ("bullish_fvg",),
      measured={"score": member_score, "touches": 0},
      origin_index=3,
    ),
    TechniqueInstance(
      TECHNIQUE_IFVG, "buy", 4100.7, 4101.3, None, ("ifvg",),
      measured={"score": member_score, "touches": 2},
      origin_index=4,
    ),
    TechniqueInstance(
      TECHNIQUE_CRT, "buy", 4100.8, 4101.2, None, ("crt",),
      measured={"score": member_score, "touches": 4},
      origin_index=5,
    ),
  ]
  bonus = 2.5
  two_band = build_confluence_bands(
    two_technique, symbol="XAU", atr=2.0, pip_size=0.1,
    min_overlap=0.5, confluence_bonus=bonus,
  )[0]
  five_band = build_confluence_bands(
    five_technique, symbol="XAU", atr=2.0, pip_size=0.1,
    min_overlap=0.5, confluence_bonus=bonus,
  )[0]

  assert two_band.score == pytest.approx(member_score + bonus * 1)
  assert five_band.score == pytest.approx(member_score + bonus * 4)
  assert five_band.score > two_band.score
  assert five_band.touches == 4
  assert two_band.touches == 1


def test_detector_settings_confluence_technique_bonus_score_default():
  from app.analysis.detectors import DetectorSettings

  settings = DetectorSettings()
  assert settings.confluence_technique_bonus_score == 2.5
