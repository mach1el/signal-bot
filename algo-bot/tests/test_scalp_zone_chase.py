"""Scalp zone access — inside or momentum chase within budget."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.autotrade.entry_activation import evaluate_entry_activation
from app.autotrade.execution_confirmation import (
  ZONE_ACCESS_MOMENTUM_CHASE,
  ZONE_ACCESS_RETEST_ONLY,
  scalp_zone_access,
)
from app.analysis.entry_location import EntryLocationDecision
from app.autotrade.zone_execution_cutover import _scalp_access
from app.autotrade.zone_watch import ZoneWatch


pytestmark = pytest.mark.no_database


def test_sell_scalp_chase_within_budget_past_zone_low():
  # SELL zone 4100–4102; ask/bid already 1.0 below low → 10p chase.
  access = scalp_zone_access(
    "SELL",
    bid=4099.0,
    ask=4099.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
  )
  assert access.status == "chase"
  assert access.executable is True
  assert access.chase_pips == pytest.approx(10.0)


def test_breakout_retest_sell_below_zone_waits_not_chases():
  """Live 2026-08-31 XAU: SELL retest must not market below the retest band."""
  access = scalp_zone_access(
    "SELL",
    bid=4429.62,
    ask=4429.72,
    zone_low=4430.76,
    zone_high=4433.36,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=40.0,
    zone_access_mode=ZONE_ACCESS_RETEST_ONLY,
  )
  assert access.status == "approach_wait"
  assert access.executable is False
  assert access.chase_pips == pytest.approx(11.4, abs=0.05)


def test_breakout_retest_sell_inside_zone_executable():
  access = scalp_zone_access(
    "SELL",
    bid=4431.0,
    ask=4431.1,
    zone_low=4430.76,
    zone_high=4433.36,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=40.0,
    zone_access_mode=ZONE_ACCESS_RETEST_ONLY,
  )
  assert access.status == "inside"
  assert access.executable is True


def test_breakout_retest_buy_above_zone_waits_not_chases():
  access = scalp_zone_access(
    "BUY",
    bid=4103.0,
    ask=4103.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
    zone_access_mode=ZONE_ACCESS_RETEST_ONLY,
  )
  assert access.status == "approach_wait"
  assert access.executable is False


def test_momentum_chase_mode_unchanged_for_range_sweep():
  access = scalp_zone_access(
    "SELL",
    bid=4099.0,
    ask=4099.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
    zone_access_mode=ZONE_ACCESS_MOMENTUM_CHASE,
  )
  assert access.status == "chase"
  assert access.executable is True


def test_sell_scalp_approach_above_zone_still_waits():
  # SELL supply: price still above the band (wrong side / not past) → wait.
  access = scalp_zone_access(
    "SELL",
    bid=4103.0,
    ask=4103.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
  )
  assert access.status == "approach_wait"
  assert access.executable is False


def test_sell_scalp_chase_missed_beyond_100():
  access = scalp_zone_access(
    "SELL",
    bid=4089.0,  # 110 pips below low
    ask=4089.1,
    zone_low=4100.0,
    zone_high=4102.0,
    tolerance=0.0,
    pip_size=0.1,
    maximum_chase_pips=100.0,
  )
  assert access.status == "chase_missed"
  assert access.executable is False


def test_entry_activation_allows_range_edge_chase():
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
    maximum_chase_pips=100.0,
  )
  assert decision.measured.get("chase_entry") is True
  assert decision.reason_code != "quote_outside_zone"
  # Missing M1 trigger may still wait — but never die on quote_outside.


def _zone_record(*, direction: str = "SELL") -> ZoneWatch:
  return ZoneWatch(
    version=1,
    zone_id="z1",
    symbol="XAU",
    direction=direction,
    low=4100.0,
    high=4102.0,
    width=2.0,
    source_timeframe="M5",
    structural_sources=("FVG",),
    confluence_tags=(),
    grade="A",
    score=1.0,
    freshness=0,
    touch_count=0,
    discovered_at=1,
    last_confirmed_at=1,
    last_touch_at=None,
    invalidation_price=None,
    state="watching_retest",
    market_map_id="",
    structure_signature="sig",
    updated_at=1,
    technique_tags=("fvg",),
  )


def test_technique_scalp_access_uses_execution_chase_budget(monkeypatch):
  from app.autotrade import zone_execution_cutover as cutover
  from app.autotrade.execution_confirmation import ScalpZoneAccess, ExecutableZoneEvidence

  monkeypatch.setattr(cutover, "_technique_chase_pips", lambda: 40.0)
  captured: dict = {}

  def _fake_scalp(*args, **kwargs):
    captured["kwargs"] = kwargs
    evidence = ExecutableZoneEvidence(
      executable_quote=4098.0,
      quote_side="ask",
      inside=False,
      distance_to_zone=2.0,
      distance_pips=20.0,
      tolerance_price=0.0,
    )
    return ScalpZoneAccess(evidence, "chase", 20.0, float(kwargs["maximum_chase_pips"]))

  monkeypatch.setattr(cutover, "scalp_zone_access", _fake_scalp)
  access = _scalp_access(
    _zone_record(),
    (4098.0, 4098.1, 100),
    strategy="FVG",
  )
  assert captured["kwargs"]["maximum_chase_pips"] == pytest.approx(40.0)
  assert access.status == "chase"
  assert access.executable is True
  assert access.maximum_chase_pips == pytest.approx(40.0)


def test_confluence_scalp_access_uses_execution_chase_budget(monkeypatch):
  from app.autotrade import zone_execution_cutover as cutover
  from app.autotrade.execution_confirmation import ScalpZoneAccess, ExecutableZoneEvidence

  monkeypatch.setattr(cutover, "_technique_chase_pips", lambda: 40.0)
  captured: dict = {}

  def _fake_scalp(*args, **kwargs):
    captured["kwargs"] = kwargs
    evidence = ExecutableZoneEvidence(
      executable_quote=4095.0,
      quote_side="ask",
      inside=False,
      distance_to_zone=5.0,
      distance_pips=50.0,
      tolerance_price=0.0,
    )
    return ScalpZoneAccess(
      evidence, "chase_missed", 50.0, float(kwargs["maximum_chase_pips"]),
    )

  monkeypatch.setattr(cutover, "scalp_zone_access", _fake_scalp)
  access = _scalp_access(
    _zone_record(),
    (4095.0, 4095.1, 100),
    strategy="Confluence Zone",
  )
  assert captured["kwargs"]["maximum_chase_pips"] == pytest.approx(40.0)
  assert access.status == "chase_missed"
  assert access.executable is False


def test_zone_reaction_scalp_access_stays_strict_inside(monkeypatch):
  from app.autotrade import zone_execution_cutover as cutover

  called = {"scalp": False}

  def _fake_scalp(*args, **kwargs):
    called["scalp"] = True
    raise AssertionError("Zone Reaction must not use scalp_zone_access chase")

  monkeypatch.setattr(cutover, "scalp_zone_access", _fake_scalp)
  access = _scalp_access(
    _zone_record(),
    (4098.0, 4098.1, 100),
    strategy="Zone Reaction",
  )
  assert called["scalp"] is False
  assert access.maximum_chase_pips == pytest.approx(0.0)
  assert access.executable is False
  assert access.status == "approach_wait"


def test_technique_chase_pips_helper_reads_execution_budget(monkeypatch):
  from app.autotrade import zone_execution_cutover as cutover

  monkeypatch.setattr(
    cutover,
    "runtime_config",
    SimpleNamespace(
      execution=SimpleNamespace(
        entry=SimpleNamespace(maximum_chase_distance_pips=40.0),
      ),
    ),
  )
  assert cutover._technique_chase_pips() == pytest.approx(40.0)
