"""TradePlan V7 builder - translates an already-confirmed StrategyMatch.

Per docs/adr-trade-plan-v7-boundary.md this is a pure reshape of what
`evaluate_execution_policy` (the same route/stop planner the V6 path already
calls) already decided - never a second, independently-derived route or
stop, and never a direction-derived shortcut (BUY => bias up, BUY => demand).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan_builder import (
  TradePlanBuildRejected,
  build_trade_plan_from_strategy_match,
)


pytestmark = pytest.mark.no_database


def _match(
  *,
  direction: str = "BUY",
  entry_low: float = 4088.10,
  entry_high: float = 4090.00,
  structure_swing: float = 4081.80,
  targets: tuple[int, ...] = (60, 140, 250),
  match_id: str = "match-abc",
  structural_zone_id: str | None = "zone-xau-4088-4090",
  structural_kind: str | None = "demand",
  htf_bias: str = "up",
  regime_kind: str = "trend",
  strategy: str = "Trend Pullback",
  family: str = "trend_pullback",
) -> StrategyMatch:
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol="XAU",
    source_tf="M15",
    event_ts="1719999600",
    issued_at=1719999600,
    expires_at=1720003200,
    strategy=strategy,
    strategy_mode="with_trend",
    direction=direction,
    key_level=(entry_low + entry_high) / 2,
    entry_low=entry_low,
    entry_high=entry_high,
    current_price=(entry_low + entry_high) / 2,
    confluence=3,
    reasons=("htf_uptrend", "demand_reaction"),
    atr=1.8,
    structure_swing=structure_swing,
    targets_pips=targets,
    tier="A",
    family=family,
    structural_zone_id=structural_zone_id,
    structural_zone_low=entry_low,
    structural_zone_high=entry_high,
    structural_kind=structural_kind,
    structural_timeframe="H1",
    htf_bias=htf_bias,
    regime_kind=regime_kind,
  )


def _build(match: StrategyMatch, **overrides):
  kwargs = {
    "plan_id": "plan-1",
    "setup_id": "setup-1",
    "thesis_id": "thesis-1",
    "pip_size": Decimal("0.1"),
    "spot_price": match.current_price,
    "regime": "trend",
    "cfg": None,
    "executable_quote": match.current_price,
    "max_volume": 1000,
  }
  kwargs.update(overrides)
  return build_trade_plan_from_strategy_match(match, **kwargs)


def test_builds_valid_plan_with_real_bias_kind_and_regime():
  plan = _build(_match())

  assert plan.version == 7
  assert plan.plan_id == "plan-1"
  assert plan.thesis_id == "thesis-1"
  assert plan.symbol == "XAU"
  assert plan.analysis.direction == "BUY"
  assert plan.analysis.strategy == "Trend Pullback"
  # Real captured values, not derived from direction.
  assert plan.analysis.bias == "up"
  assert plan.analysis.regime == "trend"
  assert plan.source_structure.kind == "demand"
  assert plan.source_structure.structure_id == "zone-xau-4088-4090"
  assert plan.source_structure.timeframe == "H1"


def test_bias_and_kind_are_whatever_was_captured_not_direction_derived():
  # A BUY with a bearish HTF bias and a supply-side structural kind (e.g. a
  # counter-trend fade) must round-trip exactly as captured - the builder
  # must never silently overwrite it with "BUY => bias up, BUY => demand".
  match = _match(direction="BUY", htf_bias="down", structural_kind="supply")
  plan = _build(match)

  assert plan.analysis.direction == "BUY"
  assert plan.analysis.bias == "down"
  assert plan.source_structure.kind == "supply"


def test_now_ts_refreshes_a_stale_matchs_expiry_window():
  """Live incident: a match built at issued_at=1719999600 with a 3600s TTL
  (expires_at=1720003200) sat in WAITING_RETEST/IN_ZONE_WAITING_M1 for
  most of that window before its retest/M1 confirmation finally completed
  and the plan actually got published near the end of the original TTL.
  The C# executor then armed a market_watch plan with almost no runway
  left and it expired without ever getting a chance to submit, despite the
  executable quote already being inside the zone at publication. The
  published plan's expiry must restart from actual publish time (now_ts),
  not inherit whatever was left of the original match's clock.
  """
  match = _match()
  # Same TTL (3600s) as the fixture, but "now" is deep into that original
  # window - simulating a match that sat waiting for most of its TTL.
  stale_now = match.issued_at + 3500
  plan = _build(match, now_ts=stale_now)

  assert plan.expires_at == stale_now + 3600
  assert plan.entry.expires_at == stale_now + 3600
  # Not the stale, almost-exhausted original deadline.
  assert plan.expires_at != match.expires_at


def test_without_now_ts_falls_back_to_the_matchs_own_expiry():
  # Existing behavior preserved when a caller doesn't pass now_ts.
  match = _match()
  plan = _build(match)

  assert plan.expires_at == match.expires_at
  assert plan.entry.expires_at == match.expires_at


def test_missing_structural_zone_id_fails_closed():
  match = _match(structural_zone_id=None)

  with pytest.raises(TradePlanBuildRejected) as excinfo:
    _build(match)

  assert excinfo.value.reason_code == "missing_stable_thesis_id"


def test_missing_structural_kind_fails_closed():
  match = _match(structural_kind=None)

  with pytest.raises(TradePlanBuildRejected) as excinfo:
    _build(match)

  assert excinfo.value.reason_code == "missing_structural_kind"


def test_missing_htf_bias_fails_closed():
  match = _match(htf_bias="")

  with pytest.raises(TradePlanBuildRejected) as excinfo:
    _build(match)

  assert excinfo.value.reason_code == "missing_htf_bias"


def test_market_route_produces_market_watch_entry():
  # quote inside the zone, zone-fill disabled -> execution_policy resolves
  # "market".
  match = _match(entry_low=4088.10, entry_high=4090.00)
  plan = _build(match, spot_price=4089.0, executable_quote=4089.0)

  assert plan.entry.type == "market_watch"
  assert plan.entry.zone_low == Decimal("4088.1")
  assert plan.entry.zone_high == Decimal("4090.0")
  assert plan.entry.price_side == "ask"
  assert plan.execution_policy.allow_market is True
  assert plan.execution_policy.allow_limit is False


def test_single_limit_route_produces_single_limit_entry():
  # Narrow zone relative to ATR (zone_width_atr < 0.5) resolves "single".
  match = _match(entry_low=4088.60, entry_high=4088.80)
  plan = _build(match, spot_price=4088.5, executable_quote=4088.5)

  assert plan.entry.type == "single_limit"
  assert plan.entry.order_price == Decimal("4088.5")
  assert plan.execution_policy.allow_limit is True
  assert plan.execution_policy.allow_market is False


def test_zone_split_route_produces_limit_ladder_with_two_legs():
  cfg = SimpleNamespace(
    auto_trade_zone_fill_enabled=True, auto_trade_zone_fill_min_atr=0.1,
  )
  match = _match(entry_low=4088.10, entry_high=4090.00)
  plan = _build(match, cfg=cfg, spot_price=4089.0, executable_quote=4089.0)

  assert plan.entry.type == "limit_ladder"
  assert len(plan.entry.legs) == 2
  total_ratio = sum(leg.volume_ratio for leg in plan.entry.legs)
  assert total_ratio == Decimal("1")


def test_key_level_reaction_emits_market_with_limit_scale():
  cfg = SimpleNamespace(
    auto_trade_zone_fill_enabled=True,
    auto_trade_zone_fill_min_atr=0.1,
    auto_trade_reaction_scale_enabled=True,
    auto_trade_reaction_market_fraction=0.70,
    auto_trade_reaction_scale_fraction=0.30,
    auto_trade_reaction_scale_step_atr=0.5,
    auto_trade_reaction_scale_invalid_policy="single_market",
  )
  match = _match(
    strategy="Key Level Reaction",
    family="key_level",
    structural_kind="key_level",
    entry_low=4088.10,
    entry_high=4090.00,
    # Within reaction 40–60 pip envelope from ~4089 entry.
    structure_swing=4083.50,
  )
  plan = _build(match, cfg=cfg, spot_price=4089.0, executable_quote=4089.0)

  assert plan.entry.type == "market_with_limit_scale"
  assert len(plan.entry.legs) == 2
  assert plan.entry.legs[0].order_type == "market"
  assert plan.entry.legs[1].order_type == "limit"
  assert plan.entry.legs[0].volume_ratio == Decimal("0.70")
  assert plan.entry.legs[1].volume_ratio == Decimal("0.30")
  assert plan.execution_policy.allow_market is True
  assert plan.execution_policy.allow_limit is True


def test_demand_zone_reaction_does_not_emit_market_with_limit_scale():
  cfg = SimpleNamespace(
    auto_trade_zone_fill_enabled=True,
    auto_trade_zone_fill_min_atr=0.1,
    auto_trade_reaction_scale_enabled=True,
    auto_trade_zone_scale_first_leg_fraction=0.70,
    auto_trade_zone_scale_step_atr=0.5,
  )
  match = _match(
    strategy="Demand Zone Reaction",
    family="supply_demand",
    structural_kind="demand",
    entry_low=4088.10,
    entry_high=4090.00,
    structure_swing=4083.50,
  )
  plan = _build(match, cfg=cfg, spot_price=4089.0, executable_quote=4089.0)

  assert plan.entry.type == "limit_ladder"
  assert all(leg.order_type is None for leg in plan.entry.legs)

def test_stop_price_comes_from_execution_policy_not_raw_structure_swing():
  # structure_swing=4081.80 but the real protective-stop plan (buffer,
  # clamping, min/max stop pips) produces a different absolute price - the
  # builder must transport that, not fall back to the raw swing.
  match = _match(structure_swing=4081.80)
  plan = _build(match, spot_price=4089.0, executable_quote=4089.0)

  assert plan.stop.price != Decimal("4081.8")
  # Protective-stop plan applies buffer/clamp; exact absolute depends on the
  # live envelope helpers — only assert it is not the raw structure swing.
  assert plan.stop.price < Decimal("4088.10")
  assert plan.stop.structure_id == "zone-xau-4088-4090"


def test_targets_convert_pips_to_absolute_prices_from_planned_entry():
  plan = _build(_match(targets=(60, 140, 250)), spot_price=4089.0, executable_quote=4089.0)

  prices = [target.price for target in plan.targets]
  assert prices == [Decimal("4095.0"), Decimal("4103.0"), Decimal("4114.0")]
  assert [target.target_id for target in plan.targets] == ["TP1", "TP2", "TP3"]


def test_equal_close_ratios_sum_to_exactly_one():
  plan = _build(_match(targets=(60, 140, 250)))

  total = sum(target.close_ratio for target in plan.targets)
  assert total == Decimal("1")


def test_custom_close_ratios_are_respected():
  plan = _build(
    _match(targets=(60, 140, 250)),
    close_ratios=[Decimal("0.4"), Decimal("0.35"), Decimal("0.25")],
  )

  ratios = [target.close_ratio for target in plan.targets]
  assert ratios == [Decimal("0.4"), Decimal("0.35"), Decimal("0.25")]


def test_close_ratios_length_mismatch_rejects():
  with pytest.raises(TradePlanBuildRejected, match="close_ratios length"):
    _build(_match(targets=(60, 140, 250)), close_ratios=[Decimal("1.0")])


def test_missing_targets_pips_rejects():
  with pytest.raises(TradePlanBuildRejected, match="no targets_pips"):
    _build(_match(targets=()))


def test_be_after_target_defaults_to_first_target():
  plan = _build(_match(targets=(60, 140, 250)))

  assert plan.management.be_after_target_id == "TP1"


def test_be_after_target_index_can_be_overridden():
  plan = _build(_match(targets=(60, 140, 250)), be_after_target_index=1)

  assert plan.management.be_after_target_id == "TP2"


def test_execution_policy_preference_does_not_block_builder():
  # confluence 1 is below Trend Pullback's min_confluence=2 — preference
  # telemetry only; the builder still produces a plan.
  match = _match()
  low_confluence = replace(match, confluence=1)

  plan = _build(low_confluence)
  assert plan.analysis.confluence == 1


def test_retest_trigger_wick_drives_stop_rr_and_confirmation_provenance():
  plan = _build(
    _match(structure_swing=4085.0, targets=(140, 250)),
    confirmation_source="m1_retest",
    execution_confirmation_bar_ts=1_720_000_060,
    zone_episode_id="episode-1",
    trigger_wick_extreme=4083.5,
  )

  assert plan.stop.source == "m1_trigger_wick"
  assert plan.stop.price < Decimal("4084.46")
  assert plan.provenance.confirmation_source == "m1_retest"
  assert plan.provenance.confirmation_bar_ts == 1_720_000_060
  assert plan.provenance.zone_episode_id == "episode-1"


def test_final_reward_risk_preference_keeps_builder_plan():
  plan = _build(
    _match(structure_swing=4085.0, targets=(60,)),
    confirmation_source="m1_retest",
    execution_confirmation_bar_ts=1_720_000_060,
    zone_episode_id="episode-rr",
    trigger_wick_extreme=4083.5,
  )
  assert plan.management is not None
  assert plan.stop.source == "m1_trigger_wick"


def test_stop_inside_opposing_zone_surfaces_precise_reason_and_evidence():
  # Structure stop lands inside a tight demand zone; push beyond max envelope
  # must reject with stop_inside_opposing_zone (not a generic wrapper) and
  # carry stop-side zone + push distances for later improvement evidence.
  match = replace(
    _match(
      strategy="Key Level Reaction",
      family="key_level",
      structure_swing=4096.70,
      entry_low=4099.5,
      entry_high=4100.5,
      targets=(30,),
      regime_kind="chop",
    ),
    atr=2.0,
  )
  cfg = SimpleNamespace(
    auto_trade_stop_push_beyond_zone=True,
    auto_trade_add_stop_buffer_atr=0.3,
    auto_trade_wick_stop_buffer_atr=0.15,
    auto_trade_xau_price_digits=2,
    auto_trade_execution_zone_max_width_atr=2.0,
    auto_trade_execution_zone_max_width_pips=100,
    auto_trade_trend_stop_min_pips=20,
    auto_trade_trend_stop_max_pips=60,
    auto_trade_zone_fill_enabled=True,
  )
  with pytest.raises(TradePlanBuildRejected) as excinfo:
    _build(
      match,
      spot_price=4100.0,
      executable_quote=4100.0,
      regime="chop",
      cfg=cfg,
      opposing_zone_low=4094.0,
      opposing_zone_high=4097.0,
      opposing_zone_id="supply-evidence-1",
    )

  assert excinfo.value.reason_code == "stop_inside_opposing_zone"
  measured = excinfo.value.measured
  assert measured["stop_reject_detail"] == "pushed_exceeds_max_envelope"
  assert measured["stop_side_opposing_zone_id"] == "supply-evidence-1"
  assert measured["stop_side_opposing_zone_low"] == 4094.0
  assert measured["stop_side_opposing_zone_high"] == 4097.0
  assert measured["stop_side_opposing_execution_grade"] is True
  assert measured.get("planned_base_stop_price")
  assert measured.get("planned_pushed_stop_price")
  assert measured.get("pushed_over_envelope_pips")
  assert measured["stop_max_envelope_pips"] == 60
