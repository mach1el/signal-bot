"""History /trade_stats uses highest TP/pips hit, not lot-weighted net."""

from app.signals.pips_format import legs_achieved_pips, legs_net_pips


def test_tp2_then_be_reports_peak_not_volume_blend():
  # Mirrors the production case: TP1 25% @ 31, TP2 25% @ 59, BE 50% @ 0.
  # Lot-weighted net ≈ 22.5; achieved must stay at the highest TP.
  legs = [
    {"frac": 0.25, "pips": 31},
    {"frac": 0.25, "pips": 59},
    {"frac": 0.50, "pips": 0},
  ]
  assert legs_achieved_pips(legs) == 59
  assert legs_achieved_pips(legs) != 22
  assert legs_net_pips(legs) == 59


def test_pure_stop_loss_uses_final_exit():
  assert legs_achieved_pips([{"frac": 1.0, "pips": -47}]) == -47


def test_partial_then_worse_exit_keeps_booked_tp():
  assert legs_achieved_pips([
    {"frac": 0.5, "pips": 50},
    {"frac": 0.5, "pips": -30},
  ]) == 50


def test_empty_legs_are_zero():
  assert legs_achieved_pips([]) == 0
