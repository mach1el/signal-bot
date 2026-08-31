"""Canonical scalp naming + shared M1/M5 context helper."""

from __future__ import annotations

import pandas as pd
import pytest

from app.scalping.models import ARCHETYPE_RANGE_SWEEP, STRATEGY_DISPLAY
from app.scalping.unified_context import (
  _m5_atr,
  build_scalp_context_and_micro,
)


pytestmark = pytest.mark.no_database


def test_strategy_display_maps_range_sweep_archetype():
  assert STRATEGY_DISPLAY[ARCHETYPE_RANGE_SWEEP] == "Range Sweep Scalp"


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
  atr = _m5_atr(m5, pip_size=0.1)
  assert atr > 0
  context, micro = build_scalp_context_and_micro(
    symbol="XAU",
    windows={"m1": m1, "m5": m5, "m15": m5, "h1": m5},
    price=2020.5,
    pip_size=0.1,
    now=int(m5.index[-1].timestamp()),
    cfg=None,
  )
  assert context is not None
  assert context.symbol == "XAU"
  assert micro is not None
