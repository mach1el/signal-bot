"""TradePlan V7 builder - translates an already-confirmed StrategyMatch.

Per docs/adr-trade-plan-v7-boundary.md this must be a pure reshape, not a
second decision: stop.price must be exactly StrategyMatch.structure_swing
(no recomputation), and target prices must be a mechanical pip->price
conversion of the already-decided targets_pips ladder.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.autotrade.trade_plan import TradePlanError
from app.autotrade.trade_plan_builder import build_trade_plan_from_strategy_match


def _match(
  *,
  direction: str = "BUY",
  entry_low: float = 4088.10,
  entry_high: float = 4090.00,
  structure_swing: float = 4081.80,
  targets: tuple[int, ...] = (60, 140, 250),
  thesis_id: str | None = "thesis-abc",
  match_id: str = "match-abc",
) -> StrategyMatch:
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol="XAU",
    source_tf="M15",
    event_ts="1719999600",
    issued_at=1719999600,
    expires_at=1720003200,
    strategy="Trend Pullback",
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
    family="trend_pullback",
    thesis_id=thesis_id,
  )


def _build(match: StrategyMatch, **overrides):
  kwargs = {
    "plan_id": "plan-1",
    "setup_id": "setup-1",
    "pip_size": Decimal("0.1"),
    "entry_reference_price": Decimal("4090.00"),
    "price_side": "ask",
    "max_volume": 1000,
  }
  kwargs.update(overrides)
  return build_trade_plan_from_strategy_match(match, **kwargs)


def test_builds_valid_plan_from_confirmed_match():
  plan = _build(_match())

  assert plan.version == 7
  assert plan.plan_id == "plan-1"
  assert plan.symbol == "XAU"
  assert plan.analysis.direction == "BUY"
  assert plan.analysis.strategy == "Trend Pullback"
  assert plan.entry.type == "market_watch"
  assert plan.entry.zone_low == Decimal("4088.1")
  assert plan.entry.zone_high == Decimal("4090.0")


def test_stop_price_is_exactly_structure_swing_no_recomputation():
  match = _match(structure_swing=4081.80)
  plan = _build(match)

  assert plan.stop.price == Decimal("4081.8")
  assert plan.stop.source == "structural_invalidation"


def test_targets_convert_pips_to_absolute_prices_from_entry_reference():
  plan = _build(_match(targets=(60, 140, 250)))

  prices = [target.price for target in plan.targets]
  assert prices == [Decimal("4096.0"), Decimal("4104.0"), Decimal("4115.0")]
  assert [target.target_id for target in plan.targets] == ["TP1", "TP2", "TP3"]


def test_sell_direction_targets_go_below_entry_reference():
  match = _match(
    direction="SELL",
    entry_low=4110.00,
    entry_high=4112.00,
    structure_swing=4118.50,
    targets=(80, 180),
  )
  plan = _build(match, entry_reference_price=Decimal("4111.20"), price_side="bid")

  prices = [target.price for target in plan.targets]
  assert prices == [Decimal("4103.20"), Decimal("4093.20")]
  assert plan.stop.price == Decimal("4118.5")


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


def test_close_ratios_length_mismatch_raises():
  with pytest.raises(TradePlanError, match="close_ratios length"):
    _build(_match(targets=(60, 140, 250)), close_ratios=[Decimal("1.0")])


def test_missing_targets_pips_raises():
  with pytest.raises(TradePlanError, match="no targets_pips"):
    _build(_match(targets=()))


def test_invalid_price_side_raises():
  with pytest.raises(TradePlanError, match="price_side"):
    _build(_match(), price_side="mid")


def test_thesis_id_falls_back_to_match_id_when_absent():
  match = _match(thesis_id=None, match_id="fallback-id")
  plan = _build(match)

  assert plan.thesis_id == "fallback-id"


def test_be_after_target_defaults_to_first_target():
  plan = _build(_match(targets=(60, 140, 250)))

  assert plan.management.be_after_target_id == "TP1"


def test_be_after_target_index_can_be_overridden():
  plan = _build(_match(targets=(60, 140, 250)), be_after_target_index=1)

  assert plan.management.be_after_target_id == "TP2"


def test_source_structure_kind_follows_direction():
  buy_plan = _build(_match(direction="BUY"))
  sell_plan = _build(
    _match(
      direction="SELL",
      entry_low=4110.00,
      entry_high=4112.00,
      structure_swing=4118.50,
    ),
    entry_reference_price=Decimal("4111.20"),
    price_side="bid",
  )

  assert buy_plan.source_structure.kind == "demand"
  assert sell_plan.source_structure.kind == "supply"
