from __future__ import annotations

from dataclasses import replace

import pandas as pd
import pytest
from pydantic import ValidationError

from app.analysis.detectors import (
  ConfluenceFactors,
  DetectionContext,
  DetectorSettings,
  IndicatorSet,
  StructureSet,
  _factors_for_confirmation,
  _raw_factor_score,
  trendline_reaction,
)
from app.analysis.engine import AnalysisSettings, _nested_cfg_from_analysis_settings
from app.analysis.structural_reaction_support import (
  CONFIRM_ENGULFING,
  CONFIRM_REJECTION_CHOCH,
  CONFIRM_STRONG_RECLAIM,
  CONFIRM_SWEEP_RECLAIM,
  CONFIRM_WICK_REJECTION,
  ReactionConfirmation,
)
from app.analysis.types import Swing, Zone
from app.analysis.trendlines import Trendline, _dedup, trendlines, value_at
from app.analysis.zones import TRENDLINE_SCORE, score_zones
from app.configuration.models.analysis import AnalysisTrendlinesConfig


pytestmark = pytest.mark.no_database


def _cfg(settings: AnalysisSettings | None = None):
  return _nested_cfg_from_analysis_settings(settings or AnalysisSettings())


def _atr(df: pd.DataFrame, value: float = 1.0) -> pd.Series:
  return pd.Series([value] * len(df), index=df.index)


def _support_df(length: int = 30, slope: float = 0.1, base: float = 100.0) -> pd.DataFrame:
  rows = []
  for index in range(length):
    support = base + slope * index
    rows.append((support + 0.5, support + 1.2, support, support + 0.7, 100))
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-10", periods=length, freq="5min", tz="UTC"),
  )


def _resistance_df(
  length: int = 30,
  slope: float = -0.1,
  base: float = 110.0,
) -> pd.DataFrame:
  rows = []
  for index in range(length):
    resistance = base + slope * index
    rows.append((resistance - 0.5, resistance, resistance - 1.2, resistance - 0.7, 100))
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-10", periods=length, freq="5min", tz="UTC"),
  )


def _support_swings(
  indexes: tuple[int, ...] = (1, 11, 21),
  slope: float = 0.1,
  base: float = 100.0,
) -> list[Swing]:
  return [Swing(index, "low", base + slope * index) for index in indexes]


def _resistance_swings(
  indexes: tuple[int, ...] = (1, 11, 21),
  slope: float = -0.1,
  base: float = 110.0,
) -> list[Swing]:
  return [Swing(index, "high", base + slope * index) for index in indexes]


def _tl(
  kind: str,
  point_idx: tuple[int, ...],
  slope: float,
  intercept: float,
  touches: int,
  broken: bool,
  break_index: int | None,
  **kwargs,
) -> Trendline:
  defaults = {
    "fit_error_atr": 0.0,
    "violations": 0,
    "bars_since_last_touch": 0,
    "span_bars": (point_idx[-1] - point_idx[0]) if point_idx else 0,
    "exhausted": False,
  }
  defaults.update(kwargs)
  return Trendline(
    kind,
    point_idx,
    slope,
    intercept,
    touches,
    broken,
    break_index,
    **defaults,
  )


# --- construction cases 1-14 -------------------------------------------------


def test_three_ascending_lows_fit_one_support_line():
  df = _support_df()
  lines = trendlines(_support_swings(), df, _atr(df), _cfg())
  assert len(lines) == 1
  assert lines[0].kind == "support"
  assert lines[0].touches == 3
  assert lines[0].point_idx == (1, 11, 21)
  assert value_at(lines[0], 29) == pytest.approx(102.9)
  assert lines[0].fit_error_atr == pytest.approx(0.0)
  assert lines[0].exhausted is False


def test_ascending_highs_reject_resistance_and_emit_counter_trend():
  df = _resistance_df(slope=0.1, base=100.0)
  swings = _resistance_swings(slope=0.1, base=100.0)
  events: list[tuple[str, str, dict[str, str]]] = []
  lines = trendlines(
    swings,
    df,
    _atr(df),
    _cfg(),
    symbol="XAU",
    timeframe="M5",
    metric_sink=lambda name, symbol, labels: events.append((name, symbol, labels)),
  )
  assert lines == []
  assert any(name == "trendline_rejected_counter_trend" for name, _, _ in events)
  assert events[0][1] == "XAU"
  assert events[0][2] == {"tf": "M5"}


def test_descending_lows_reject_support():
  df = _support_df(slope=-0.1, base=110.0)
  swings = _support_swings(slope=-0.1, base=110.0)
  assert trendlines(swings, df, _atr(df), _cfg()) == []


def test_three_descending_highs_fit_one_resistance_line():
  df = _resistance_df()
  lines = trendlines(_resistance_swings(), df, _atr(df), _cfg())
  assert len(lines) == 1
  assert lines[0].kind == "resistance"
  assert lines[0].touches == 3
  assert lines[0].point_idx == (1, 11, 21)


def test_slope_below_minimum_is_rejected():
  settings = AnalysisSettings(tl_min_slope_atr=0.05, tl_max_slope_atr=0.15)
  df = _support_df(slope=0.03)
  swings = _support_swings(slope=0.03)
  assert trendlines(swings, df, _atr(df), _cfg(settings)) == []


def test_span_below_minimum_is_rejected():
  settings = AnalysisSettings(tl_min_span_bars=25)
  df = _support_df()
  assert trendlines(_support_swings(), df, _atr(df), _cfg(settings)) == []


def test_touch_spacing_collapses_cluster_to_earliest():
  settings = AnalysisSettings(
    tl_min_touch_spacing_bars=5,
    tl_min_touches=3,
    tl_max_touches=5,
  )
  df = _support_df(length=35)
  # 2 and 3 are within spacing of 1; keep earliest of each cluster → 1, 12, 24.
  swings = _support_swings((1, 2, 3, 12, 24))
  lines = trendlines(swings, df, _atr(df), _cfg(settings))
  assert len(lines) == 1
  assert lines[0].point_idx == (1, 12, 24)
  assert lines[0].touches == 3


def test_stale_last_touch_is_rejected():
  settings = AnalysisSettings(tl_max_bars_since_last_touch=5)
  df = _support_df(length=40)
  swings = _support_swings((1, 11, 21))
  assert trendlines(swings, df, _atr(df), _cfg(settings)) == []


def test_fit_error_reject_and_value_on_accepted_line():
  settings = AnalysisSettings(tl_max_fit_error_atr=0.05, tl_tol_atr=0.4)
  df = _support_df()
  # Perfect line still accepted with near-zero fit error.
  accepted = trendlines(_support_swings(), df, _atr(df), _cfg(settings))
  assert len(accepted) == 1
  assert accepted[0].fit_error_atr == pytest.approx(0.0)

  noisy = [
    Swing(1, "low", 100.1),
    Swing(11, "low", 101.4),
    Swing(21, "low", 101.8),
  ]
  assert trendlines(noisy, df, _atr(df), _cfg(settings)) == []


def test_wick_violations_without_close_through():
  settings = AnalysisSettings(tl_max_violations=1, tl_pierce_tolerance_atr=0.5)
  df = _support_df()
  # One wick pierce below the line without close-through.
  line_at_8 = 100.8
  df.iloc[8, df.columns.get_loc("low")] = line_at_8 - 0.8
  df.iloc[8, df.columns.get_loc("close")] = line_at_8 + 0.2
  one = trendlines(_support_swings(), df, _atr(df), _cfg(settings))
  assert len(one) == 1
  assert one[0].violations == 1
  assert one[0].broken is False

  # Second violation rejects.
  df.iloc[9, df.columns.get_loc("low")] = 100.9 - 0.8
  df.iloc[9, df.columns.get_loc("close")] = 100.9 + 0.2
  assert trendlines(_support_swings(), df, _atr(df), _cfg(settings)) == []


def test_close_through_sets_broken_with_break_index():
  df = _support_df()
  df.iloc[25, df.columns.get_loc("low")] = 99.0
  df.iloc[25, df.columns.get_loc("close")] = 99.5
  line = trendlines(_support_swings(), df, _atr(df), _cfg())[0]
  assert line.broken is True
  assert line.break_index == 25


def test_touch_and_pierce_tolerances_are_independent():
  # Wide touch band still finds touches; tight pierce rejects mid-span close.
  settings = AnalysisSettings(tl_tol_atr=0.5, tl_pierce_tolerance_atr=0.1)
  df = _support_df()
  df.iloc[15, df.columns.get_loc("low")] = 99.0
  df.iloc[15, df.columns.get_loc("close")] = 101.3  # below line(~101.5)-0.1
  assert trendlines(_support_swings(), df, _atr(df), _cfg(settings)) == []

  # Tight touch misses noisy pivots; wide pierce would still contain.
  tight_touch = AnalysisSettings(tl_tol_atr=0.05, tl_pierce_tolerance_atr=1.0)
  noisy = [
    Swing(1, "low", 100.2),
    Swing(11, "low", 101.1),
    Swing(21, "low", 102.2),
  ]
  assert trendlines(noisy, df, _atr(df), _cfg(tight_touch)) == []


def test_exhausted_line_still_returned_from_construction():
  settings = AnalysisSettings(tl_max_touches=3, tl_min_touches=3)
  df = _support_df(length=40)
  swings = _support_swings((1, 11, 21, 31))
  lines = trendlines(swings, df, _atr(df), _cfg(settings))
  assert len(lines) == 1
  assert lines[0].touches == 4
  assert lines[0].exhausted is True


def test_dedup_prefers_tighter_fit_when_touches_tie():
  loose = _tl(
    "support", (1, 11, 21), 0.10, 100.0, 3, False, None,
    fit_error_atr=0.12, span_bars=20, bars_since_last_touch=2,
  )
  tight = _tl(
    "support", (1, 12, 22), 0.10, 100.02, 3, False, None,
    fit_error_atr=0.01, span_bars=21, bars_since_last_touch=1,
  )
  kept = _dedup([loose, tight], last_bar=29, atr=1.0, maximum_touches=4)
  assert kept == [tight]


def test_mid_span_close_beyond_pierce_rejects_candidate():
  df = _support_df()
  df.iloc[15, df.columns.get_loc("low")] = 99.0
  df.iloc[15, df.columns.get_loc("close")] = 99.2
  assert trendlines(_support_swings(), df, _atr(df), _cfg()) == []


def test_slope_beyond_atr_bound_is_rejected():
  df = _support_df(slope=0.2)
  steep = _support_swings(slope=0.2)
  assert trendlines(steep, df, _atr(df), _cfg()) == []


def test_zone_score_rewards_unbroken_trendline_confluence():
  zone = Zone(102.7, 103.1, "demand", source="supply_demand")
  line = _tl("support", (1, 11, 21), 0.1, 100, 3, False, None)
  broken = replace(line, broken=True, break_index=25)

  plain = score_zones([zone], [], [], 0)[0]
  scored = score_zones(
    [zone],
    [],
    [],
    0,
    trendlines=[line],
    bar_index=29,
  )[0]
  ignored = score_zones(
    [zone],
    [],
    [],
    0,
    trendlines=[broken],
    bar_index=29,
  )[0]

  assert scored.score == pytest.approx(plain.score + TRENDLINE_SCORE)
  assert "TL confluence" in scored.score_reasons
  assert ignored.score == plain.score


# --- config cases 21-23 ------------------------------------------------------


def test_minimum_slope_atr_must_be_less_than_maximum():
  with pytest.raises(ValidationError):
    AnalysisTrendlinesConfig(minimum_slope_atr=0.2, maximum_slope_atr=0.15)


def test_minimum_touches_must_not_exceed_maximum():
  with pytest.raises(ValidationError):
    AnalysisTrendlinesConfig(minimum_touches=5, maximum_touches=4)


def test_analysis_settings_flat_shim_agrees_with_nested_config():
  nested = AnalysisTrendlinesConfig()
  settings = AnalysisSettings()
  mapped = _nested_cfg_from_analysis_settings(settings).analysis.trendlines
  assert mapped.tolerance_atr == nested.tolerance_atr == settings.tl_tol_atr
  assert mapped.pierce_tolerance_atr == nested.pierce_tolerance_atr == (
    settings.tl_pierce_tolerance_atr
  )
  assert mapped.minimum_slope_atr == nested.minimum_slope_atr == settings.tl_min_slope_atr
  assert mapped.maximum_slope_atr == nested.maximum_slope_atr == settings.tl_max_slope_atr
  assert mapped.minimum_touches == nested.minimum_touches == settings.tl_min_touches
  assert mapped.maximum_touches == nested.maximum_touches == settings.tl_max_touches
  assert mapped.minimum_span_bars == nested.minimum_span_bars == settings.tl_min_span_bars
  assert mapped.minimum_touch_spacing_bars == nested.minimum_touch_spacing_bars == (
    settings.tl_min_touch_spacing_bars
  )
  assert mapped.maximum_bars_since_last_touch == nested.maximum_bars_since_last_touch == (
    settings.tl_max_bars_since_last_touch
  )
  assert mapped.maximum_fit_error_atr == nested.maximum_fit_error_atr == (
    settings.tl_max_fit_error_atr
  )
  assert mapped.maximum_violations == nested.maximum_violations == settings.tl_max_violations


# --- detector cases 15-20 ----------------------------------------------------


def _buy_rejection_df() -> pd.DataFrame:
  return pd.DataFrame(
    [
      (100, 101, 98, 100, 100),
      (101, 108, 100, 107, 100),
      (107, 109, 103, 104, 100),
      (104, 106, 102, 103, 100),
      (106, 110, 101, 109, 100),
    ],
    columns=["open", "high", "low", "close", "volume"],
    index=pd.date_range("2026-07-10", periods=5, freq="5min", tz="UTC"),
  )


def _tl_ctx(
  df: pd.DataFrame,
  line: Trendline,
  *,
  bias: str = "up",
  settings: DetectorSettings | None = None,
  metric_sink=None,
) -> DetectionContext:
  tf = "M5"
  structure = StructureSet(
    swings=[],
    bias=bias,
    levels=[],
    equal_levels=[],
    fvg_zones=[],
    order_blocks=[],
    trendlines=[line],
  )
  return DetectionContext(
    symbol="XAU",
    tf=tf,
    frames={tf: df},
    indicators={tf: IndicatorSet(atr=pd.Series([3.0] * len(df), index=df.index))},
    structures={tf: structure},
    htf_bias=bias,
    settings=settings or DetectorSettings(confluence_floor=2),
    metric_sink=metric_sink,
  )


def _fake_confirmation(confirmation_type: str) -> ReactionConfirmation:
  return ReactionConfirmation(
    confirmation_type=confirmation_type,
    touch_bar_ts="t0",
    confirmation_bar_ts="t1",
    touch_index=4,
    confirmation_index=4,
    has_choch=confirmation_type == CONFIRM_REJECTION_CHOCH,
  )


def _mapped_factors(confirmation_type: str) -> ConfluenceFactors:
  factors = ConfluenceFactors(
    htf_aligned=True,
    touches=3,
    wick_rejection=confirmation_type == CONFIRM_WICK_REJECTION,
    displacement_grade=confirmation_type == CONFIRM_ENGULFING,
    structural_agreement=confirmation_type in {
      CONFIRM_SWEEP_RECLAIM,
      CONFIRM_STRONG_RECLAIM,
    },
  )
  return _factors_for_confirmation(factors, _fake_confirmation(confirmation_type))


def test_wick_rejection_factor_only_for_wick_confirmation():
  wick = _mapped_factors(CONFIRM_WICK_REJECTION)
  assert wick.wick_rejection is True
  for other in (
    CONFIRM_SWEEP_RECLAIM,
    CONFIRM_REJECTION_CHOCH,
    CONFIRM_STRONG_RECLAIM,
    CONFIRM_ENGULFING,
  ):
    assert _mapped_factors(other).wick_rejection is False


def test_displacement_grade_only_for_engulfing():
  assert _mapped_factors(CONFIRM_ENGULFING).displacement_grade is True
  for other in (
    CONFIRM_WICK_REJECTION,
    CONFIRM_SWEEP_RECLAIM,
    CONFIRM_REJECTION_CHOCH,
    CONFIRM_STRONG_RECLAIM,
  ):
    assert _mapped_factors(other).displacement_grade is False


def test_structural_agreement_for_reclaim_and_choch():
  assert _mapped_factors(CONFIRM_SWEEP_RECLAIM).structural_agreement is True
  assert _mapped_factors(CONFIRM_STRONG_RECLAIM).structural_agreement is True
  choch = _mapped_factors(CONFIRM_REJECTION_CHOCH)
  assert choch.structural_agreement is True
  assert choch.choch is True
  assert _mapped_factors(CONFIRM_WICK_REJECTION).structural_agreement is False


def test_different_confirmation_types_change_confluence_score(monkeypatch):
  df = _buy_rejection_df()
  line = _tl("support", (0, 2, 4), 0.0, 105.0, 3, False, None)
  scores: dict[str, int] = {}

  for confirmation_type in (CONFIRM_WICK_REJECTION, CONFIRM_REJECTION_CHOCH):
    monkeypatch.setattr(
      "app.analysis.detectors.evaluate_structural_reaction",
      lambda *args, confirmation_type=confirmation_type, **kwargs: (
        _fake_confirmation(confirmation_type)
      ),
    )
    result = trendline_reaction(_tl_ctx(df, line, bias="up"))
    assert result is not None
    scores[confirmation_type] = result.confluence

  assert scores[CONFIRM_WICK_REJECTION] != scores[CONFIRM_REJECTION_CHOCH]
  assert _raw_factor_score(_mapped_factors(CONFIRM_WICK_REJECTION)) != (
    _raw_factor_score(_mapped_factors(CONFIRM_REJECTION_CHOCH))
  )


def test_reject_exhausted_skips_and_emits_metric(monkeypatch):
  df = _buy_rejection_df()
  line = _tl(
    "support", (0, 2, 4), 0.0, 105.0, 4, False, None, exhausted=True,
  )
  events: list[str] = []
  monkeypatch.setattr(
    "app.analysis.detectors.evaluate_structural_reaction",
    lambda *args, **kwargs: _fake_confirmation(CONFIRM_WICK_REJECTION),
  )
  result = trendline_reaction(
    _tl_ctx(
      df,
      line,
      settings=DetectorSettings(
        confluence_floor=2,
        trendline_reject_exhausted=True,
      ),
      metric_sink=lambda name, symbol, labels: events.append(name),
    ),
  )
  assert result is None
  assert events == ["trendline_skipped_exhausted"]


def test_require_htf_aligned_skips_counter_bias(monkeypatch):
  df = _buy_rejection_df()
  line = _tl("support", (0, 2, 4), 0.0, 105.0, 3, False, None)
  events: list[str] = []
  monkeypatch.setattr(
    "app.analysis.detectors.evaluate_structural_reaction",
    lambda *args, **kwargs: _fake_confirmation(CONFIRM_WICK_REJECTION),
  )
  blocked = trendline_reaction(
    _tl_ctx(
      df,
      line,
      bias="down",
      settings=DetectorSettings(
        confluence_floor=1,
        trendline_require_htf_aligned=True,
      ),
      metric_sink=lambda name, symbol, labels: events.append(name),
    ),
  )
  assert blocked is None
  assert events == ["trendline_skipped_htf_misaligned"]

  admitted = trendline_reaction(
    _tl_ctx(
      df,
      line,
      bias="down",
      settings=DetectorSettings(
        confluence_floor=1,
        trendline_require_htf_aligned=False,
      ),
    ),
  )
  assert admitted is not None
  assert admitted.direction == "BUY"
