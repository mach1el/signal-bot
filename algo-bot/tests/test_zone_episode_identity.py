"""Required regression test C for the zone/M1 simplification refactor:
an ambiguous key level must never generate both a BUY and a SELL
opportunity, and must not resolve direction via an arbitrary
confluence-margin tiebreak - see key_level_reaction() in
app/analysis/detectors.py and docs/p0-simple-zone-m1-baseline-map.md
section 3.

Level/price values below were chosen empirically against the fixture
price action (not asserted from theory) to land in each of the three
deterministic branches: level below price, level above price, and price
inside the level's own band (proximal_band_atr=0.5 * atr=3 = 1.5).
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis import detectors
from app.analysis.structure import Level


def _df(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows, columns=["open", "high", "low", "close", "volume"], index=index,
  )


def _series(df: pd.DataFrame, value: float) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _ctx(df: pd.DataFrame, *, bias: str = "up", levels: list[Level]) -> detectors.DetectionContext:
  tf = "M5"
  structure = detectors.StructureSet(
    swings=[], bias=bias, levels=levels, equal_levels=[], fvg_zones=[],
    order_blocks=[], breaks=[], zones=[], liquidity_grabs=[],
    session_levels=[], dealing_range=None, trendlines=[],
  )
  return detectors.DetectionContext(
    symbol="XAU", tf=tf, frames={tf: df},
    indicators={tf: detectors.IndicatorSet(atr=_series(df, 3))},
    structures={tf: structure}, htf_bias=bias,
    settings=detectors.DetectorSettings(confluence_floor=2),
  )


def _flat_df(price: float) -> pd.DataFrame:
  # No wicks, no directional movement anywhere in the lookback window - a
  # genuinely flat market cannot confirm a reaction in either direction.
  return _df([(price, price, price, price, 100) for _ in range(5)])


def _buy_rejection_df() -> pd.DataFrame:
  # Last close (current price) = 109.
  return _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 102, 103, 100),
    (106, 110, 101, 109, 100),
  ])


def _sell_rejection_df() -> pd.DataFrame:
  # Last close (current price) = 103.
  return _df([
    (110, 112, 108, 110, 100),
    (109, 110, 101, 102, 100),
    (102, 107, 100, 106, 100),
    (106, 108, 104, 107, 100),
    (107, 112, 101, 103, 100),
  ])


def test_ambiguous_level_with_no_reaction_produces_no_opportunity():
  flat = _flat_df(105.0)
  level = Level(105.0, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(flat, bias="up", levels=[level]))

  assert result is None


def test_ambiguous_level_with_bullish_reclaim_produces_exactly_one_buy():
  # price=109, band=[107.5, 110.5] - 108.5 sits inside the level's own
  # band, so direction comes from whichever side confirms, not position.
  buy_df = _buy_rejection_df()
  level = Level(108.5, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(buy_df, bias="down", levels=[level]))

  assert result is not None
  assert result.direction == "BUY"
  assert result.key_level_role == "ambiguous"


def test_ambiguous_level_with_bearish_reclaim_produces_exactly_one_sell_in_a_separate_episode():
  # price=103, band=[101.5, 104.5] - 103.4 sits inside the level's own
  # band, same "inside the band" shape as the BUY case above, but this
  # fixture's actual price action only confirms SELL there.
  sell_df = _sell_rejection_df()
  level = Level(103.4, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(sell_df, bias="up", levels=[level]))

  assert result is not None
  assert result.direction == "SELL"
  assert result.key_level_role == "ambiguous"
  # A distinct underlying level/price action produces a distinct
  # structural_id - never the same episode as the BUY case above.
  buy_df = _buy_rejection_df()
  buy_level = Level(108.5, "reaction", touches=3, strength=3)
  buy_result = detectors.key_level_reaction(
    _ctx(buy_df, bias="down", levels=[buy_level]),
  )
  assert buy_result is not None
  assert buy_result.structural_id != result.structural_id


def test_level_deterministically_below_price_never_evaluates_sell():
  # Level clearly below current price (109) and outside its own band -
  # support hypothesis only, never a confluence-margin coin flip.
  buy_df = _buy_rejection_df()
  level = Level(105.0, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(buy_df, bias="down", levels=[level]))

  assert result is not None
  assert result.direction == "BUY"


def test_level_deterministically_above_price_never_evaluates_buy():
  # Level clearly above current price (103) and outside its own band -
  # resistance hypothesis only.
  sell_df = _sell_rejection_df()
  level = Level(107.0, "reaction", touches=3, strength=3)

  result = detectors.key_level_reaction(_ctx(sell_df, bias="up", levels=[level]))

  assert result is not None
  assert result.direction == "SELL"
