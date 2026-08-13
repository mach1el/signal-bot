"""Unit tests for entry-location premium/discount authority."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis.entry_location import (
  build_entry_location_context,
  evaluate_entry_location,
  range_position,
)


pytestmark = pytest.mark.no_database

RANGE_LOW = 4000.0
RANGE_HIGH = 4100.0


def _location_cfg(mode: str = "enforce"):
  return SimpleNamespace(
    actionability=SimpleNamespace(
      entry_location=SimpleNamespace(
        mode=mode,
        missing_context_policy="block",
        reversal=SimpleNamespace(
          buy_maximum_position=0.50,
          sell_minimum_position=0.50,
          extreme_buy_block_position=0.65,
          extreme_sell_block_position=0.35,
        ),
        range_reversion=SimpleNamespace(
          buy_maximum_position=0.40,
          sell_minimum_position=0.60,
          equilibrium_exclusion_width=0.20,
        ),
        trend_pullback=SimpleNamespace(
          buy_maximum_position=0.70,
          sell_minimum_position=0.30,
        ),
        breakout_retest=SimpleNamespace(allow_directional_expansion=True),
      ),
    ),
  )


def _context(
  *,
  price: float,
  direction: str = "BUY",
  ask: float | None = None,
  bid: float | None = None,
  m15_low: float = RANGE_LOW,
  m15_high: float = RANGE_HIGH,
) -> object:
  return build_entry_location_context(
    execution_price=price,
    direction=direction,
    ask=ask,
    bid=bid,
    m15_range_low=m15_low,
    m15_range_high=m15_high,
  )


def _eval(
  *,
  strategy: str,
  direction: str,
  price: float,
  mode: str = "enforce",
  ask: float | None = None,
  bid: float | None = None,
  m15_low: float = RANGE_LOW,
  m15_high: float = RANGE_HIGH,
  breakout_evidence: dict | None = None,
):
  cfg = _location_cfg(mode)
  ctx = _context(
    price=price,
    direction=direction,
    ask=ask,
    bid=bid,
    m15_low=m15_low,
    m15_high=m15_high,
  )
  return evaluate_entry_location(
    strategy=strategy,
    direction=direction,
    context=ctx,
    cfg=cfg,
    breakout_evidence=breakout_evidence,
  )


def test_range_position_helper():
  assert range_position(4030.0, RANGE_LOW, RANGE_HIGH) == pytest.approx(0.3)
  assert range_position(4050.0, RANGE_LOW, RANGE_HIGH) == pytest.approx(0.5)
  assert range_position(4080.0, RANGE_LOW, RANGE_HIGH) == pytest.approx(0.8)
  assert range_position(4000.0, 4100.0, 4000.0) is None


@pytest.mark.parametrize(
  ("case_id", "strategy", "direction", "price", "mode", "expected_allowed", "expected_reason", "expected_would_block"),
  [
    # Reversal archetype — discount / premium / extreme
    ("01_buy_discount", "Zone Reaction", "BUY", 4030.0, "enforce", True, "entry_location_allowed", False),
    ("02_buy_premium", "Zone Reaction", "BUY", 4060.0, "enforce", False, "buy_in_premium", True),
    ("03_buy_extreme", "Zone Reaction", "BUY", 4070.0, "enforce", False, "buy_at_range_extreme", True),
    ("04_sell_premium", "Zone Reaction", "SELL", 4070.0, "enforce", True, "entry_location_allowed", False),
    ("05_sell_discount", "Zone Reaction", "SELL", 4040.0, "enforce", False, "sell_in_discount", True),
    ("06_sell_extreme", "Zone Reaction", "SELL", 4020.0, "enforce", False, "sell_at_range_extreme", True),
    # Range reversion — edge + equilibrium
    ("07_range_buy_edge", "Range Edge Scalp", "BUY", 4020.0, "enforce", True, "entry_location_allowed", False),
    ("08_range_buy_not_edge", "Range Edge Scalp", "BUY", 4065.0, "enforce", False, "range_buy_not_at_discount_edge", True),
    ("09_range_sell_edge", "Range Edge Scalp", "SELL", 4080.0, "enforce", True, "entry_location_allowed", False),
    ("10_range_sell_not_edge", "Range Edge Scalp", "SELL", 4035.0, "enforce", False, "range_sell_not_at_premium_edge", True),
    ("11_range_equilibrium", "Range Edge Scalp", "BUY", 4050.0, "enforce", False, "range_entry_near_equilibrium", True),
    # Trend pullback
    ("12_trend_buy_ok", "Trend Pullback", "BUY", 4060.0, "enforce", True, "entry_location_allowed", False),
    ("13_trend_buy_extreme", "Trend Pullback", "BUY", 4080.0, "enforce", False, "buy_at_range_extreme", True),
    ("14_trend_sell_ok", "Trend Pullback", "SELL", 4040.0, "enforce", True, "entry_location_allowed", False),
    ("15_trend_sell_extreme", "Trend Pullback", "SELL", 4020.0, "enforce", False, "sell_at_range_extreme", True),
    # Breakout — evidence required; name alone does not bypass premium
    ("16_breakout_no_evidence", "Break & Retest", "BUY", 4060.0, "enforce", False, "buy_in_premium", True),
    ("17_breakout_full_evidence", "Break & Retest", "BUY", 4060.0, "enforce", True, "location_override_accepted_breakout_retest", False),
    # Missing context policies
    ("18_missing_shadow", "Zone Reaction", "BUY", 4030.0, "shadow", True, "entry_location_context_missing", True),
    ("19_missing_enforce", "Zone Reaction", "BUY", 4030.0, "enforce", False, "entry_location_context_missing", True),
  ],
)
def test_entry_location_cases(
  case_id,
  strategy,
  direction,
  price,
  mode,
  expected_allowed,
  expected_reason,
  expected_would_block,
):
  evidence = None
  if case_id == "17_breakout_full_evidence":
    evidence = {
      "accepted_break": True,
      "correct_key_level_role": True,
      "retest_of_broken_level": True,
      "directionally_valid_close": True,
      "target_room_beyond_breakout": True,
    }
  if case_id in {"18_missing_shadow", "19_missing_enforce"}:
    ctx = build_entry_location_context(
      execution_price=price,
      direction=direction,
      m15_range_low=None,
      m15_range_high=None,
    )
    decision = evaluate_entry_location(
      strategy=strategy,
      direction=direction,
      context=ctx,
      cfg=_location_cfg(mode),
    )
  else:
    decision = _eval(
      strategy=strategy,
      direction=direction,
      price=price,
      mode=mode,
      breakout_evidence=evidence,
    )
  assert decision.allowed is expected_allowed, case_id
  assert decision.reason_code == expected_reason, case_id
  assert decision.would_block is expected_would_block, case_id


def test_buy_uses_ask_sell_uses_bid():
  ctx_buy = build_entry_location_context(
    execution_price=4030.0,
    direction="BUY",
    ask=4060.0,
    bid=4025.0,
    m15_range_low=RANGE_LOW,
    m15_range_high=RANGE_HIGH,
  )
  assert ctx_buy.execution_price == pytest.approx(4060.0)
  buy_decision = evaluate_entry_location(
    strategy="Zone Reaction",
    direction="BUY",
    context=ctx_buy,
    cfg=_location_cfg("enforce"),
  )
  assert buy_decision.allowed is False
  assert buy_decision.reason_code == "buy_in_premium"

  ctx_sell = build_entry_location_context(
    execution_price=4070.0,
    direction="SELL",
    ask=4075.0,
    bid=4040.0,
    m15_range_low=RANGE_LOW,
    m15_range_high=RANGE_HIGH,
  )
  assert ctx_sell.execution_price == pytest.approx(4040.0)
  sell_decision = evaluate_entry_location(
    strategy="Zone Reaction",
    direction="SELL",
    context=ctx_sell,
    cfg=_location_cfg("enforce"),
  )
  assert sell_decision.allowed is False
  assert sell_decision.reason_code == "sell_in_discount"


def test_malformed_range_treated_as_missing():
  ctx = build_entry_location_context(
    execution_price=4030.0,
    direction="BUY",
    m15_range_low=4100.0,
    m15_range_high=4000.0,
  )
  assert ctx.range_available is False
  decision = evaluate_entry_location(
    strategy="Zone Reaction",
    direction="BUY",
    context=ctx,
    cfg=_location_cfg("enforce"),
  )
  assert decision.allowed is False
  assert decision.reason_code == "entry_location_context_missing"


def test_raw_position_outside_unit_interval_preserved():
  ctx = build_entry_location_context(
    execution_price=4120.0,
    direction="BUY",
    m15_range_low=RANGE_LOW,
    m15_range_high=RANGE_HIGH,
  )
  assert ctx.effective_range_position_raw == pytest.approx(1.2)
  assert ctx.effective_range_position == pytest.approx(1.0)
  decision = evaluate_entry_location(
    strategy="Zone Reaction",
    direction="BUY",
    context=ctx,
    cfg=_location_cfg("enforce"),
  )
  assert decision.allowed is False
  assert decision.reason_code == "buy_at_range_extreme"
  assert decision.measured["effective_range_position_raw"] == pytest.approx(1.2)


def test_shadow_mode_allows_with_would_block():
  decision = _eval(
    strategy="Zone Reaction",
    direction="BUY",
    price=4060.0,
    mode="shadow",
  )
  assert decision.allowed is True
  assert decision.hard_block is False
  assert decision.would_block is True
  assert decision.reason_code == "buy_in_premium"


def test_technique_sell_at_extreme_is_allowed():
  # FVG/CRT often sit at dealing-range extremes — skip extreme_* hard block.
  decision = _eval(
    strategy="FVG",
    direction="SELL",
    price=4020.0,
    mode="enforce",
  )
  assert decision.allowed is True
  assert decision.reason_code == "entry_location_allowed"
  assert decision.would_block is False


def test_technique_sell_in_discount_still_blocked():
  decision = _eval(
    strategy="CRT",
    direction="SELL",
    price=4040.0,
    mode="enforce",
  )
  assert decision.allowed is False
  assert decision.reason_code == "sell_in_discount"
  assert decision.would_block is True


def test_confluence_buy_at_extreme_is_allowed():
  decision = _eval(
    strategy="Confluence Zone",
    direction="BUY",
    price=4070.0,
    mode="enforce",
  )
  assert decision.allowed is True
  assert decision.reason_code == "entry_location_allowed"
