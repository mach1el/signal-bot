"""PR-F detector logic corrections — one focused test per sub-item."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest

from app.analysis import detectors
from app.analysis.structural_reaction_support import (
  CONFIRM_REJECTION_CHOCH,
  engulfing_on_bar,
  evaluate_structural_reaction,
  momentum_impulse_structural_id,
)
from app.analysis.structure import Level, Swing, Zone
from app.analysis.types import Grab, Pool
from app.analysis.scalp_ranges import ScalpBarrier, ScalpRange


pytestmark = pytest.mark.no_database


def _df(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-07-10", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=index,
  )


def _series(df: pd.DataFrame, value: float) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _indicators(df: pd.DataFrame, *, atr: float = 3) -> detectors.IndicatorSet:
  return detectors.IndicatorSet(atr=_series(df, atr))


def _ctx(
  df: pd.DataFrame,
  *,
  bias: str = "up",
  levels: list[Level] | None = None,
  zones: list[Zone] | None = None,
  swings: list[Swing] | None = None,
  grabs: list[Grab] | None = None,
  scalp_barriers: list[ScalpBarrier] | None = None,
  scalp_range: ScalpRange | None = None,
  settings: detectors.DetectorSettings | None = None,
) -> detectors.DetectionContext:
  structure = detectors.StructureSet(
    swings=swings or [],
    bias=bias,
    levels=levels or [],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    breaks=[],
    zones=zones or [],
    liquidity_grabs=grabs or [],
    session_levels=[],
    dealing_range=None,
    trendlines=[],
    box_break=None,
    liquidity_pools=[],
    scalp_barriers=scalp_barriers or [],
    scalp_range=scalp_range,
  )
  return detectors.DetectionContext(
    symbol="XAU",
    tf="M5",
    frames={"M5": df},
    indicators={"M5": _indicators(df, atr=2.0)},
    structures={"M5": structure},
    htf_bias=bias,
    settings=settings or detectors.DetectorSettings(confluence_floor=2),
  )


def test_f1_snap_back_fires_on_impulse_extension_not_zone_distance():
  # sweep low — impossible under zone-based extension, valid under impulse.
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 98, 100, 100),   # sweep low 98
    (100, 104, 99.5, 103.5, 100),  # touch + bullish close inside zone
  ])
  zone = Zone(100, 102, "demand", source="supply_demand")
  grab = Grab(Pool("sell", 100, 0.1, 2), 3, "bull", df.index[3], "A")
  ctx = _ctx(
    df,
    zones=[zone],
    swings=[Swing(3, "low", 98.0)],
    grabs=[grab],
    settings=detectors.DetectorSettings(
      confluence_floor=2,
      snap_atr_mult=1.5,
      snap_back_extension_source="impulse",
      structural_reaction_lookback_bars=3,
    ),
  )

  result = detectors.snap_back(ctx)

  assert result is not None
  assert result.setup == "Snap-Back"
  assert result.direction == "BUY"

  zone_only = replace(
    ctx,
    settings=replace(ctx.settings, snap_back_extension_source="zone"),
  )
  assert detectors.snap_back(zone_only) is None


def test_f2_rejection_choch_carries_has_choch_and_boosts_confluence():
  df = _df([
    (100, 101, 98, 100, 100),
    (101, 108, 100, 107, 100),
    (107, 109, 103, 104, 100),
    (104, 106, 100.5, 103, 100),
    (106, 110, 101, 109, 100),
  ])
  conf = evaluate_structural_reaction(
    df,
    direction="BUY",
    low=100,
    high=106,
    lookback_bars=3,
    has_choch=True,
    atr=2.0,
  )
  assert conf is not None
  assert conf.confirmation_type == CONFIRM_REJECTION_CHOCH
  assert conf.has_choch is True

  base = detectors._confluence_from_factors(
    detectors.ConfluenceFactors(htf_aligned=True),
  )
  enriched = detectors._confluence_from_factors(
    detectors._factors_for_confirmation(
      detectors.ConfluenceFactors(htf_aligned=True),
      conf,
    ),
  )
  assert enriched > base


def test_f3_engulfing_requires_opposite_prior_and_minimum_range():
  same_colour_prior = pd.Series(
    {"open": 102.0, "high": 103.5, "low": 101.8, "close": 103.0},
  )
  engulf = pd.Series({"open": 102.5, "high": 106.5, "low": 102.0, "close": 106.0})
  assert engulfing_on_bar(engulf, same_colour_prior, "BUY", atr=2.0) is False

  opposite_prior = pd.Series(
    {"open": 103.0, "high": 104.0, "low": 102.0, "close": 102.5},
  )
  tiny_engulf = pd.Series({"open": 102.4, "high": 102.8, "low": 102.2, "close": 102.7})
  assert engulfing_on_bar(
    tiny_engulf, opposite_prior, "BUY", atr=2.0, minimum_range_atr=0.5,
  ) is False
  assert engulfing_on_bar(
    engulf, opposite_prior, "BUY", atr=2.0, minimum_range_atr=0.5,
  ) is True


def test_f4_range_edge_scalp_rejects_stale_confirmation_outside_base_window():
  # Touch + wick rejection at bar 6; last three bars have no confirmation.
  df = _df([
    (105, 107, 103, 106, 100),
    (106, 108, 104, 105, 100),
    (105, 107, 103, 106, 100),
    (106, 108, 104, 106, 100),
    (105, 107, 103, 104, 100),
    (104, 106, 102, 104, 100),
    (109.5, 110.5, 108.5, 109.0, 100),  # upper touch + bearish rejection
    (108, 109, 107, 108, 100),
    (107, 108, 106, 107, 100),
    (106, 107, 105, 106, 100),
  ])
  upper = ScalpBarrier(
    "resistance", 110, 109.7, 110.3, 3, 3, 0, 6, ["wick ×3"], 9,
  )
  lower = ScalpBarrier(
    "support", 100, 99.7, 100.3, 3, 3, 0, 0, ["wick ×3"], 9,
  )
  scalp_range = ScalpRange(lower, upper, 105, 5, 48)
  ctx = _ctx(
    df,
    bias="range",
    scalp_barriers=[lower, upper],
    scalp_range=scalp_range,
    settings=detectors.DetectorSettings(
      confluence_floor=2,
      structural_reaction_lookback_bars=3,
      range_scalp_lookback=48,
      range_scalp_min_room_atr=0.5,
    ),
  )

  assert detectors.range_edge_scalp(ctx) is None


def test_f5_momentum_ride_emits_structural_identity_without_min_touch_filter():
  df = _df([
    (100, 102, 98, 100, 100),
    (101, 104, 100, 103, 100),
    (103, 106, 102, 105, 100),
    (105, 108, 104, 107, 100),
    (107, 111, 106.2, 110.5, 100),
  ])
  broken_high = Swing(3, "high", 108.0)
  ctx = _ctx(
    df,
    levels=[Level(108.8, "reaction", band=0.1, touches=4)],
    swings=[broken_high, Swing(2, "low", 102.0)],
    settings=detectors.DetectorSettings(
      confluence_floor=2,
      key_level_min_touches=3,
    ),
  )

  result = detectors.momentum_ride(ctx)

  assert result is not None
  assert result.structural_source == "momentum_impulse"
  assert result.structural_id == momentum_impulse_structural_id(
    "XAU", "M5", "BUY", broken_high,
  )

  # Momentum's level fallback is a proximity anchor after impulse break —
  # key_level_min_touches must not apply (fresh 1-touch levels remain valid).
  weak_level_ctx = replace(
    ctx,
    structures={
      "M5": replace(
        ctx.structures["M5"],
        zones=[],
        levels=[Level(108.8, "reaction", band=0.1, touches=1)],
      ),
    },
  )
  weak = detectors.momentum_ride(weak_level_ctx)
  assert weak is not None
  assert weak.key_level == 108.8
