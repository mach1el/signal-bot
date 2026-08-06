"""Scalp zone access — inside or momentum chase within budget."""

from __future__ import annotations

import pytest

from app.autotrade.entry_activation import evaluate_entry_activation
from app.autotrade.execution_confirmation import scalp_zone_access
from app.analysis.entry_location import EntryLocationDecision


pytestmark = pytest.mark.no_database


def test_sell_scalp_chase_within_15_pips_past_zone_low():
  # SELL zone 4100–4102; ask/bid already 1.0 below low → 10p chase.
  access = scalp_zone_access(
    "SELL",
    bid=4099.0,
    ask=4099.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=15.0,
  )
  assert access.status == "chase"
  assert access.executable is True
  assert access.chase_pips == pytest.approx(10.0)


def test_sell_scalp_approach_below_zone_still_waits():
  # Price still approaching supply from below — not chase (past edge).
  # Wait: bid below low means past for SELL. Actually past = low - quote.
  # 4099 vs low 4100 → past=10p → chase. Approach wait needs quote ABOVE high?
  # For SELL approaching INTO zone from below: quote < low is past/chase
  # in trade direction. Approach for SELL from below into supply is quote < low
  # before touch — which IS the same as chase mathematically once past.
  # Real approach wait for SELL is quote > high (above supply, wrong side)
  # or... wait HFS SELL: distance = zone_low - executable, chase when > 0.
  # So quote below low = chase. Approach = still north of zone (inside or
  # quote > high waiting)? Range Edge SELL at supply: price rises into zone.
  # Approaching: quote < low (not yet in zone). That's our approach_wait when
  # past <= 0 — but past = low - quote is POSITIVE when quote < low.
  #
  # Re-read HFS: for SELL distance = zone_low - executable; if price is ABOVE
  # zone approaching, executable > high > low, distance = low - executable < 0
  # → approach wait. If price BELOW zone, distance > 0 → chase.
  # So approach for SELL is price still ABOVE the zone (hasn't entered/ripped).
  access = scalp_zone_access(
    "SELL",
    bid=4103.0,
    ask=4103.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=15.0,
  )
  assert access.status == "approach_wait"
  assert access.executable is False


def test_sell_scalp_chase_missed_beyond_15():
  access = scalp_zone_access(
    "SELL",
    bid=4097.0,  # 30 pips below low
    ask=4097.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=15.0,
  )
  assert access.status == "chase_missed"
  assert access.executable is False


def test_entry_activation_allows_range_edge_chase():
  from types import SimpleNamespace

  location = EntryLocationDecision(
    allowed=True,
    reason_code="entry_location_allowed",
    hard_block=False,
    archetype="range_reversion",
    would_block=False,
    measured={"mode": "enforce"},
  )
  cfg = SimpleNamespace(
    execution=SimpleNamespace(
      activation=SimpleNamespace(
        mode="enforce",
        reaction_trigger_maximum_age_bars=2,
      ),
    ),
  )
  decision = evaluate_entry_activation(
    strategy="Range Edge Scalp",
    direction="SELL",
    zone_entered_at=100,
    quote_inside=False,
    decisive_break=False,
    trigger=None,
    location_decision=location,
    now=160,
    cfg=cfg,
    chase_pips=10.0,
    maximum_chase_pips=15.0,
  )
  assert decision.measured.get("chase_entry") is True
  assert decision.reason_code != "quote_outside_zone"
  # Missing M1 trigger may still wait — but never die on quote_outside.
