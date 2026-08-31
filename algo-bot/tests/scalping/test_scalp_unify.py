"""Canonical scalp naming + shared M1/M5 context helper."""

from __future__ import annotations

import pandas as pd
import pytest

from app.scalping.models import ARCHETYPE_RANGE_SWEEP, STRATEGY_DISPLAY
from app.scalping.unified_context import (
  _m5_atr,
  build_scalp_context_and_micro,
  derive_scalp_analysis_labels,
)


pytestmark = pytest.mark.no_database


def _trending_ohlc(*, bars: int, direction: str, start: float = 2000.0) -> pd.DataFrame:
  rows = []
  index = []
  price = start
  step = 2.0 if direction == "up" else -2.0
  for i in range(bars):
    o = price
    c = price + step
    h = max(o, c) + 0.5
    low = min(o, c) - 0.5
    rows.append({"open": o, "high": h, "low": low, "close": c, "volume": 1.0})
    index.append(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i))
    price = c
  return pd.DataFrame(rows, index=index)


def test_strategy_display_maps_range_sweep_archetype():
  assert STRATEGY_DISPLAY[ARCHETYPE_RANGE_SWEEP] == "Range Sweep Scalp"


def test_derive_scalp_analysis_labels_trending_h1():
  h1 = _trending_ohlc(bars=55, direction="up")
  m15 = h1.iloc[-30:].copy()
  m5 = h1.iloc[-20:].copy()
  htf_bias, m5_structure, regime_kind = derive_scalp_analysis_labels(
    {"h1": h1, "m15": m15, "m5": m5},
    pip_size=0.1,
  )
  assert htf_bias in {"up", "down"}
  assert m5_structure
  assert "Regime(" not in regime_kind


def test_derive_scalp_analysis_labels_warmup_guard():
  h1 = _trending_ohlc(bars=20, direction="up")
  m15 = h1.copy()
  m5 = h1.copy()
  htf_bias, _, _ = derive_scalp_analysis_labels(
    {"h1": h1, "m15": m15, "m5": m5},
    pip_size=0.1,
  )
  assert htf_bias == "unknown"


def test_derive_scalp_analysis_labels_empty_windows():
  assert derive_scalp_analysis_labels({}, pip_size=0.1) == (
    "unknown",
    "unknown",
    "unknown",
  )


def test_build_scalp_context_and_micro_derives_labels_when_omitted():
  h1 = _trending_ohlc(bars=55, direction="up")
  m5 = _trending_ohlc(bars=30, direction="up", start=2100.0)
  m1_idx = pd.date_range("2026-01-01", periods=60, freq="1min", tz="UTC")
  m1 = pd.DataFrame({
    "open": [2120.0] * 60,
    "high": [2121.0] * 60,
    "low": [2119.0] * 60,
    "close": [2120.5] * 60,
  }, index=m1_idx)
  context, micro, analysis_labels_ms = build_scalp_context_and_micro(
    symbol="XAU",
    windows={"m1": m1, "m5": m5, "m15": m5, "h1": h1},
    price=2120.5,
    pip_size=0.1,
    now=int(m5.index[-1].timestamp()),
    cfg=None,
  )
  assert context is not None
  assert micro is not None
  assert analysis_labels_ms >= 0.0
  assert (context.htf_bias, context.m5_structure, context.regime) != (
    "unknown",
    "range",
    "range",
  )


def test_build_scalp_context_and_micro_explicit_labels_win():
  idx = pd.date_range("2026-01-01", periods=30, freq="5min", tz="UTC")
  m5 = pd.DataFrame({
    "open": [2000.0 + i for i in range(30)],
    "high": [2001.0 + i for i in range(30)],
    "low": [1999.0 + i for i in range(30)],
    "close": [2000.5 + i for i in range(30)],
  }, index=idx)
  m1_idx = pd.date_range("2026-01-01", periods=60, freq="1min", tz="UTC")
  m1 = pd.DataFrame({
    "open": [2020.0] * 60,
    "high": [2021.0] * 60,
    "low": [2019.0] * 60,
    "close": [2020.5] * 60,
  }, index=m1_idx)
  context, micro, _ = build_scalp_context_and_micro(
    symbol="XAU",
    windows={"m1": m1, "m5": m5, "m15": m5, "h1": m5},
    price=2020.5,
    pip_size=0.1,
    now=int(m5.index[-1].timestamp()),
    cfg=None,
    htf_bias="down",
    m5_structure="bearish",
    regime="trend",
  )
  assert context is not None
  assert context.htf_bias == "down"
  assert context.m5_structure == "bearish"
  assert context.regime == "trend"
  assert micro is not None
  atr = _m5_atr(m5, pip_size=0.1)
  assert atr > 0


def test_build_scalp_context_and_micro_from_windows():
  idx = pd.date_range("2026-01-01", periods=30, freq="5min", tz="UTC")
  m5 = pd.DataFrame({
    "open": [2000.0 + i for i in range(30)],
    "high": [2001.0 + i for i in range(30)],
    "low": [1999.0 + i for i in range(30)],
    "close": [2000.5 + i for i in range(30)],
  }, index=idx)
  m1_idx = pd.date_range("2026-01-01", periods=60, freq="1min", tz="UTC")
  m1 = pd.DataFrame({
    "open": [2020.0] * 60,
    "high": [2021.0] * 60,
    "low": [2019.0] * 60,
    "close": [2020.5] * 60,
  }, index=m1_idx)
  context, micro, analysis_labels_ms = build_scalp_context_and_micro(
    symbol="XAU",
    windows={"m1": m1, "m5": m5, "m15": m5, "h1": m5},
    price=2020.5,
    pip_size=0.1,
    now=int(m5.index[-1].timestamp()),
    cfg=None,
    htf_bias="unknown",
    m5_structure="range",
    regime="range",
  )
  assert context is not None
  assert context.symbol == "XAU"
  assert micro is not None
  assert analysis_labels_ms == 0.0
