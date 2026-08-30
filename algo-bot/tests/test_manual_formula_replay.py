"""Unit tests for the repaired manual formula replay harness (PR-J)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analysis.detectors import DetectionResult
from app.analysis.structure import Zone
from app.scripts import manual_formula_replay as replay


pytestmark = pytest.mark.no_database


def test_wilson_intervals_match_known_bounds():
  lo, hi = replay._wilson(13, 20)
  assert lo == pytest.approx(0.433, abs=0.002)
  assert hi == pytest.approx(0.819, abs=0.002)
  lo2, hi2 = replay._wilson(2, 6)
  assert lo2 == pytest.approx(0.097, abs=0.002)
  assert hi2 == pytest.approx(0.700, abs=0.002)


def test_fisher_exact_known_tables():
  assert replay._fisher_exact_2x2(13, 7, 2, 4) == pytest.approx(0.348, abs=0.002)
  assert replay._fisher_exact_2x2(17, 12, 5, 4) == pytest.approx(1.0, abs=0.001)


def test_entry_position_clamped_while_raw_is_not():
  analysis = SimpleNamespace(
    dealing_range=SimpleNamespace(low=100.0, high=110.0),
  )
  raw, clamped, brackets = replay._entry_position_fields(
    analysis, {}, fill=160.0,
  )
  assert raw == pytest.approx(6.0)
  assert clamped == 1.0
  assert brackets is False


def _row(**overrides):
  base = {
    "strategy": "Key Level Reaction",
    "direction": "BUY",
    "result_pips": 10.0,
    "win": True,
    "htf_bias": "up",
    "htf_aligned": True,
    "detector_matched": False,
    "detector_fired_at_fill": False,
    "entry_position": 0.5,
    "nearest_zone_score": 8.0,
    "technique_near_count": 0,
    "nearest_zone_reasons": [],
    "detector_reasons": [],
    "detector_confluence": None,
    "dealing_range_brackets": True,
    "r_multiple": 1.0,
  }
  base.update(overrides)
  return base


def test_scorecard_suppresses_htf_when_confounded_with_direction():
  rows = []
  # 80% agreement: aligned == (direction == BUY)
  for i in range(16):
    direction = "BUY" if i < 10 else "SELL"
    aligned = direction == "BUY"
    rows.append(_row(
      direction=direction,
      htf_bias="up" if aligned or direction == "SELL" else "down",
      htf_aligned=aligned,
      win=i % 2 == 0,
      result_pips=10.0 if i % 2 == 0 else -5.0,
    ))
  # Force agreement > 0.70 across the whole set.
  sc = replay._scorecard(rows)
  assert sc["diagnostics"]["htf_direction_agreement"] > 0.70
  for cell in sc["cells"]:
    assert cell["htf_confounded"] is True
    assert cell["htf_aligned_win_pct"] is None
    assert cell["htf_suppressed_reason"] == "confounded_with_direction"


def test_scorecard_nulls_rates_under_min_n_but_keeps_expectancy():
  rows = [
    _row(
      result_pips=20.0 if i % 2 == 0 else -10.0,
      win=i % 2 == 0,
      r_multiple=2.0 if i % 2 == 0 else -1.0,
      detector_matched=False,
      htf_aligned=True,
    )
    for i in range(5)
  ]
  sc = replay._scorecard(rows)
  assert len(sc["cells"]) == 1
  cell = sc["cells"][0]
  assert cell["n"] == 5
  assert cell["win_rate"] is None
  assert cell["detector_matched_win_pct"] is None
  assert cell["detector_miss_win_pct"] is None
  assert cell["expectancy_pips"] is not None
  assert cell["avg_r"] is not None
  assert cell["cell_underpowered"] is True


def _detection(direction: str, lo: float, hi: float) -> DetectionResult:
  side = "demand" if direction == "BUY" else "supply"
  return DetectionResult(
    setup="Key Level Reaction",
    direction=direction,
    key_level=(lo + hi) / 2.0,
    entry_zone=Zone(lo, hi, side, source="level", score=0.0),
    current_price=(lo + hi) / 2.0,
    confluence=2,
    reasons=["test"],
  )


def test_detector_matched_rejects_wrong_direction_and_far_zone():
  sell = _detection("SELL", 200.0, 205.0)
  assert replay._detection_matched(
    sell, direction="BUY", fill=202.0, atr=2.0, match_atr=0.5,
  ) is False
  far = _detection("BUY", 300.0, 305.0)
  assert replay._detection_matched(
    far, direction="BUY", fill=200.0, atr=2.0, match_atr=0.5,
  ) is False
  near = _detection("BUY", 199.0, 201.0)
  assert replay._detection_matched(
    near, direction="BUY", fill=200.0, atr=2.0, match_atr=0.5,
  ) is True
