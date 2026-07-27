"""Shared entry-distance and stop-trail parity fixtures."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.autotrade.entry_distance import measure_entry_distance

pytestmark = pytest.mark.no_database

_CONTRACTS = Path(__file__).resolve().parents[2] / "contracts" / "autotrade"


def _load(name: str) -> dict:
  return json.loads((_CONTRACTS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
  "case",
  _load("entry-distance-parity.json")["cases"],
  ids=lambda case: case["name"],
)
def test_entry_distance_parity_fixture(case):
  fixture = _load("entry-distance-parity.json")
  measurement = measure_entry_distance(
    direction=case["direction"],
    bid=case["bid"],
    ask=case["ask"],
    zone_low=case["zone_low"],
    zone_high=case["zone_high"],
    pip_size=fixture["pip_size"],
    cap_pips=fixture["cap_pips"],
  )
  assert measurement.executable_quote == Decimal(case["expected_executable_quote"])
  assert measurement.distance_pips == Decimal(case["expected_distance_pips"])
  assert measurement.within_cap is (not case["expected_rejected"])


@pytest.mark.parametrize(
  "case",
  _load("stop-trail-parity.json")["cases"],
  ids=lambda case: case["name"],
)
def test_stop_trail_be_plus_six_ticks_parity(case):
  fixture = _load("stop-trail-parity.json")
  tick = Decimal(fixture["tick_size"])
  buffer_ticks = Decimal(str(fixture["break_even_buffer_ticks"]))
  entry = Decimal(case["entry_price"])
  offset = buffer_ticks * tick
  if case["direction"] == "BUY":
    desired = entry + offset
  else:
    desired = entry - offset
  assert desired == Decimal(case["expected_stop"])
  assert offset == Decimal(case["expected_offset"])
  assert offset == Decimal("0.06")
  assert offset != Decimal("0.60")
  assert case["expected_label"] == f"BE+{fixture['break_even_buffer_ticks']} ticks"
  assert abs(desired - entry) == Decimal("0.06")
