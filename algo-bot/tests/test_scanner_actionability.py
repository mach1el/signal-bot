"""Cross-side scanner actionability and structural target-room regressions."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.analysis.actionability import resolve_actionability
from app.analysis import scanner
from app.analysis.detectors import DetectionResult
from app.analysis.key_level_role import (
  ROLE_AMBIGUOUS,
  ROLE_BROKEN_RESISTANCE,
  ROLE_RESISTANCE,
  ROLE_SUPPORT,
  classify_key_level_role,
)
from app.analysis.market_map import MapEntry, MarketMap
from app.analysis.types import Zone
from app.autotrade.execution_policy import (
  ExecutionPolicy,
  ExecutionPolicyEvaluation,
  classify_guard_severity,
)
from app.autotrade.structural_target_room import (
  evaluate_structural_target_room,
  filter_displaced_opposing_entries,
)
from app.autotrade.strategy_match import (
  strategy_match_key,
)
from app.autotrade.multi_match import strategy_matches_key
from app.persistence import redis_state


pytestmark = pytest.mark.no_database


def _result(
  direction: str,
  low: float,
  high: float,
  *,
  setup: str | None = None,
  quality: int = 3,
  current_price: float | None = None,
  structural_id: str | None = None,
  role: str | None = None,
) -> DetectionResult:
  side = "demand" if direction == "BUY" else "supply"
  return DetectionResult(
    setup=setup or (
      "Demand Zone Reaction" if direction == "BUY"
      else "Supply Zone Reaction"
    ),
    direction=direction,
    key_level=(low + high) / 2,
    entry_zone=Zone(low, high, side, score=float(quality)),
    current_price=(
      (low + high) / 2 if current_price is None else current_price
    ),
    confluence=quality,
    reasons=["fixture reaction"],
    mode="counter_bias" if direction == "BUY" else "with_bias",
    structural_source=(
      "key_level" if setup == "Key Level Reaction" else "supply_demand"
    ),
    structural_id=structural_id or f"{direction}:{low}:{high}",
    structural_low=low,
    structural_high=high,
    structural_timeframe="M5",
    structural_kind=(
      "round" if setup == "Key Level Reaction" else side
    ),
    key_level_role=role,
    planned_entry_price=(
      (low + high) / 2 if current_price is None else current_price
    ),
    provisional_targets_pips=(30, 50, 70),
  )


def _entry(
  side: str,
  low: float,
  high: float,
  *,
  tier: str = "major",
  contains_price: bool = False,
  tags: tuple[str, ...] = (),
) -> MapEntry:
  return MapEntry(
    side,
    low,
    high,
    round(low),
    round(high),
    tier,
    list(tags or (side,)),
    10.0,
    contains_price=contains_price,
  )


def _map(
  *entries: MapEntry,
  price: float = 4100.0,
) -> MarketMap:
  return MarketMap(
    entries=list(entries),
    price=price,
    eq=None,
    box_low=None,
    box_high=None,
    bias="down",
    bias_tf="H1",
    actionable_entries=list(entries),
  )


def _cfg(
  *,
  guard_mode: str = "balanced",
  profile: str = "conservative",
  actionability_gate: bool = True,
  role_ambiguity_gate: bool = True,
  allow_counter_bias: bool = True,
  displacement_lookback_bars: int = 0,
) -> SimpleNamespace:
  return SimpleNamespace(
    contested_corridor_gap_atr=0.5,
    auto_trade_allow_counter_bias=allow_counter_bias,
    auto_trade_structural_guard_mode=guard_mode,
    auto_trade_profile=profile,
    auto_trade_opposing_barrier_atr=0.5,
    scanner_actionability_gate_enabled=actionability_gate,
    key_level_role_ambiguity_gate_enabled=role_ambiguity_gate,
    auto_trade_displacement_override_lookback_bars=displacement_lookback_bars,
  )


@pytest.mark.parametrize("profile", ["conservative", "demo_eval"])
@pytest.mark.parametrize("guard_mode", ["observe", "balanced", "strict"])
def test_buy_under_overlapping_sell_major_is_observation_only(
  profile,
  guard_mode,
):
  buy = _result(
    "BUY",
    4041.67,
    4046.73,
    setup="Key Level Reaction",
    quality=3,
    current_price=4045.95,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    _entry("buy", 4042.0, 4051.0, tier="major", contains_price=True),
    price=4045.95,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(guard_mode=guard_mode, profile=profile),
  )

  assert resolution.observed == (buy,)
  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_major_no_room"
  )
  assert decision.hard_block is False
  assert decision.allowed is True
  assert decision.measured["planned_entry_price"] == pytest.approx(4045.95)
  assert decision.measured["opposing_low"] == pytest.approx(4046.0)
  # 2026-07-31: the candidate zone is now trimmed to its non-overlapping
  # portion before the room check runs (see
  # _trim_zone_against_overlapping_barrier), so the overlap of the
  # *adjusted* zone is genuinely zero - the major-tier zero-room rule is
  # preference telemetry, not a hard reject.
  assert decision.measured["entry_overlap_price"] == pytest.approx(0.0)


def test_target_room_is_observation_when_actionability_gate_is_off():
  buy = _result(
    "BUY",
    4041.67,
    4046.73,
    quality=3,
    current_price=4045.95,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    price=4045.95,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_major_no_room"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_target_room_is_telemetry_when_actionability_gate_is_on():
  buy = _result(
    "BUY",
    4041.67,
    4046.73,
    quality=3,
    current_price=4045.95,
  )
  market_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    price=4045.95,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_major_no_room"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_partial_overlap_trims_the_zone_instead_of_killing_the_whole_setup():
  """2026-07-30 incident: a BUY entry zone overlapped a (non-major)
  opposing SELL zone by a small sliver at its far edge, and the whole
  setup got hard-rejected even though 75% of the zone - including the
  actual planned entry point - was clean, untouched room. The overlap
  check compares raw zone bounds, not the planned entry price, so a
  zone-level sliver used to kill a trade whose entry was never really in
  danger. Trimming the zone to its own non-overlapping portion first
  lets the room check evaluate the real, now non-overlapping geometry.
  """
  buy = _result(
    "BUY",
    4100.0,
    4108.0,
    quality=3,
    current_price=4102.0,
  )
  market_map = _map(
    _entry("sell", 4106.0, 4112.0, tier="zone"),
    price=4102.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.gated == ()
  assert len(resolution.actionable) == 1
  trimmed = resolution.actionable[0]
  # Zone trimmed from 4100-4108 down to 4100-4106 (the opposing zone's
  # near edge) - the overlapping top 2.0 is gone, the clean bottom 6.0
  # remains.
  assert trimmed.entry_zone.low == pytest.approx(4100.0)
  assert trimmed.entry_zone.high == pytest.approx(4106.0)
  assert trimmed.target_cap_pips == pytest.approx(30.0)


def test_full_overlap_still_rejects_nothing_left_to_trim_into():
  """A candidate zone entirely consumed by an opposing zone has no clean
  portion to trim into. Overlap containment is preference telemetry — the
  setup remains actionable with the observed decision retained.
  """
  buy = _result(
    "BUY",
    4106.5,
    4107.5,
    quality=3,
    current_price=4107.0,
  )
  market_map = _map(
    _entry("sell", 4100.0, 4112.0, tier="zone"),
    price=4107.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_entry_contained"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_room_below_the_ladder_but_above_the_floor_is_capped_not_rejected():
  """2026-07-30/31: three real setups the same evening (15.18/15.2/19.9
  pips of real buffered room) were all hard-rejected for falling short of
  the smallest *configured* target (30 pips), even though genuine,
  positive room existed. min_capped_target_pips (15) is a floor
  independent of the configured ladder - room below the ladder but at or
  above this floor is now allowed with its own real room as the target.
  """
  buy = _result(
    "BUY",
    4103.0,
    4107.0,
    quality=3,
    current_price=4107.0,
  )
  market_map = _map(
    _entry("sell", 4110.0, 4118.0, tier="major"),
    price=4107.0,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert resolution.gated == ()
  assert len(resolution.actionable) == 1
  capped = resolution.actionable[0]
  # raw_room = 4110.0 - 4107.0 = 3.0; buffer = 0.5*2.0 = 1.0; buffered =
  # 2.0 price units = 20 pips - below the 30-pip floor of the configured
  # ladder [30, 50, 70] but above the 15-pip minimum-viability floor.
  assert capped.target_cap_pips == pytest.approx(20.0)


def test_room_below_the_floor_still_rejects():
  """Room below the viability floor remains a preference observation —
  telemetry only, setup stays actionable.
  """
  buy = _result(
    "BUY",
    4103.0,
    4108.7,
    quality=3,
    current_price=4108.7,
  )
  market_map = _map(
    _entry("sell", 4110.0, 4118.0, tier="major"),
    price=4108.7,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_barrier_no_target"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_counter_bias_reaction_is_observed_when_disabled():
  # Counter-bias disabled is a contextual observation. With the actionability
  # gate on, only documented hard conflicts (geometry / executable overlap /
  # insufficient target room) hard-block; HTF disagreement stays telemetry.
  buy = _result("BUY", 4100.0, 4101.0, quality=3, current_price=4100.5)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=_map(price=4100.5),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(allow_counter_bias=False, actionability_gate=True),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "counter_bias_disabled"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_with_bias_reaction_is_unaffected_by_counter_bias_disabled():
  sell = _result("SELL", 4100.0, 4101.0, quality=3, current_price=4100.5)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[sell],
    market_map=_map(price=4100.5),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(allow_counter_bias=False),
  )

  assert not any(
    decision.reason_code == "counter_bias_disabled"
    for _item, decision in resolution.decisions
  )
  assert resolution.actionable == (sell,)


def test_invalid_geometry_is_always_analysis_only():
  result = _result("BUY", 4100.0, 4101.0)
  result = replace(
    result,
    entry_zone=replace(result.entry_zone, top=float("nan")),
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    market_map=_map(price=4100.5),
    context=SimpleNamespace(htf_bias="up"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False, role_ambiguity_gate=False),
  )

  assert resolution.actionable == ()
  assert resolution.gated[0][1].reason_code == "invalid_geometry"
  assert resolution.gated[0][1].hard_block


def test_tier_c_is_allowed_as_preference_telemetry_by_static_pre_gate():
  result = _result(
    "SELL",
    4100.0,
    4102.0,
    quality=0,
    current_price=4101.0,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={"M5": SimpleNamespace(scalp_range=None)},
    regime=SimpleNamespace(kind="trend"),
  )

  eligible, measured = scanner._reward_risk_pre_gate(
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    result,
  )

  # Tier C builds a match with reduced risk — scanner pre-gate is telemetry.
  assert eligible is True
  assert measured.get("preference_telemetry") is True
  assert measured.get("policy_hard_block") is False


@pytest.mark.parametrize("reason_code", [
  "policy_zone_too_wide",
  "entry_inside_opposing_zone",
  "policy_regime_not_permitted",
  "protective_stop_unavailable",
])
def test_static_pre_gate_honors_full_policy_denial_with_sufficient_rr(
  monkeypatch,
  reason_code,
):
  result = _result(
    "BUY",
    4098.0,
    4100.0,
    quality=3,
    current_price=4099.5,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    frames={"M5": pd.DataFrame({"close": [4099.5]})},
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={"M5": SimpleNamespace(scalp_range=None)},
    regime=SimpleNamespace(kind="trend"),
  )
  policy = ExecutionPolicy(
    family="supply_demand",
    min_confluence=2,
    max_entry_drift_atr=1.0,
    max_entry_drift_pips=20.0,
    max_zone_width_atr=1.0,
    min_target_room_atr=0.5,
    min_reward_risk=1.0,
    risk_multiplier=1.0,
    order_type_preference="either",
    permitted_regimes=("trend",),
  )
  monkeypatch.setattr(
    scanner,
    "evaluate_execution_policy",
    lambda *_args, **_kwargs: ExecutionPolicyEvaluation(
      allowed=False,
      reason_code=reason_code,
      message="entry zone exceeds policy width",
      terminal=True,
      measured={
        "reward_risk": 3.0,
        "min_reward_risk": 1.0,
        "planned_stop_price": 4093.5,
        "zone_width": 6.5,
      },
      policy=policy,
    ),
  )

  eligible, measured = scanner._reward_risk_pre_gate(
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    result,
  )

  assert eligible is True
  assert measured["reward_risk"] == 3.0
  assert measured["policy_reason_code"] == reason_code
  assert measured["policy_message"] == "entry zone exceeds policy width"
  assert measured["policy_hard_block"] is False
  assert measured.get("preference_telemetry") is True


def test_equal_opposing_observations_remain_raw_but_both_are_not_actionable():
  buy = _result("BUY", 4100.0, 4101.0, quality=3)
  sell = _result("SELL", 4100.0, 4101.0, quality=3)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    market_map=_map(price=4100.5),
    context=SimpleNamespace(htf_bias="range"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert resolution.observed == (buy, sell)
  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert len(contested) == 2
  assert all(decision.hard_block is False for decision in contested)
  assert all(decision.allowed is True for decision in contested)
  assert resolution.conflicts[0]["outcome"] == "contested_corridor"
  assert contested[0].measured["executable_conflict"] is True


def test_confluence_margin_never_picks_a_side_out_of_a_contested_corridor():
  """Executable overlap keeps both sides actionable with contested telemetry."""
  buy = _result("BUY", 4100.0, 4101.0, quality=4)
  sell = _result("SELL", 4100.0, 4101.0, quality=2)
  no_room = _map(
    _entry("sell", 4100.8, 4103.0, tier="major"),
    price=4100.5,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    market_map=no_room,
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  reasons = {
    decision.reason_code for _item, decision in resolution.decisions
  }
  assert "contested_corridor" in reasons
  assert resolution.conflicts[0]["outcome"] == "contested_corridor"


def test_distant_map_room_does_not_rescue_an_overlapping_pair():
  """A shared executable quote inside both bands is still contested."""
  buy = _result("BUY", 4100.0, 4101.0, quality=4)
  sell = _result("SELL", 4100.0, 4101.0, quality=2)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    market_map=_map(
      _entry("sell", 4120.0, 4123.0, tier="major"),
      price=4100.5,
    ),
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert contested
  assert all(decision.hard_block is False for decision in contested)


def test_nearby_non_overlapping_bands_remain_watched():
  """Proximity alone must not kill both sides — retain nearby opposing bands."""
  buy = _result("BUY", 4001.0, 4007.0, quality=3, current_price=4004.0)
  sell = _result("SELL", 4007.0, 4019.0, quality=2, current_price=4013.0)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    market_map=_map(price=4005.0),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  assert resolution.conflicts == ()
  reasons = {decision.reason_code for _item, decision in resolution.decisions}
  assert "contested_corridor" not in reasons
  assert "nearby_opposing_structure" in reasons


def test_bands_well_separated_beyond_the_gap_threshold_are_not_contested():
  buy = _result("BUY", 4001.0, 4003.0, quality=3)
  sell = _result("SELL", 4050.0, 4052.0, quality=2)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    market_map=_map(price=4002.0),
    context=SimpleNamespace(htf_bias="down"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  reasons = {decision.reason_code for _item, decision in resolution.gated}
  assert "contested_corridor" not in reasons


def test_proposed_entry_inside_opposing_band_is_executable_conflict():
  buy = _result("BUY", 4100.0, 4105.0, quality=3, current_price=4099.0)
  sell = _result("SELL", 4102.0, 4108.0, quality=3, current_price=4106.0)
  buy = replace(buy, planned_entry_price=4103.0)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    market_map=_map(price=4099.0),
    context=SimpleNamespace(htf_bias="range"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert len(contested) == 2
  assert all(decision.hard_block is False for decision in contested)


@pytest.mark.parametrize(
  ("direction", "entry_low", "entry_high", "barrier"),
  [
    ("BUY", 4100.0, 4101.0, _entry("sell", 4120.0, 4123.0)),
    ("SELL", 4120.0, 4121.0, _entry("buy", 4098.0, 4101.0)),
  ],
)
def test_valid_direction_with_structural_room_remains_actionable(
  direction,
  entry_low,
  entry_high,
  barrier,
):
  result = _result(direction, entry_low, entry_high, quality=3)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    market_map=_map(barrier, price=result.current_price),
    context=SimpleNamespace(
      htf_bias="up" if direction == "BUY" else "down",
    ),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  assert resolution.actionable[0].target_cap_pips == pytest.approx(70)


def test_generic_key_level_without_acceptance_is_ambiguous():
  frame = pd.DataFrame(
    {
      "open": [100.0, 100.2, 99.9],
      "high": [100.4, 100.5, 100.3],
      "low": [99.7, 99.8, 99.6],
      "close": [100.1, 99.9, 100.0],
    },
    index=pd.date_range("2026-07-28", periods=3, freq="5min", tz="UTC"),
  )

  decision = classify_key_level_role(
    kind="round",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=frame,
    breakout_accept_bars=2,
  )

  assert decision.role == ROLE_AMBIGUOUS


def test_role_ambiguity_is_telemetry_only_never_a_hard_block():
  """P0 zone/M1 simplification: key_level_reaction() (detectors.py) no
  longer emits a genuinely-undecided ambiguous-role result - it
  deterministically resolves to exactly one direction before this point,
  or discards the level entirely. Hard-blocking "ambiguous role" here
  duplicated a decision already made upstream and rejected every one of
  those decisions regardless of how they were actually resolved. Now
  recorded for telemetry only, never gated - and never actionable-
  blocking regardless of the (already dead)
  key_level_role_ambiguity_gate_enabled flag.
  """
  result = _result(
    "BUY",
    99.5,
    100.5,
    setup="Key Level Reaction",
    role=ROLE_AMBIGUOUS,
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    market_map=_map(price=100.0),
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False, role_ambiguity_gate=False),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "key_level_role_ambiguous"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_key_level_explicit_role_and_accepted_break_are_deterministic():
  inside = pd.DataFrame(
    {"close": [100.0, 100.1]},
    index=pd.date_range("2026-07-28", periods=2, freq="5min", tz="UTC"),
  )
  accepted = pd.DataFrame(
    {"close": [101.0, 101.2]},
    index=pd.date_range("2026-07-28", periods=2, freq="5min", tz="UTC"),
  )

  assert classify_key_level_role(
    kind="support",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=inside,
    breakout_accept_bars=2,
  ).role == ROLE_SUPPORT
  assert classify_key_level_role(
    kind="resistance",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=inside,
    breakout_accept_bars=2,
  ).role == ROLE_RESISTANCE
  assert classify_key_level_role(
    kind="resistance",
    level_price=100.0,
    band_low=99.5,
    band_high=100.5,
    closed_bars=accepted,
    breakout_accept_bars=2,
  ).role == ROLE_BROKEN_RESISTANCE


@pytest.mark.parametrize("guard_mode", ["observe", "balanced", "strict"])
def test_preference_geometry_is_telemetry_in_every_guard_mode(guard_mode):
  decision = classify_guard_severity(
    "opposing_barrier",
    "entry_inside_opposing_zone",
    "entry is contained",
    guard_mode=guard_mode,
    hard_geometry=True,
  )

  assert decision.hard_block is False
  assert decision.outcome == "allow_with_warning"


def test_soft_geometry_remains_mode_aware():
  observed = classify_guard_severity(
    "opposing_barrier",
    "opposing_ahead",
    "barrier ahead",
    guard_mode="observe",
    hard_geometry=False,
  )
  balanced = classify_guard_severity(
    "opposing_barrier",
    "opposing_ahead",
    "barrier ahead",
    guard_mode="balanced",
    hard_geometry=False,
  )
  strict = classify_guard_severity(
    "opposing_barrier",
    "opposing_ahead",
    "barrier ahead",
    guard_mode="strict",
    hard_geometry=False,
  )

  assert observed.outcome == "allow_with_warning"
  assert balanced.outcome == "allow_with_warning"
  # Preferenced opposing-barrier proximity remains telemetry even in strict.
  assert strict.hard_block is False
  assert strict.outcome == "allow_with_warning"


def test_structural_target_room_caps_configured_ladder():
  decision = evaluate_structural_target_room(
    direction="BUY",
    planned_entry_price=4100.0,
    candidate_entry_low=4099.0,
    candidate_entry_high=4100.5,
    configured_target_pips=(30, 50, 70),
    actionable_entries=(
      _entry("sell", 4106.0, 4108.0, tier="zone"),
    ),
    atr=1.0,
    pip_size=0.1,
    barrier_buffer_atr=0.5,
  )

  assert decision.allowed
  assert decision.effective_target_pips == pytest.approx(50)
  assert decision.fitted_targets_pips == (30, 50)
  assert decision.measured["configured_target_pips"] == [30, 50, 70]
  assert decision.measured["effective_target_pips"] == pytest.approx(50)


def test_filter_displaced_opposing_entries_drops_a_closed_through_barrier():
  sell_barrier = _entry("sell", 4095.67, 4106.28, tier="major")
  buy_barrier = _entry("buy", 4001.0, 4007.0, tier="major")
  kept = filter_displaced_opposing_entries(
    [sell_barrier, buy_barrier],
    direction="BUY",
    recent_closes=[4098.0, 4102.0, 4108.5],
  )
  # The BUY-side barrier is never "opposing" for a BUY candidate in the
  # first place - kept regardless. The SELL barrier is dropped because one
  # of the recent closes (4108.5) closed decisively above its high (4106.28).
  assert kept == [buy_barrier]


def test_filter_displaced_opposing_entries_keeps_an_untested_barrier():
  sell_barrier = _entry("sell", 4095.67, 4106.28, tier="major")
  kept = filter_displaced_opposing_entries(
    [sell_barrier],
    direction="BUY",
    recent_closes=[4098.0, 4100.0, 4103.5],
  )
  assert kept == [sell_barrier]


def test_filter_displaced_opposing_entries_requires_a_close_not_a_wick():
  # A high approaching or even piercing the barrier intrabar does not count
  # - only a genuine closed-bar break does (recent_closes are closes only).
  sell_barrier = _entry("sell", 4095.67, 4106.28, tier="major")
  kept = filter_displaced_opposing_entries(
    [sell_barrier],
    direction="BUY",
    recent_closes=[4106.28, 4106.0, 4105.9],
  )
  assert kept == [sell_barrier]


def test_barrier_capped_target_is_used_by_reward_risk_pre_gate(monkeypatch):
  monkeypatch.setattr(scanner.settings, "auto_trade_tp_pips", "30,50,70")
  # This fixture's _result() helper labels every BUY "counter_bias"
  # regardless of the ctx.htf_bias passed in below (a fixture quirk, not
  # something this test is exercising) - this test is about barrier-capped
  # target room, not counter-bias policy, so keep it enabled here.
  monkeypatch.setattr(scanner.settings, "auto_trade_allow_counter_bias", True)
  result = _result(
    "BUY",
    4095.0,
    4101.0,
    quality=3,
    current_price=4100.5,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="up",
    frames={"M5": pd.DataFrame({"close": [4100.5]})},
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={"M5": SimpleNamespace(scalp_range=None)},
    regime=SimpleNamespace(kind="trend"),
  )
  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    market_map=_map(
      _entry("sell", 4105.0, 4108.0, tier="zone"),
      price=4100.5,
    ),
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=scanner.settings,
  )

  assert len(resolution.actionable) == 1
  capped = resolution.actionable[0]
  assert capped.target_cap_pips == pytest.approx(30)
  eligible, measured = scanner._reward_risk_pre_gate(
    "XAU",
    "M5",
    "2026-07-28T12:10:00+00:00",
    ctx,
    capped,
  )

  # Scanner RR pre-gate is non-blocking telemetry; barrier-capped RR is retained.
  assert eligible is True
  assert measured.get("preference_telemetry") is True
  assert measured.get("policy_hard_block") is False
  assert measured["configured_target_pips"] == [30, 50, 70]
  assert measured["effective_target_pips"] == pytest.approx(30)
  assert measured["opposing_low"] == pytest.approx(4105.0)
  assert measured["reward_risk"] < measured["min_reward_risk"]


def test_recent_displacement_beyond_barrier_lets_a_contained_buy_through():
  """Live incident: a BUY at XAU ~4099 got hard-blocked by opposing_entry_
  contained against a major opposing zone (4095.67-4106.28) - but recent M5
  closes had already closed decisively above that zone's own high, meaning
  the barrier was functionally already broken by live price while its own
  (HTF-sourced) classification hadn't caught up. The candidate must not be
  blocked on a barrier price has already displaced beyond.
  """
  buy = _result(
    "BUY",
    4093.95,
    4101.61,
    quality=3,
    current_price=4099.41,
  )
  market_map = _map(
    _entry(
      "sell", 4095.67, 4106.28, tier="major",
      tags=("OB", "breaker", "flip", "supply"),
    ),
    price=4099.41,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": pd.DataFrame({
      "close": [4098.0, 4102.0, 4108.5],
    })},
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(displacement_lookback_bars=3),
  )

  assert resolution.gated == ()
  assert len(resolution.actionable) == 1


def test_no_displacement_still_blocks_as_before():
  """Same setup as above, but the recent closes never actually close beyond
  the barrier (still a wick/approach, not a confirmed break). Containment
  remains preference telemetry — actionable with the observed decision.
  """
  buy = _result(
    "BUY",
    4093.95,
    4101.61,
    quality=3,
    current_price=4099.41,
  )
  market_map = _map(
    _entry(
      "sell", 4095.67, 4106.28, tier="major",
      tags=("OB", "breaker", "flip", "supply"),
    ),
    price=4099.41,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": pd.DataFrame({
      "close": [4098.0, 4100.0, 4103.5],
    })},
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(displacement_lookback_bars=3),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_entry_contained"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_displacement_override_disabled_by_default_lookback_zero():
  """With lookback 0 the displacement override never fires; containment is
  still preference telemetry (actionable + observed decision).
  """
  buy = _result(
    "BUY",
    4093.95,
    4101.61,
    quality=3,
    current_price=4099.41,
  )
  market_map = _map(
    _entry("sell", 4095.67, 4106.28, tier="major"),
    price=4099.41,
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": pd.DataFrame({"close": [4098.0, 4102.0, 4108.5]})},
  )

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy],
    market_map=market_map,
    context=ctx,
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(),
  )

  assert len(resolution.actionable) == 1
  assert resolution.gated == ()
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_entry_contained"
  )
  assert decision.hard_block is False
  assert decision.allowed is True


def test_empty_market_map_is_valid_and_unavailable_map_retains_candidate():
  result = _result("BUY", 4100.0, 4101.0)
  available = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    market_map=_map(price=4100.5),
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(),
  )
  unavailable = resolve_actionability(
    symbol="XAU",
    observed_results=[result],
    market_map=None,
    context=SimpleNamespace(htf_bias="up"),
    atr=1.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=True),
  )

  assert len(available.actionable) == 1
  assert len(unavailable.actionable) == 1
  assert unavailable.gated == ()
  decision = next(
    decision for _item, decision in unavailable.decisions
    if decision.reason_code == "context_degraded"
  )
  assert decision.hard_block is False
  assert decision.measured["market_map_available"] is False
  assert decision.measured["context_degraded"] is True
  assert (
    decision.measured["context_degraded_reason"]
    == "opposing_context_unavailable"
  )


def test_gate_false_retains_contextual_observations_including_contested():
  buy = _result("BUY", 4100.0, 4101.0, quality=3)
  sell = _result("SELL", 4100.0, 4101.0, quality=3)

  resolution = resolve_actionability(
    symbol="XAU",
    observed_results=[buy, sell],
    market_map=_map(price=4100.5),
    context=SimpleNamespace(htf_bias="range"),
    atr=2.0,
    pip_size=0.1,
    cfg=_cfg(actionability_gate=False),
  )

  assert len(resolution.actionable) == 2
  assert resolution.gated == ()
  contested = [
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "contested_corridor"
  ]
  assert contested
  assert all(decision.hard_block is False for decision in contested)


@pytest.mark.asyncio
async def test_live_incident_never_reaches_lifecycle_card_or_strategy_match(
  monkeypatch,
):
  client = redis_state.get_client()
  frame = pd.DataFrame(
    {
      "open": [4044.8, 4045.1, 4045.4],
      "high": [4045.4, 4046.2, 4046.0],
      "low": [4043.8, 4044.0, 4044.7],
      "close": [4045.0, 4045.4, 4045.95],
      "volume": [100.0, 120.0, 110.0],
    },
    index=pd.date_range(
      "2026-07-28 12:00",
      periods=3,
      freq="5min",
      tz="UTC",
    ),
  )
  ctx = SimpleNamespace(
    tf="M5",
    htf_bias="down",
    frames={"M5": frame},
    indicators={"M5": SimpleNamespace(atr=pd.Series([2.0]))},
    structures={
      "M5": SimpleNamespace(
        scalp_range=None,
        scalp_barriers=[],
      )
    },
    regime=SimpleNamespace(kind="trend"),
    spot_price=4045.95,
    analysis=SimpleNamespace(per_tf={}),
    settings=scanner._detector_settings(),
  )
  incident = _result(
    "BUY",
    4041.67,
    4046.73,
    setup="Key Level Reaction",
    quality=3,
    current_price=4045.95,
  )
  incident = replace(
    incident,
    key_level=4044.20,
    confirmation="sweep_reclaim",
    confirmation_type="sweep_reclaim",
    source_touches=7,
    bias_relationship="counter_bias",
  )
  current_map = _map(
    _entry("sell", 4046.0, 4055.0, tier="major"),
    _entry("sell", 4060.0, 4063.0, tier="zone"),
    _entry("buy", 4042.0, 4051.0, tier="major", contains_price=True),
    price=4045.95,
  )
  monkeypatch.setattr(scanner.settings, "scanner_symbols", "XAU")
  monkeypatch.setattr(scanner.settings, "scanner_exec_tf", "M5")
  monkeypatch.setattr(scanner.settings, "scanner_htf", "H1,M15")
  monkeypatch.setattr(scanner.settings, "telegram_owner_id", 4242)
  monkeypatch.setattr(scanner.settings, "scanner_gate_max_source_touches", 0)
  monkeypatch.setattr(
    scanner.settings, "scanner_actionability_gate_enabled", True,
  )
  monkeypatch.setattr(
    scanner.settings, "key_level_role_ambiguity_gate_enabled", True,
  )
  monkeypatch.setattr(
    scanner,
    "_load_market_context_for_symbol",
    AsyncMock(return_value=(ctx, ctx.frames)),
  )
  monkeypatch.setattr(
    scanner,
    "build_map",
    lambda *_args, **_kwargs: current_map,
  )
  notify = AsyncMock()

  sent = await scanner._handle_event(
    "XAU:M5:2026-07-28T12:10:00+00:00",
    client=client,
    detectors=[lambda _ctx: incident],
    notify=notify,
  )

  # Preference telemetry: opposing_major_no_room keeps the setup actionable.
  # Telegram owner notify may still be suppressed by other handoff rules.
  assert len(sent) == 1
  notify.assert_not_awaited()
  status = json.loads(await client.get("scanner:last_tick:XAU:M5"))
  assert status["observed_count"] == 1
  assert status["actionable_count"] == 1
  gate = next(
    item for item in status["actionability_gated"]
    if item["reason_code"] == "opposing_major_no_room"
  )
  assert gate["hard_block"] is False
  assert gate["measured"]["entry_overlap_price"] == pytest.approx(0.0)
