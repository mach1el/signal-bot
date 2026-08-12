"""Unit tests for technique_geometry predicates and confluence band builder."""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.confluence_zone import build_confluence_bands
from app.analysis.technique_geometry import (
  TECHNIQUE_FVG,
  TECHNIQUE_IFVG,
  TECHNIQUE_OB,
  TECHNIQUE_SD,
  TechniqueGeometrySettings,
  TechniqueInstance,
  collect_technique_instances,
  discover_ifvg_instances,
  epsilon,
  overlap_ratio,
  proximal_retest,
  validate_technique_instance,
)
from app.analysis.types import Zone


pytestmark = pytest.mark.no_database


def _df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
  index = pd.date_range("2026-08-01", periods=len(rows), freq="5min", tz="UTC")
  return pd.DataFrame(
    rows,
    columns=["open", "high", "low", "close"],
    index=index,
  )


def test_epsilon_uses_pip_and_atr_floor():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  assert epsilon(pip_size=0.1, atr=1.0, settings=settings) == 0.1
  assert epsilon(pip_size=0.1, atr=10.0, settings=settings) == pytest.approx(0.5)


def test_overlap_ratio_partial():
  assert overlap_ratio(100.0, 102.0, 101.0, 103.0) == pytest.approx(0.5)


def test_proximal_retest_buy_inside_band():
  settings = TechniqueGeometrySettings(pip_size=0.1)
  assert proximal_retest(
    side="buy", low=100.0, high=101.0, price=100.5, atr=2.0, settings=settings,
  )


def test_fvg_discovery_three_candle_gap():
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99.5, 100),
    (102, 103, 101.5, 102.5),
  ])
  zones = [
    Zone(101.0, 101.5, "demand", origin_index=2, source="bullish_fvg"),
  ]
  instances = collect_technique_instances(
    sd_zones=[], ob_zones=[], fvg_zones=zones, df=df,
  )
  assert len(instances) == 1
  assert instances[0].technique == TECHNIQUE_FVG


def test_ifvg_inverts_bullish_fvg_on_close_through():
  df = _df([
    (100, 101, 99, 100),
    (100, 101, 99.5, 100),
    (102, 103, 101.5, 102.5),
    (102, 102.5, 100.5, 100.8),
  ])
  zones = [
    Zone(101.0, 101.5, "demand", origin_index=2, source="bullish_fvg"),
  ]
  ifvg = discover_ifvg_instances(zones, df, settings=TechniqueGeometrySettings())
  assert len(ifvg) == 1
  assert ifvg[0].technique == TECHNIQUE_IFVG
  assert ifvg[0].side == "sell"


def test_build_confluence_bands_requires_two_distinct_techniques():
  instances = [
    TechniqueInstance(
      TECHNIQUE_SD, "buy", 4100.0, 4101.0, None, ("supply_demand",),
      origin_index=1,
    ),
    TechniqueInstance(
      TECHNIQUE_OB, "buy", 4100.5, 4101.5, None, ("order_block",),
      measured={"has_bos": True, "body_frac": 0.8},
      origin_index=2,
    ),
    TechniqueInstance(
      TECHNIQUE_FVG, "buy", 4102.0, 4103.0, None, ("bullish_fvg",),
      origin_index=3,
    ),
  ]
  bands = build_confluence_bands(
    instances, symbol="XAU", atr=2.0, pip_size=0.1, min_overlap=0.5,
  )
  assert len(bands) == 1
  assert set(bands[0].technique_tags) == {TECHNIQUE_SD, TECHNIQUE_OB}
  assert bands[0].low == 4100.0
  assert bands[0].high == 4101.5


def test_single_technique_does_not_form_confluence_band():
  instances = [
    TechniqueInstance(
      TECHNIQUE_SD, "buy", 4100.0, 4101.0, None, ("supply_demand",),
      origin_index=1,
    ),
    TechniqueInstance(
      TECHNIQUE_SD, "buy", 4100.2, 4101.2, None, ("supply_demand",),
      origin_index=4,
    ),
  ]
  bands = build_confluence_bands(
    instances, symbol="XAU", atr=2.0, pip_size=0.1, min_overlap=0.5,
  )
  assert bands == []


def test_optimize_imbalance_entry_clips_sell_fvg_to_five_price():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.9, 4430.5, "supply", origin_index=2, source="bearish_fvg",
  )
  clipped, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0,
  )
  assert changed is True
  assert clipped.low == pytest.approx(4420.9)
  assert clipped.high == pytest.approx(4425.9)
  assert clipped.high - clipped.low == pytest.approx(5.0)


def test_optimize_imbalance_entry_clips_buy_fvg_to_five_price():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4410.0, 4420.0, "demand", origin_index=2, source="bullish_fvg",
  )
  clipped, changed = optimize_imbalance_entry_zone(
    zone, direction="BUY", max_width_price=5.0,
  )
  assert changed is True
  assert clipped.low == pytest.approx(4415.0)
  assert clipped.high == pytest.approx(4420.0)


def test_optimize_imbalance_leaves_non_fvg_zone_alone():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.9, 4430.5, "supply", origin_index=2, source="supply_demand",
  )
  same, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0,
  )
  assert changed is False
  assert same.low == zone.low
  assert same.high == zone.high


def test_optimize_imbalance_confluence_tags_trigger_clip():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.9, 4430.5, "supply", origin_index=2, source="confluence",
    sources=["fvg_ifvg"],
  )
  clipped, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0, tags=("fvg_ifvg",),
  )
  assert changed is True
  assert clipped.high - clipped.low == pytest.approx(5.0)


def test_optimize_imbalance_skips_already_narrow_band():
  from app.analysis.technique_geometry import optimize_imbalance_entry_zone

  zone = Zone(
    4420.0, 4423.0, "supply", origin_index=2, source="bearish_fvg",
  )
  same, changed = optimize_imbalance_entry_zone(
    zone, direction="SELL", max_width_price=5.0,
  )
  assert changed is False
  assert same is zone
