"""Proves setup_lifecycle.py is actually wired into the live scanner path.

app/analysis/scanner.py::_advance_setup_to_confirmed is the wiring point:
every successfully-built StrategyMatch with a structural identity must reach
analysis:setup:{setup_id} = CONFIRMED, and repeated/overlapping confirmations
of the same canonical structure must share one thesis_id rather than each
minting a new one (the exact 23 Jul-style overlap this state machine exists
to prevent - see docs/adr-trade-plan-v7-boundary.md).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from app.analysis import scanner
from app.analysis.detectors import (
  DetectionContext,
  DetectionResult,
  DetectorSettings,
  IndicatorSet,
  StructureSet,
)
from app.analysis.structural_reaction_support import v7_thesis_id
from app.analysis.types import Zone
from app.autotrade.setup_lifecycle import (
  CONFIRMED,
  claim_active_thesis,
  load_setup,
)
from app.persistence import redis_state

NOW = int(datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc).timestamp())


def _context() -> DetectionContext:
  index = pd.date_range("2026-07-22 10:00", periods=20, freq="5min", tz="UTC")
  frame = pd.DataFrame({
    "open": [4116.0] * 20,
    "high": [4121.5] * 20,
    "low": [4113.1] * 20,
    "close": [4116.0] * 20,
    "volume": [100.0] * 20,
  }, index=index)
  indicators = IndicatorSet(pd.Series([1.2] * 20, index=index))
  structure = StructureSet(
    swings=[], bias="up", levels=[], equal_levels=[], fvg_zones=[],
    order_blocks=[], scalp_range=None,
  )
  return DetectionContext(
    symbol="XAU",
    tf="M5",
    frames={"M5": frame},
    indicators={"M5": indicators},
    structures={"M5": structure},
    htf_bias="up",
    settings=DetectorSettings(),
  )


def _result(
  *,
  touch_bar_ts: str = "1784721000",
  confirmation_bar_ts: str = "1784721300",
  entry_low: float = 4112.8,
  entry_high: float = 4113.4,
) -> DetectionResult:
  return DetectionResult(
    "Trend Pullback",
    "BUY",
    4113.0,
    Zone(entry_low, entry_high, "demand", score=8.0),
    4113.2,
    3,
    ["htf_uptrend", "demand_reaction"],
    mode="with_trend",
    confirmation="sweep_reclaim",
    structural_source="supply_demand",
    structural_id="xau-h1-demand-4112",
    structural_low=4112.5,
    structural_high=4113.6,
    structural_timeframe="H1",
    structural_kind="demand",
    confirmation_type="sweep_reclaim",
    confirmation_bar_ts=confirmation_bar_ts,
    touch_bar_ts=touch_bar_ts,
  )


@pytest.mark.asyncio
async def test_confirmed_match_reaches_confirmed_setup_state():
  client = redis_state.get_client()

  match = await scanner._sync_strategy_match(
    client, "XAU", "M5", "1784721300", _context(), [_result()],
  )

  assert match is not None
  assert match.thesis_id is not None
  record = await load_setup(client, match.match_id)
  assert record is not None
  assert record.state == CONFIRMED
  assert record.thesis_id == match.thesis_id
  assert record.source_structure_id == "xau-h1-demand-4112"


@pytest.mark.asyncio
async def test_repeated_scan_of_same_detection_is_idempotent():
  client = redis_state.get_client()

  first = await scanner._sync_strategy_match(
    client, "XAU", "M5", "1784721300", _context(), [_result()],
  )
  second = await scanner._sync_strategy_match(
    client, "XAU", "M5", "1784721300", _context(), [_result()],
  )

  assert first is not None and second is not None
  assert first.match_id == second.match_id
  assert first.thesis_id == second.thesis_id
  record = await load_setup(client, first.match_id)
  assert record.state == CONFIRMED


@pytest.mark.asyncio
async def test_two_confirmations_of_same_structure_share_one_thesis_id():
  # M5 bar A and M5 bar B: same canonical structure (structural_id is
  # identical), different touch/confirmation timestamps and a 0.01 zone
  # edge difference - this must NOT look like two different theses.
  client = redis_state.get_client()

  bar_a = await scanner._sync_strategy_match(
    client, "XAU", "M5", "1784721300",
    _context(), [_result(
      touch_bar_ts="1784721000", confirmation_bar_ts="1784721300",
      entry_low=4112.80, entry_high=4113.40,
    )],
  )
  bar_b = await scanner._sync_strategy_match(
    client, "XAU", "M5", "1784721600",
    _context(), [_result(
      touch_bar_ts="1784721300", confirmation_bar_ts="1784721600",
      entry_low=4112.81, entry_high=4113.41,
    )],
  )

  assert bar_a is not None and bar_b is not None
  assert bar_a.match_id != bar_b.match_id, (
    "different confirmation instances must still get distinct setup_ids"
  )
  assert bar_a.thesis_id == bar_b.thesis_id, (
    "same structural_id/family/direction must resolve to one thesis_id "
    "regardless of confirmation timestamp or a sub-pip zone edge difference"
  )
  expected = v7_thesis_id(
    symbol="XAU",
    strategy_family=bar_a.family,
    direction="BUY",
    structural_id="xau-h1-demand-4112",
  )
  assert bar_a.thesis_id == expected

  # Only the first setup to claim the thesis may own it.
  first_claim = await claim_active_thesis(
    client, symbol="XAU", thesis_id=bar_a.thesis_id, setup_id=bar_a.match_id,
  )
  second_claim = await claim_active_thesis(
    client, symbol="XAU", thesis_id=bar_b.thesis_id, setup_id=bar_b.match_id,
  )
  assert first_claim is True
  assert second_claim is False
