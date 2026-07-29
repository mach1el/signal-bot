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
from app.autotrade.structural_target_room import evaluate_structural_target_room
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
) -> SimpleNamespace:
  return SimpleNamespace(
    scanner_conflict_overlap=0.5,
    scanner_conflict_margin=1.0,
    auto_trade_allow_counter_bias=True,
    auto_trade_structural_guard_mode=guard_mode,
    auto_trade_profile=profile,
    auto_trade_opposing_barrier_atr=0.5,
    scanner_actionability_gate_enabled=actionability_gate,
    key_level_role_ambiguity_gate_enabled=role_ambiguity_gate,
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
  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  decision = resolution.gated[0][1]
  assert decision.hard_block
  assert decision.reason_code == "opposing_major_no_room"
  assert decision.measured["planned_entry_price"] == pytest.approx(4045.95)
  assert decision.measured["opposing_low"] == pytest.approx(4046.0)
  assert decision.measured["entry_overlap_price"] == pytest.approx(0.73)


def test_target_room_is_static_block_even_when_legacy_gate_is_off():
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

  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "opposing_major_no_room"
  )
  assert decision.hard_block is True
  assert decision.allowed is False


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


def test_tier_c_is_rejected_by_typed_static_pre_gate():
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

  assert not eligible
  assert measured["static_rejection_reason"] == "tier_c_analysis_only"


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

  assert not eligible
  assert measured["reward_risk"] == 3.0
  assert measured["policy_reason_code"] == reason_code
  assert measured["policy_message"] == "entry zone exceeds policy width"
  assert measured["policy_hard_block"] is True


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
    cfg=_cfg(),
  )

  assert resolution.observed == (buy, sell)
  assert resolution.actionable == ()
  assert {item[1].reason_code for item in resolution.gated} == {
    "opposing_conflict_ambiguous",
  }
  assert resolution.conflicts[0]["outcome"] == "both_dropped"


def test_decisive_side_is_only_provisional_and_still_needs_room():
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
    cfg=_cfg(),
  )

  assert resolution.actionable == ()
  reasons = {decision.reason_code for _item, decision in resolution.gated}
  assert "opposing_conflict_weaker" in reasons
  assert "opposing_major_no_room" in reasons
  assert resolution.conflicts[0]["outcome"] == "stronger_kept"


def test_decisive_side_with_room_remains_actionable():
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
    cfg=_cfg(),
  )

  assert len(resolution.actionable) == 1
  assert resolution.actionable[0].direction == "BUY"
  assert resolution.actionable[0].target_cap_pips == pytest.approx(70)
  assert resolution.gated[0][1].reason_code == "opposing_conflict_weaker"


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


def test_role_ambiguity_is_static_block_even_when_legacy_gate_is_off():
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

  assert resolution.actionable == ()
  assert len(resolution.gated) == 1
  decision = next(
    decision for _item, decision in resolution.decisions
    if decision.reason_code == "key_level_role_ambiguous"
  )
  assert decision.hard_block is True
  assert decision.allowed is False


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
def test_hard_geometry_blocks_in_every_guard_mode(guard_mode):
  decision = classify_guard_severity(
    "opposing_barrier",
    "entry_inside_opposing_zone",
    "entry is contained",
    guard_mode=guard_mode,
    hard_geometry=True,
  )

  assert decision.hard_block
  assert decision.outcome == "block"


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
  assert strict.hard_block


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


def test_barrier_capped_target_is_used_by_reward_risk_pre_gate(monkeypatch):
  monkeypatch.setattr(scanner.settings, "auto_trade_tp_pips", "30,50,70")
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

  assert not eligible
  assert measured["configured_target_pips"] == [30, 50, 70]
  assert measured["effective_target_pips"] == pytest.approx(30)
  assert measured["opposing_low"] == pytest.approx(4105.0)
  assert measured["reward_risk"] < measured["min_reward_risk"]


def test_empty_market_map_is_valid_but_unavailable_map_fails_structural_closed():
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
    cfg=_cfg(),
  )

  assert len(available.actionable) == 1
  assert unavailable.actionable == ()
  assert unavailable.gated[0][1].reason_code == "opposing_context_unavailable"


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

  # Non-negotiable Telegram requirement: an opposing_major_no_room
  # observation has no executable StrategyMatch and must never reach
  # Telegram - not even as an ANALYSIS ONLY / MARKET OBSERVATION card.
  # (This incident used to still get one via the now-removed
  # `notification_results = digest or analysis_only_results` fallback,
  # which is why `sent` is now correctly empty rather than length 1.)
  assert sent == []
  notify.assert_not_awaited()
  assert await client.get(strategy_match_key("XAU")) is None
  assert await client.get(strategy_matches_key("XAU")) is None
  assert [key async for key in client.scan_iter("analysis:setup:*")] == []
  assert [
    key async for key in client.scan_iter("auto_trade:forming_message:*")
  ] == []
  assert [key async for key in client.scan_iter("execution:plan:*")] == []
  status = json.loads(await client.get("scanner:last_tick:XAU:M5"))
  assert status["observed_count"] == 1
  assert status["actionable_count"] == 0
  gate = status["actionability_gated"][0]
  assert gate["reason_code"] == "opposing_major_no_room"
  assert gate["measured"]["entry_overlap_price"] == pytest.approx(0.73)
  logs = await client.lrange(scanner._detect_log_key("XAU", "M5"), 0, 0)
  entry = json.loads(logs[0])["entries"][0]
  assert entry["outcome"] == "actionability_gated"
  assert entry["reason"] == "opposing_major_no_room"
