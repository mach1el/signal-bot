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
  detect_momentum_ignition,
  detect_sweep_reclaim,
  build_micro_structure,
  macro_momentum_direction,
)
from app.scalping.models import (
  ARCHETYPE_IMPULSE_PULLBACK,
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
      momentum_chase_enabled=False,
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


def test_asia_empty_outside_killzone_under_technique_pack():
  """Owner 2026-08-10: HFS archetypes only inside killzone (not all of Asia)."""
  from types import SimpleNamespace
  from app.scalping.models import (
    ARCHETYPE_BREAKOUT_RETEST,
    ARCHETYPE_IMPULSE_PULLBACK,
    ARCHETYPE_MOMENTUM_CHASE,
    ARCHETYPE_RANGE_SWEEP,
  )
  cfg = SimpleNamespace(
    market_data=SimpleNamespace(
      sessions=SimpleNamespace(
        london_start=7, ny_start=13, asia_start=22, daily_rollover_utc_hour=21,
      ),
    ),
    execution=SimpleNamespace(
      technique=SimpleNamespace(
        enforce=True,
        include_late_ny=True,
        london_window_hours=3,
        ny_window_hours=3,
        hfs_require_killzone=True,
      ),
    ),
  )
  assert permitted_archetypes_for_session("asia", hour=3, cfg=cfg) == (
    ARCHETYPE_RANGE_SWEEP,
  )
  assert permitted_archetypes_for_session("rollover", cfg=cfg) == ()
  assert permitted_archetypes_for_session("london", hour=8, cfg=cfg) == (
    ARCHETYPE_RANGE_SWEEP,
    ARCHETYPE_IMPULSE_PULLBACK,
    ARCHETYPE_BREAKOUT_RETEST,
  )


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


def test_edge_touch_reclaim_counts_without_piercing_below():
  # Wick exactly to the edge (not through) used to return None forever.
  idx = pd.date_range("2026-07-01 10:00", periods=2, freq="1min", tz="UTC")
  df = pd.DataFrame([
    {"open": 4010, "high": 4012, "low": 4008, "close": 4009, "volume": 1},
    {"open": 4001, "high": 4005, "low": 4000.0, "close": 4004, "volume": 1},
  ], index=idx)
  hit = detect_sweep_reclaim(
    df, direction="BUY", edge_price=4000.0, tolerance=1.0, lookback_bars=1,
  )
  assert hit is not None
  assert hit["extreme"] == 4000.0


def test_sweep_reclaim_lookback_picks_prior_bar():
  # Reclaim on bar -2; newest bar is noise. Activation allows age=2 bars so
  # discovery must recover the prior reclaim instead of reporting discovered=0.
  idx = pd.date_range("2026-07-01 10:00", periods=3, freq="1min", tz="UTC")
  df = pd.DataFrame([
    {"open": 4010, "high": 4012, "low": 4008, "close": 4011, "volume": 1},
    {"open": 3998, "high": 4005, "low": 3995, "close": 4002, "volume": 1},
    # Newest is mid-range noise — no edge touch within tolerance.
    {"open": 4005, "high": 4008, "low": 4004, "close": 4007, "volume": 1},
  ], index=idx)
  assert detect_sweep_reclaim(
    df, direction="BUY", edge_price=4000.0, tolerance=1.0, lookback_bars=1,
  ) is None
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


def test_risk_loss_streak_blocks_after_cooldown_until_reset():
  from app.scalping.risk import (
    apply_loss_streak_cooldown_reset,
    record_scalp_outcome,
  )

  cfg = _cfg()
  now = 1_780_000_000
  state = ScalpRiskState(consecutive_losses=3, last_loss_ts=now - 60)
  during = evaluate_risk(state, cfg, session="london", now=now)
  assert during.allowed is False
  assert during.reason_code == "scalp_loss_streak_cooldown"

  after_cooldown = evaluate_risk(
    state, cfg, session="london", now=now + 10 * 60,
  )
  assert after_cooldown.allowed is False
  assert after_cooldown.reason_code == "scalp_loss_streak_active"

  cleared = apply_loss_streak_cooldown_reset(
    state, cfg, now=now + 10 * 60,
  )
  assert cleared.consecutive_losses == 0
  allowed = evaluate_risk(cleared, cfg, session="london", now=now + 10 * 60)
  assert allowed.allowed is True

  winning = record_scalp_outcome(
    ScalpRiskState(consecutive_losses=2, last_loss_ts=now),
    result_pips=20.0,
    stop_pips=20.0,
    now=now,
    closed=True,
  )
  assert winning.consecutive_losses == 0


def test_daily_loss_limit_unsticks_at_trading_day_rollover():
  """Live 2026-08-13: daily_r sat at -3.75R from a loss on 2026-08-12,
  scalp_daily_loss_limit stayed tripped over 24h later because day_key was
  persisted but never compared against anything -- the reset never ran.
  """
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  # Matches the live redis snapshot: stale/never-set day_key, breached limit.
  state = ScalpRiskState(daily_trades=7, daily_r=-3.75, day_key="")
  now = 1_786_631_040  # 2026-08-13 14:24 UTC

  stuck = evaluate_risk(state, cfg, session="london", now=now)
  assert stuck.allowed is False
  assert stuck.reason_code == "scalp_daily_loss_limit"

  reset_state = apply_daily_reset(state, cfg, now=now, session="london")
  assert reset_state.daily_r == 0.0
  assert reset_state.daily_trades == 0
  assert reset_state.day_key != ""

  unstuck = evaluate_risk(reset_state, cfg, session="london", now=now)
  assert unstuck.allowed is True


def test_daily_reset_is_noop_within_same_trading_day():
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  now = 1_786_631_040  # 2026-08-13 14:24 UTC
  # Prime day_key for "now"'s trading day, as a real load->reset->accumulate
  # cycle would, before asserting a later same-day call leaves counters alone.
  primed = apply_daily_reset(ScalpRiskState(), cfg, now=now, session="london")
  state = ScalpRiskState(
    daily_trades=4, daily_r=-1.5,
    day_key=primed.day_key, session_key=primed.session_key,
  )

  ten_minutes_later = apply_daily_reset(
    state, cfg, now=now + 600, session="london"
  )
  assert ten_minutes_later.daily_trades == 4
  assert ten_minutes_later.daily_r == -1.5


def test_daily_reset_boundary_is_trading_rollover_not_utc_midnight():
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  before_rollover = 1_786_568_340  # 2026-08-12 20:59 UTC
  after_rollover = 1_786_568_460  # 2026-08-12 21:01 UTC
  primed = apply_daily_reset(
    ScalpRiskState(), cfg, now=before_rollover, session="late_ny"
  )
  state = ScalpRiskState(
    daily_trades=5, daily_r=-2.0,
    day_key=primed.day_key, session_key=primed.session_key,
  )

  state = apply_daily_reset(state, cfg, now=before_rollover, session="late_ny")
  # Same trading day as before_rollover -- must not reset yet.
  assert state.daily_trades == 5
  assert state.daily_r == -2.0

  state = apply_daily_reset(state, cfg, now=after_rollover, session="rollover")
  # Crossed the 21:00 UTC trading-day boundary -- must reset.
  assert state.daily_trades == 0
  assert state.daily_r == 0.0


def test_session_reset_clears_session_counters_on_session_change():
  from app.scalping.risk import apply_daily_reset

  cfg = _cfg()
  now = 1_786_631_040  # 2026-08-13 14:24 UTC
  state = ScalpRiskState(session_trades=6, session_r=-1.8, day_key="")
  state = apply_daily_reset(state, cfg, now=now, session="london")
  assert state.session_trades == 0
  assert state.session_r == 0.0

  state.session_trades = 6
  state.session_r = -1.8
  same_session = apply_daily_reset(state, cfg, now=now + 60, session="london")
  assert same_session.session_trades == 6
  assert same_session.session_r == -1.8

  new_session = apply_daily_reset(
    same_session, cfg, now=now + 120, session="new_york"
  )
  assert new_session.session_trades == 0
  assert new_session.session_r == 0.0


def test_momentum_chase_disabled_by_default():
  from app.scalping.strategies import discover_momentum_chase

  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-momentum-off",
    symbol="XAU",
    created_at=1_780_000_300,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_000_300,
    htf_bias="down",
    m5_structure="bearish",
    regime="trend",
    dealing_range_low=4000.0,
    dealing_range_high=4200.0,
    dealing_range_position=0.5,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=None,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=("momentum_chase",),
    atr=1.0,
  )
  idx = pd.date_range("2026-07-01 10:00", periods=6, freq="1min", tz="UTC")
  df = pd.DataFrame({
    "open": [4100, 4098, 4095, 4092, 4088, 4085],
    "high": [4101, 4099, 4096, 4093, 4089, 4086],
    "low": [4097, 4094, 4091, 4087, 4084, 4080],
    "close": [4098, 4095, 4092, 4088, 4085, 4082],
    "volume": [1] * 6,
  }, index=idx)
  assert discover_momentum_chase(
    ctx, None, df, _cfg(), pip_size=0.1, now=1_780_000_300,
  ) == []


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


def test_activation_chases_momentum_within_chase_budget():
  """Price past zone high must chase, not wait as quote_outside_zone."""
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="c",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=4000.0,
    dealing_range_high=4200.0,
    dealing_range_position=0.25,
    active_range_low=4000.0,
    active_range_high=4100.0,
    active_range_eq=4050.0,
    nearest_support_low=4000.0,
    nearest_support_high=4002.0,
    nearest_resistance_low=4098.0,
    nearest_resistance_high=4100.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_RANGE_SWEEP,),
    atr=5.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="chase-o",
    context_id="c",
    symbol="XAU",
    archetype=ARCHETYPE_RANGE_SWEEP,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=100,
    zone_low=4010.0,
    zone_high=4012.0,
    key_level=4011.0,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=90,
    trigger_price=4011.0,
    invalidation_price=4005.0,
    expected_target_price=4035.0,
    expected_target_pips=25,
    expected_stop_pips=15,
    expected_reward_risk=1.5,
    location_position=0.25,
    score=1.0,
    reasons=("t",),
    expires_at=200,
  )
  cfg = _cfg()
  cfg.strategies.high_frequency_scalp.activation.maximum_chase_pips = 100.0
  # 10 pips above zone high — used to soft-wait forever; must chase.
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4012.9,
    quote_ask=4013.0,
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=cfg,
  )
  assert decision.allowed is True
  assert decision.measured.get("chase_entry") is True
  assert decision.measured.get("chase_pips") == pytest.approx(10.0)

  missed = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4022.9,
    quote_ask=4023.0,  # 110 pips above zone → miss past 100
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=cfg,
  )
  assert missed.allowed is False
  assert missed.reason_code == "scalp_missed_chase"


def test_activation_allows_one_to_one_room_when_min_net_fits():
  """Live 2026-08-06 09:08: HFS Impulse Pullback BUY target=30 stop=30 died
  on scalp_net_rr_insufficient after cost haircut. Min net room is enough.
  """
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="c",
    symbol="XAU",
    created_at=100,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=100,
    htf_bias="up",
    m5_structure="bullish",
    regime="trend",
    dealing_range_low=4250.0,
    dealing_range_high=4300.0,
    dealing_range_position=0.52,
    active_range_low=4250.0,
    active_range_high=4285.0,
    active_range_eq=4267.0,
    nearest_support_low=4250.0,
    nearest_support_high=4252.0,
    nearest_resistance_low=4283.0,
    nearest_resistance_high=4285.0,
    buy_corridor_room_pips=40.0,
    sell_corridor_room_pips=40.0,
    session="london",
    permitted_archetypes=(ARCHETYPE_IMPULSE_PULLBACK,),
    atr=5.0,
  )
  opp = ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="rr-o",
    context_id="c",
    symbol="XAU",
    archetype=ARCHETYPE_IMPULSE_PULLBACK,
    direction="BUY",
    discovered_at=100,
    source_bar_ts=100,
    zone_low=4278.42,
    zone_high=4279.40,
    key_level=4278.91,
    trigger_type="impulse_pullback",
    trigger_bar_ts=90,
    trigger_price=4278.91,
    invalidation_price=4270.0,
    expected_target_price=4281.91,
    expected_target_pips=30,
    expected_stop_pips=30,
    expected_reward_risk=1.0,
    location_position=0.52,
    score=1.0,
    reasons=("impulse_pullback_continuation",),
    expires_at=200,
  )
  decision = evaluate_scalp_activation(
    opp,
    ctx,
    quote_bid=4278.9,
    quote_ask=4279.0,
    quote_ts=100,
    now=100,
    pip_size=0.1,
    cfg=_cfg(),
  )
  assert decision.allowed is True
  assert decision.reason_code == "scalp_activation_allowed"
  assert decision.measured.get("net_rr_below_policy") is True
  assert decision.measured.get("preference_telemetry") is True
  assert decision.measured["net_reward_risk"] < 1.10


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


def test_scalp_target_prefers_1to2_falls_back_to_1to1():
  # Owner 2026-08-11: every scalp is 1:2 when the room supports it, 1:1
  # otherwise - never a ladder, never anything outside this pair.
  from app.scalping.strategies import _select_target

  # Room comfortably fits 2x stop -> 1:2 wins.
  buy_price, buy_pips = _select_target(
    direction="BUY", entry=4000.0, room_pips=40.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  )
  assert buy_pips == pytest.approx(36.0)
  assert buy_price == pytest.approx(4003.6)

  sell_price, sell_pips = _select_target(
    direction="SELL", entry=4000.0, room_pips=40.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  )
  assert sell_pips == pytest.approx(36.0)
  assert sell_price == pytest.approx(3996.4)

  # Room fits 1x stop but not 2x -> falls back to 1:1, not a trimmed 1:2.
  price, pips = _select_target(
    direction="BUY", entry=4000.0, room_pips=20.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  )
  assert pips == pytest.approx(18.0)
  assert price == pytest.approx(4001.8)

  # Stop below the minimum net target at 1:1 but 1:2 clears it -> 1:2 wins,
  # not the old "no opportunity" outcome from when only 1:1 existed.
  price, pips = _select_target(
    direction="BUY", entry=4000.0, room_pips=40.0, stop_pips=10.0,
    min_net=15.0, pip_size=0.1,
  )
  assert pips == pytest.approx(20.0)

  # Neither ratio fits the available room -> no opportunity, not a trimmed
  # target outside the {1:1, 1:2} pair.
  assert _select_target(
    direction="BUY", entry=4000.0, room_pips=15.0, stop_pips=18.0,
    min_net=15.0, pip_size=0.1,
  ) is None

  assert _select_target(
    direction="BUY", entry=4000.0, room_pips=40.0, stop_pips=None,
    min_net=15.0, pip_size=0.1,
  ) is None


def _thrust_bars(*, direction: str, bars: int = 5, step: float = 2.0, start: float = 4110.0):
  """A straight, uninterrupted run -- no pullback, no basing, still fresh
  extremes on the last bar. Mirrors the live production chart (XAU 06 Aug
  2026 ~12:25-13:15 UTC) that impulse_pullback correctly reported as
  not_matched because there was no retracement yet to measure.
  """
  rows = []
  index = []
  price = start
  for i in range(bars):
    if direction == "SELL":
      o, c = price, price - step
      h, low = o + 0.2, c - 0.2
    else:
      o, c = price, price + step
      h, low = c + 0.2, o - 0.2
    rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    index.append(pd.Timestamp(1_780_000_000 + i * 60, unit="s", tz="UTC"))
    price = c
  return pd.DataFrame(rows, index=index)


def test_momentum_ignition_detects_live_sell_thrust():
  # 5 straight bearish bars, 10 price units of displacement against a 1.0
  # ATR (10x the 1.0x floor) -- exactly the "not_matched" scenario from
  # production, now caught instead of waited out.
  df = _thrust_bars(direction="SELL")
  ev = detect_momentum_ignition(df, direction="SELL", atr=1.0)
  assert ev is not None
  assert ev["pattern"] == "momentum_ignition"
  assert ev["direction"] == "SELL"
  assert ev["directional_bars"] == 5
  assert ev["displacement_atr"] == pytest.approx(10.0)
  assert ev["extreme"] == pytest.approx(4110.2)  # highest high, for the stop
  assert ev["close"] == pytest.approx(4100.0)


def test_momentum_ignition_detects_live_buy_thrust():
  df = _thrust_bars(direction="BUY", start=4090.0)
  ev = detect_momentum_ignition(df, direction="BUY", atr=1.0)
  assert ev is not None
  assert ev["direction"] == "BUY"
  assert ev["extreme"] == pytest.approx(4089.8)  # lowest low, for the stop


def test_momentum_ignition_rejects_insufficient_displacement():
  # Same shape, but a much larger ATR means 10 units of displacement no
  # longer clears the 1.0x floor (owner-tuned 2026-08-11, was 1.2 - a real
  # production thrust measured 1.056, 88% of the old floor, and got
  # rejected). Diagnostic (2026-08-11): this used to be a bare None,
  # indistinguishable from every other rejection reason - now carries its
  # own reason + the measured/required displacement.
  df = _thrust_bars(direction="SELL")
  ev = detect_momentum_ignition(df, direction="SELL", atr=50.0)
  assert ev is not None
  assert ev.get("rejected") is True
  assert ev["reason"] == "insufficient_displacement"
  assert ev["displacement_atr"] == pytest.approx(0.2)
  assert ev["min_displacement_atr"] == pytest.approx(1.0)


def test_momentum_ignition_rejects_mixed_direction():
  df = _thrust_bars(direction="SELL")
  # Flip two of five bars bullish -- only 3 directional bars, under the
  # 4-of-5 floor.
  close_col = df.columns.get_loc("close")
  df.iloc[1, close_col] = df.iloc[1]["open"] + 0.5
  df.iloc[2, close_col] = df.iloc[2]["open"] + 0.5
  ev = detect_momentum_ignition(df, direction="SELL", atr=1.0)
  assert ev is not None
  assert ev.get("rejected") is True
  assert ev["reason"] == "insufficient_directional_bars"
  assert ev["directional_bars"] == 3
  assert ev["min_directional_bars"] == 4


def test_momentum_ignition_rejects_when_stalling():
  # Last bar no longer makes a fresh low -- momentum has paused, which is
  # exactly the retracement impulse_pullback is waiting for, not this one.
  df = _thrust_bars(direction="SELL")
  last = df.index[-1]
  df.loc[last, "low"] = df["low"].iloc[:-1].min() + 0.5
  ev = detect_momentum_ignition(df, direction="SELL", atr=1.0)
  assert ev is not None
  assert ev.get("rejected") is True
  assert ev["reason"] == "momentum_stalling"


def test_discover_momentum_chase_builds_1to2_opportunity_when_room_allows():
  from app.scalping.strategies import discover_momentum_chase

  df = _thrust_bars(direction="SELL")
  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-momentum",
    symbol="XAU",
    created_at=1_780_000_300,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_000_300,
    htf_bias="down",
    m5_structure="bearish",
    regime="trend",
    dealing_range_low=4000.0,
    dealing_range_high=4200.0,
    dealing_range_position=0.5,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=None,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=("momentum_chase",),
    atr=1.0,
  )
  opps = discover_momentum_chase(
    ctx, None, df, _cfg(archetypes=SimpleNamespace(
      range_sweep_enabled=True,
      impulse_pullback_enabled=True,
      breakout_retest_enabled=True,
      momentum_chase_enabled=True,
    )), pip_size=0.1, now=1_780_000_300,
  )
  assert len(opps) == 1
  opp = opps[0]
  assert opp.archetype == "momentum_chase"
  assert opp.direction == "SELL"
  # 1:2 -- owner 2026-08-11: every scalp prefers 1:2 when the (huge, 200
  # pip) room here easily supports it, same rule as every other archetype.
  assert opp.expected_target_pips == pytest.approx(opp.expected_stop_pips * 2)
  assert opp.expected_reward_risk == pytest.approx(2.0)


def test_impulse_pullback_episode_id_survives_an_m5_context_rollover(monkeypatch):
  # Live 2026-08-06: a WAITING FILL card for an HFS Impulse Pullback never
  # updated to POSITION ACTIVATED - a second, brand-new card appeared below
  # it instead once the position actually filled. Root cause: episode_id
  # (opportunity.episode_id, the identity same_thesis()/dedupe_matches()
  # compares across scan cycles) was hashed from context.context_id, which
  # itself bakes in m5_bar_ts (context.py's compute_context_id). The exact
  # same still-unfilled impulse/pullback pattern gets a brand-new episode_id
  # the instant the M5 candle rolls over mid-wait, fails dedup against its
  # own earlier self, and spawns an independent second match/plan/card while
  # the first sits orphaned. episode_id must depend only on the pattern's
  # own stable geometry (origin/extreme/direction/symbol), never on the
  # M5-bar-bucketed context identity.
  from app.scalping.strategies import discover_impulse_pullback

  local_sell_match = {
    "pattern": "impulse_pullback",
    "direction": "SELL",
    "bar_ts": 1_780_003_600,
    "origin": 4270.0,
    "extreme": 4230.0,
    "retracement": 0.5,
    "preferred": True,
    "close": 4250.0,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: local_sell_match if direction == "SELL" else None,
  )
  flat = _drift_bars(direction="BUY", step=0.0)

  def _ctx(context_id: str, m5_bar_ts: int) -> ScalpContextSnapshot:
    return ScalpContextSnapshot(
      version=CONTEXT_VERSION,
      context_id=context_id,
      symbol="XAU",
      created_at=m5_bar_ts,
      h1_bar_ts=None,
      m15_bar_ts=None,
      m5_bar_ts=m5_bar_ts,
      htf_bias="unknown",
      m5_structure="range",
      regime="range",
      dealing_range_low=4230.0,
      dealing_range_high=4274.0,
      dealing_range_position=0.95,
      active_range_low=None,
      active_range_high=None,
      active_range_eq=None,
      nearest_support_low=None,
      nearest_support_high=None,
      nearest_resistance_low=None,
      nearest_resistance_high=None,
      buy_corridor_room_pips=None,
      sell_corridor_room_pips=200.0,
      session="london",
      permitted_archetypes=("impulse_pullback",),
      atr=8.0,
    )

  # Same real-world opportunity, discovered on two different M5 bars (an
  # M5 rollover while still waiting for fill) - context_id necessarily
  # differs since it's bucketed by m5_bar_ts.
  first = discover_impulse_pullback(
    _ctx("ctx-bar-1", 1_780_003_600), None, flat, _cfg(), pip_size=0.1,
    now=1_780_003_600,
  )
  second = discover_impulse_pullback(
    _ctx("ctx-bar-2", 1_780_003_900), None, flat, _cfg(), pip_size=0.1,
    now=1_780_003_900,
  )
  assert len(first) == 1
  assert len(second) == 1
  assert first[0].context_id != second[0].context_id
  assert first[0].episode_id == second[0].episode_id
  assert first[0].episode_id != ""


def _drift_bars(*, direction: str, bars: int = 60, step: float = 0.7, start: float = 4230.0):
  """A wide, gentle net drift -- mimics a real reclaim: not every bar is
  directional (unlike _thrust_bars), the NET displacement over the whole
  window is what matters here.
  """
  rows = []
  index = []
  price = start
  sign = 1.0 if direction == "BUY" else -1.0
  for i in range(bars):
    wiggle = 0.3 if i % 3 == 0 else -0.15  # net still trends, not monotonic
    o = price
    c = price + sign * step + sign * wiggle
    h = max(o, c) + 0.2
    low = min(o, c) - 0.2
    rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    index.append(pd.Timestamp(1_780_000_000 + i * 60, unit="s", tz="UTC"))
    price = c
  return pd.DataFrame(rows, index=index)


def test_macro_momentum_direction_detects_buy_bias():
  df = _drift_bars(direction="BUY")
  assert macro_momentum_direction(df, atr=8.0) == "BUY"


def test_macro_momentum_direction_detects_sell_bias():
  df = _drift_bars(direction="SELL")
  assert macro_momentum_direction(df, atr=8.0) == "SELL"


def test_macro_momentum_direction_none_when_insufficient_bars():
  df = _drift_bars(direction="BUY", bars=40)
  assert macro_momentum_direction(df, atr=8.0, lookback_bars=60) is None


def test_macro_momentum_direction_none_when_displacement_too_small():
  df = _drift_bars(direction="BUY", step=0.05)
  assert macro_momentum_direction(df, atr=8.0) is None


def test_impulse_pullback_vetoes_sell_against_fresh_reclaim(monkeypatch):
  # Regression, live 2026-08-06: an impulse_pullback SELL faded the "top"
  # of a range whose own high was the pre-crash level -- price was
  # mid-reclaim of a flash crash under an hour old, with the freshest,
  # strongest momentum on the chart still running against the SELL.
  # detect_impulse_pullback's own lookback (30 bars) never saw the move
  # that made that level matter; this proves the wider veto now does.
  from app.scalping.strategies import discover_impulse_pullback

  local_sell_match = {
    "pattern": "impulse_pullback",
    "direction": "SELL",
    "bar_ts": 1_780_003_600,
    "origin": 4270.0,
    "extreme": 4230.0,
    "retracement": 0.5,
    "preferred": True,
    "close": 4250.0,
  }
  monkeypatch.setattr(
    "app.scalping.strategies.detect_impulse_pullback",
    lambda df, *, direction: local_sell_match if direction == "SELL" else None,
  )

  ctx = ScalpContextSnapshot(
    version=CONTEXT_VERSION,
    context_id="ctx-reclaim",
    symbol="XAU",
    created_at=1_780_003_600,
    h1_bar_ts=None,
    m15_bar_ts=None,
    m5_bar_ts=1_780_003_600,
    htf_bias="unknown",
    m5_structure="range",
    regime="range",
    dealing_range_low=4230.0,
    dealing_range_high=4274.0,
    dealing_range_position=0.95,
    active_range_low=None,
    active_range_high=None,
    active_range_eq=None,
    nearest_support_low=None,
    nearest_support_high=None,
    nearest_resistance_low=None,
    nearest_resistance_high=None,
    buy_corridor_room_pips=None,
    sell_corridor_room_pips=200.0,
    session="london",
    permitted_archetypes=("impulse_pullback",),
    atr=8.0,
  )

  # A wide BUY reclaim is still fresh -> the local SELL match is vetoed.
  reclaiming = _drift_bars(direction="BUY")
  vetoed = discover_impulse_pullback(ctx, None, reclaiming, _cfg(), pip_size=0.1, now=1_780_003_600)
  assert vetoed == []

  # No macro drift at all -> the same local match is allowed through.
  flat = _drift_bars(direction="BUY", step=0.0)
  allowed = discover_impulse_pullback(ctx, None, flat, _cfg(), pip_size=0.1, now=1_780_003_600)
  assert len(allowed) == 1
  assert allowed[0].direction == "SELL"
