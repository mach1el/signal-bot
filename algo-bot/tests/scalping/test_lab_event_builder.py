"""Tests for historical Liquidity Sweep LabEvent builder."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.scalping.lab_event_builder import (
  active_range_as_of,
  build_liquidity_sweep_events,
  calibrate_events,
  load_ohlc_path,
  main as builder_main,
  write_ohlc_jsonl,
)
from app.scalping.replay import aggregate_report
from app.scalping.replay_lab import LabEvent, replay_lab_event


pytestmark = pytest.mark.no_database


def _m5_range_frame(
  *,
  start: str = "2024-01-02T00:00:00Z",
  n: int = 30,
  low: float = 4050.0,
  high: float = 4060.0,
) -> pd.DataFrame:
  """Flat-range M5 bars so active range is stable and known."""
  idx = pd.date_range(start=start, periods=n, freq="5min", tz="UTC")
  mid = (low + high) / 2.0
  rows = []
  for i, _ in enumerate(idx):
    # Mild oscillation inside [low, high]
    o = mid + (0.2 if i % 2 == 0 else -0.2)
    rows.append({
      "open": o,
      "high": high - 0.1,
      "low": low + 0.1,
      "close": o,
      "volume": 1.0,
    })
  # Force extremes into the lookback window
  rows[-1]["high"] = high
  rows[-2]["low"] = low
  return pd.DataFrame(rows, index=idx)


def _m1_with_pierce(
  *,
  start: str,
  range_low: float,
  range_high: float,
  pierce: str,
) -> pd.DataFrame:
  """One M1 pierce bar plus forward bars for paper fill."""
  idx = pd.date_range(start=start, periods=10, freq="1min", tz="UTC")
  mid = (range_low + range_high) / 2.0
  rows = []
  for _ in idx:
    rows.append({
      "open": mid,
      "high": mid + 0.2,
      "low": mid - 0.2,
      "close": mid,
      "volume": 1.0,
    })
  # Pierce bar at index 0 (aligned to start)
  if pierce == "BUY":
    rows[0] = {
      "open": range_low + 0.2,
      "high": range_low + 0.8,
      "low": range_low - 0.5,
      "close": range_low + 0.4,
      "volume": 1.0,
    }
    # Favorable follow-through toward target
    for i in range(1, 6):
      px = range_low + 0.4 + i * 0.8
      rows[i] = {"open": px - 0.2, "high": px + 0.3, "low": px - 0.3, "close": px, "volume": 1.0}
  else:
    rows[0] = {
      "open": range_high - 0.2,
      "high": range_high + 0.5,
      "low": range_high - 0.8,
      "close": range_high - 0.4,
      "volume": 1.0,
    }
    for i in range(1, 6):
      px = range_high - 0.4 - i * 0.8
      rows[i] = {"open": px + 0.2, "high": px + 0.3, "low": px - 0.3, "close": px, "volume": 1.0}
  return pd.DataFrame(rows, index=idx)


def test_active_range_no_lookahead():
  m5 = _m5_range_frame(n=30, low=4050.0, high=4060.0)
  # Future spike must not leak into earlier as-of
  future = m5.index[-1] + pd.Timedelta(minutes=5)
  m5.loc[future] = {
    "open": 4100.0, "high": 4200.0, "low": 4000.0, "close": 4100.0, "volume": 1.0,
  }
  as_of = m5.index[-2]  # before the spike row
  rng = active_range_as_of(m5, as_of_ts=as_of, lookback=24)
  assert rng is not None
  low, high = rng
  assert low == pytest.approx(4050.0)
  assert high == pytest.approx(4060.0)
  assert high < 4200.0


def test_build_emits_buy_pierce_and_gate_can_allow():
  m5 = _m5_range_frame(
    start="2024-01-02T08:00:00Z",  # london session UTC
    n=30,
    low=4050.0,
    high=4060.0,
  )
  # Align M1 pierce after enough M5 history
  m1_start = (m5.index[-1] + pd.Timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
  m1 = _m1_with_pierce(
    start=m1_start,
    range_low=4050.0,
    range_high=4060.0,
    pierce="BUY",
  )
  events = build_liquidity_sweep_events(m1, m5, bars_after=8)
  buy = [e for e in events if e["direction"] == "BUY"]
  assert buy, "expected BUY pierce event"
  ev = buy[0]
  assert ev["liquidity_level"] == pytest.approx(4050.0)
  assert ev["strategy"] == "liquidity_sweep_reversal"
  assert "vr" in ev
  assert ev["bars_after"]
  # Reclaim close above L at discount location → gate allow
  row = replay_lab_event(LabEvent.from_dict(ev))
  assert row["gate_allowed"] is True
  assert row["vr"] == ev["vr"]


def test_build_midrange_location_blocks_via_gate():
  """Pierce with reclaim but price mid-range → location_outside_edge."""
  m5 = _m5_range_frame(start="2024-01-02T08:00:00Z", n=30, low=4050.0, high=4060.0)
  m1_start = (m5.index[-1] + pd.Timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
  idx = pd.date_range(start=m1_start, periods=5, freq="1min", tz="UTC")
  # Sweep low but close near mid-range (premium for BUY)
  rows = [{
    "open": 4055.0,
    "high": 4056.0,
    "low": 4049.5,
    "close": 4055.5,
    "volume": 1.0,
  } for _ in idx]
  m1 = pd.DataFrame(rows, index=idx)
  events = build_liquidity_sweep_events(m1, m5, bars_after=3)
  buy = [e for e in events if e["direction"] == "BUY"]
  assert buy
  row = replay_lab_event(LabEvent.from_dict(buy[0]))
  assert row["outcome"] == "blocked"
  assert row["block_reason"] == "location_outside_edge"


def test_aggregate_by_vr():
  rows = [
    {"outcome": "target", "net_r": 1.0, "session": "london", "vr": "quiet", "mfe_pips": 1, "mae_pips": 0},
    {"outcome": "stop", "net_r": -1.0, "session": "london", "vr": "active", "mfe_pips": 0, "mae_pips": 1},
    {"outcome": "target", "net_r": 0.5, "session": "asia", "vr": "quiet", "mfe_pips": 1, "mae_pips": 0},
  ]
  agg = aggregate_report(rows)
  assert agg["by_vr"]["quiet"]["count"] == 2
  assert agg["by_vr"]["active"]["count"] == 1


def test_cli_build_from_jsonl(tmp_path: Path):
  m5 = _m5_range_frame(start="2024-01-02T08:00:00Z", n=30, low=4050.0, high=4060.0)
  m1_start = (m5.index[-1] + pd.Timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
  m1 = _m1_with_pierce(
    start=m1_start, range_low=4050.0, range_high=4060.0, pierce="BUY",
  )
  m1_path = tmp_path / "m1.jsonl"
  m5_path = tmp_path / "m5.jsonl"
  write_ohlc_jsonl(m1, m1_path)
  write_ohlc_jsonl(m5, m5_path)
  out_events = tmp_path / "events.jsonl"
  out_report = tmp_path / "report.json"
  rc = builder_main([
    "--m1", str(m1_path),
    "--m5", str(m5_path),
    "--out-events", str(out_events),
    "--out-report", str(out_report),
    "--bars-after", "5",
  ])
  assert rc == 0
  assert out_events.exists()
  lines = [ln for ln in out_events.read_text().splitlines() if ln.strip()]
  assert lines
  report = json.loads(out_report.read_text())
  assert "calibration_traded" in report
  assert "by_vr" in report["aggregate_all"]
  # Round-trip loader
  loaded = load_ohlc_path(m1_path)
  assert not loaded.empty
  cal = calibrate_events([json.loads(lines[0])])
  assert cal["event_count"] == 1
