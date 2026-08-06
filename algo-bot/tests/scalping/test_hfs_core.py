"""Scalping unit tests — context, microstructure, strategies, risk, paper."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from app.scalping.activation import evaluate_scalp_activation
from app.scalping.context import (
  build_scalp_context_snapshot,
  compute_context_id,
  is_context_fresh,
  permitted_archetypes_for_session,
)
from app.scalping.lifecycle import transition
from app.scalping.microstructure import (
  detect_breakout_retest,
  detect_impulse_pullback,
  detect_sweep_reclaim,
  build_micro_structure,
)
from app.scalping.models import (
  ARCHETYPE_RANGE_SWEEP,
  ARMED,
  DISCOVERED,
  EXECUTABLE,
  MISSED,
  OPPORTUNITY_VERSION,
  ScalpContextSnapshot,
  ScalpLifecycleRecord,
  ScalpOpportunity,
  CONTEXT_VERSION,
)
from app.scalping.ranking import rank_opportunities, score_opportunity
from app.scalping.replay import aggregate_report, evaluate_paper_outcome
from app.scalping.risk import ScalpRiskState, evaluate_risk, risk_fraction
from app.scalping.strategies import discover_range_sweep


pytestmark = pytest.mark.no_database


def _cfg(**overrides):
  hfs = SimpleNamespace(
    mode="shadow",
    archetypes=SimpleNamespace(
      range_sweep_enabled=True,
      impulse_pullback_enabled=True,
      breakout_retest_enabled=True,
    ),
    context=SimpleNamespace(
      maximum_m5_age_seconds=420,
      m1_lookback_bars=60,
      current_context_ttl_seconds=3600,
      historic_context_ttl_seconds=86400,
    ),
    location=SimpleNamespace(
      range_buy_maximum_position=0.35,
      range_sell_minimum_position=0.65,
      pullback_buy_maximum_position=0.75,
      pullback_sell_minimum_position=0.25,
    ),
    activation=SimpleNamespace(
      trigger_maximum_age_bars=2,
      maximum_chase_pips=5.0,
      rearm_distance_atr=0.25,
    ),
    target=SimpleNamespace(
      preferred_ladder_pips="20,25,30",
      minimum_net_target_pips=15.0,
    ),
    stop=SimpleNamespace(minimum_pips=12.0, maximum_pips=30.0, buffer_atr=0.1),
    policy=SimpleNamespace(
      minimum_reward_risk=1.10,
      maximum_opportunities_per_cycle=3,
      maximum_active_opportunities=10,
      maximum_spread_pips=5.0,
    ),
    risk=SimpleNamespace(
      mode="shadow",
      risk_fraction_per_trade=0.10,
      maximum_concurrent_positions=1,
      maximum_session_trades=12,
      maximum_daily_trades=30,
      maximum_consecutive_losses=3,
      cooldown_after_loss_minutes=5,
      daily_loss_limit_r=3.0,
      session_loss_limit_r=2.0,
    ),
  )
  for key, value in overrides.items():
    setattr(hfs, key, value)
  return SimpleNamespace(
    strategies=SimpleNamespace(high_frequency_scalp=hfs),
    market_data=SimpleNamespace(
      sessions=SimpleNamespace(
        asia_start=22, london_start=7, ny_start=13, daily_rollover_utc_hour=21,
      ),
    ),
  )


def _m5_range(low=4000.0, high=4100.0, bars=40, end_ts=1_780_000_000):
  rows = []
  index = []
  for i in range(bars):
    mid = (low + high) / 2
    rows.append({
      "open": mid, "high": high - 1, "low": low + 1, "close": mid, "volume": 1.0,
    })
    index.append(pd.Timestamp(end_ts - (bars - i) * 300, unit="s", tz="UTC"))
  return pd.DataFrame(rows, index=index)


def test_context_id_stable_for_same_structure():
  a = compute_context_id("XAU", 100, 4000.0, 4100.0, 4020.0, 4080.0, "range")
  b = compute_context_id("XAU", 100, 4000.0, 4100.0, 4020.0, 4080.0, "range")
  assert a == b


def test_non_xau_context_fails_closed():
  m5 = _m5_range()
  snap = build_scalp_context_snapshot(
    symbol="US30", m5=m5, m15=None, h1=None, price=4050.0,
    pip_size=0.1, atr=5.0, now=1_780_000_000, cfg=_cfg(),
  )
  assert snap is None


def test_context_freshness():
  m5 = _m5_range()
  snap = build_scalp_context_snapshot(
    symbol="XAU", m5=m5, m15=m5, h1=None, price=4050.0,
    pip_size=0.1, atr=5.0, now=int(m5.index[-1].timestamp()), cfg=_cfg(),
  )
  assert snap is not None
  assert is_context_fresh(snap, snap.m5_bar_ts + 100, 420)
  assert not is_context_fresh(snap, snap.m5_bar_ts + 1000, 420)


def test_asia_permits_only_range_sweep():
  assert permitted_archetypes_for_session("asia") == (ARCHETYPE_RANGE_SWEEP,)
  assert permitted_archetypes_for_session("rollover") == ()


def test_lower_edge_sweep_reclaim():
  idx = pd.date_range("2026-07-01 10:00", periods=3, freq="1min", tz="UTC")
  df = pd.DataFrame([
    {"open": 4010, "high": 4012, "low": 4008, "close": 4011, "volume": 1},
    {"open": 4011, "high": 4012, "low": 4007, "close": 4010, "volume": 1},
    {"open": 4009, "high": 4011, "low": 3998, "close": 4006, "volume": 1},
  ], index=idx)
  # close above edge 4000 after sweeping below
  df.iloc[-1, df.columns.get_loc("low")] = 3995.0
  df.iloc[-1, df.columns.get_loc("close")] = 4002.0
  df.iloc[-1, df.columns.get_loc("open")] = 3998.0
  hit = detect_sweep_reclaim(df, direction="BUY", edge_price=4000.0, tolerance=1.0)
  assert hit is not None
  assert hit["pattern"] == "sweep_reclaim"


def test_sweep_reclaim_lookback_picks_prior_bar():
  # Reclaim on bar -2; newest bar is noise. Activation allows age=2 bars so
  # discovery must recover the prior reclaim instead of reporting discovered=0.
  idx = pd.date_range("2026-07-01 10:00", periods=3, freq="1min", tz="UTC")
  df = pd.DataFrame([
    {"open": 4010, "high": 4012, "low": 4008, "close": 4011, "volume": 1},
    {"open": 3998, "high": 4005, "low": 3995, "close": 4002, "volume": 1},
    {"open": 4002, "high": 4004, "low": 4001, "close": 4003, "volume": 1},
  ], index=idx)
  miss = detect_sweep_reclaim(
    df, direction="BUY", edge_price=4000.0, tolerance=1.0, lookback_bars=1,
  )
  assert miss is None
  hit = detect_sweep_reclaim(
    df, direction="BUY", edge_price=4000.0, tolerance=1.0, lookback_bars=2,
  )
  assert hit is not None
  assert hit["bar_ts"] == int(idx[1].timestamp())


def test_shallow_pullback_blocks():
  idx = pd.date_range("2026-07-01 10:00", periods=20, freq="1min", tz="UTC")
  closes = list(range(4000, 4020))
  df = pd.DataFrame({
    "open": [c - 0.5 for c in closes],
    "high": [c + 1 for c in closes],
    "low": [c - 1 for c in closes],
    "close": closes,
    "volume": [1] * 20,
  }, index=idx)
  # tiny pullback from extreme
  df.iloc[-1, df.columns.get_loc("close")] = 4019.0
  df.iloc[-1, df.columns.get_loc("open")] = 4018.5
  result = detect_impulse_pullback(df, direction="BUY", min_retracement=0.25)
  assert result is not None
  assert result.get("rejected") is True
  assert result.get("reason") == "pullback_too_shallow"


def test_breakout_without_retest_waits():
  idx = pd.date_range("2026-07-01 10:00", periods=6, freq="1min", tz="UTC")
  df = pd.DataFrame({
    "open": [4050, 4051, 4052, 4055, 4060, 4062],
    "high": [4052, 4053, 4054, 4058, 4065, 4064],
    "low": [4048, 4049, 4050, 4053, 4059, 4060],
    "close": [4051, 4052, 4053, 4057, 4063, 4063],
    "volume": [1] * 6,
  }, index=idx)
  result = detect_breakout_retest(
    df, direction="BUY", box_high=4055.0, box_low=4040.0, min_displacement=1.0,
  )
  assert result is not None
  assert result.get("state") == "wait_retest"


def test_risk_daily_cap_and_no_martingale():
  cfg = _cfg()
  assert risk_fraction(cfg) == 0.10
  state = ScalpRiskState(daily_trades=30)
  decision = evaluate_risk(state, cfg, session="london", now=1_780_000_000)
  assert decision.allowed is False
  assert decision.reason_code == "scalp_daily_trade_cap"


def test_lifecycle_explicit_transitions():
  record = ScalpLifecycleRecord("oid", "eid", DISCOVERED, "ctx", 1)
  armed = transition(record, ARMED, reason="ok", now=2)
  assert armed.state == ARMED
  missed = transition(armed, MISSED, reason="chase", now=3)
  assert missed.state == MISSED


def test_paper_same_bar_conservative_stop():
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="o1",
    context_id="c1",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=1,
    source_bar_ts=1,
    zone_low=4000,
    zone_high=4002,
    key_level=4001,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=1,
    trigger_price=4001.0,
    invalidation_price=3995.0,
    expected_target_price=4020.0,
    expected_target_pips=20,
    expected_stop_pips=15,
    expected_reward_risk=1.3,
    location_position=0.2,
    score=1.0,
    reasons=("t",),
    expires_at=100,
  )
  bars = pd.DataFrame([{
    "open": 4001, "high": 4025, "low": 3990, "close": 4010, "volume": 1,
  }])
  outcome = evaluate_paper_outcome(opp, bars, pip_size=0.1)
  assert outcome.outcome == "stop"
  assert outcome.exit_reason == "same_bar_stop_priority"


def test_activation_blocks_buy_in_premium():
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="c",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="range",
    m5_structure="range",
    regime="range",
    dealing_range_low=4000.0,
    dealing_range_high=4100.0,
    dealing_range_position=0.8,
    active_range_low=4070.0,
    active_range_high=4090.0,
    active_range_eq=4080.0,
    nearest_support_low=4070.0,
    nearest_support_high=4072.0,
    nearest_resistance_low=4088.0,
    nearest_resistance_high=4090.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=5.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="o",
    context_id="c",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=100,
    zone_low=4078.0,
    zone_high=4082.0,
    key_level=4080.0,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=90,
    trigger_price=4080.0,
    invalidation_price=4070.0,
    expected_target_price=4100.0,
    expected_target_pips=20,
    expected_stop_pips=15,
    expected_reward_risk=1.3,
    location_position=0.8,
    score=1.0,
    reasons=("t",),
    expires_at=200,
  )
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4079.5,
    quote_ask=4080.5,
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert decision.allowed is False
  assert decision.hard_block is True


def test_ranking_prefers_higher_score():
  ctx = ScalpContextSnapshot(
    version=1, context_id="c", symbol="XAU", created_at=1,
    h1_bar_ts=None, m15_bar_ts=None, m5_bar_ts=1, htf_bias="up",
    m5_structure="bullish", regime="trend", dealing_range_low=4000,
    dealing_range_high=4100, dealing_range_position=0.2,
    active_range_low=4000, active_range_high=4050, active_range_eq=4025,
    nearest_support_low=4000, nearest_support_high=4002,
    nearest_resistance_low=4048, nearest_resistance_high=4050,
    buy_corridor_room_pips=30, sell_corridor_room_pips=30,
    session="london", permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,), atr=4,
  )
  from app.scalping.models import ScalpDecision, ScalpScore

  def make(score_hint, oid, pos):
    opp = ScalpOpportunity(
      version=1, opportunity_id=oid, context_id="c", symbol="XAU",
      archetype=ARCHETYPE_RANGE_SWEEP, direction="BUY", discovered_at=1,
      source_bar_ts=1, zone_low=4000, zone_high=4005, key_level=4002,
      trigger_type="sweep_reclaim", trigger_bar_ts=1, trigger_price=4002,
      invalidation_price=3990, expected_target_price=4025, expected_target_pips=25,
      expected_stop_pips=15, expected_reward_risk=1.5, location_position=pos,
      score=score_hint, reasons=("r",), expires_at=10,
    )
    decision = ScalpDecision(True, False, "ok", score_hint, {"spread_pips": 1.0, "location_position": pos})
    score = score_opportunity(opp, ctx, decision, spread_pips=1.0)
    return opp, decision, score

  ranked = rank_opportunities([make(0.5, "a", 0.4), make(0.9, "b", 0.1)], maximum=1)
  assert len(ranked) == 1
  assert ranked[0][0].opportunity_id == "b"


def test_aggregate_report_expectancy():
  rows = [
    {"outcome": "target", "net_r": 1.2, "session": "london", "archetype": "range_sweep"},
    {"outcome": "stop", "net_r": -1.0, "session": "london", "archetype": "range_sweep"},
  ]
  report = aggregate_report(rows)
  assert report["count"] == 2
  assert report["win_rate"] == 0.5
  assert report["expectancy_r"] == pytest.approx(0.1)


def test_hfs_stop_clamps_into_envelope_instead_of_dropping():
  from app.scalping.strategies import _stop_pips

  cfg = _cfg()
  # Deep wick used to return None → silent discovered=0.
  assert _stop_pips(structural=42.0, cfg=cfg) == 30.0
  assert _stop_pips(structural=8.0, cfg=cfg) == 12.0
  assert _stop_pips(structural=18.0, cfg=cfg) == 18.0
  assert _stop_pips(structural=0.0, cfg=cfg) is None
