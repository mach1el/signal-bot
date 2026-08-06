"""History /trade_stats records the real volume-weighted result, not the

best single TP level reached (which overstates a laddered close — see
production incident where a 4-leg TP1-TP4 close journaled +170 pips off
the TP4 price while the broker only realized +87 across the 25%-each fills).
"""

from app.signals.pips_format import legs_net_pips


def test_tp2_then_be_reports_volume_blend():
  # TP1 25% @ 31, TP2 25% @ 59, BE 50% @ 0 -> (0.25*31 + 0.25*59) / 1.0 = 22.5
  legs = [
    {"frac": 0.25, "pips": 31},
    {"frac": 0.25, "pips": 59},
    {"frac": 0.50, "pips": 0},
  ]
  assert legs_net_pips(legs) == 22


def test_four_leg_ladder_blends_not_peaks():
  # Mirrors the production case: TP1-TP4 each 25%, journal must not report
  # the TP4 level (170) when only a quarter of the position got there.
  legs = [
    {"frac": 0.25, "pips": 33},
    {"frac": 0.25, "pips": 75},
    {"frac": 0.25, "pips": 123},
    {"frac": 0.25, "pips": 170},
  ]
  assert legs_net_pips(legs) == 100
  assert legs_net_pips(legs) != 170


def test_pure_stop_loss_uses_final_exit():
  assert legs_net_pips([{"frac": 1.0, "pips": -47}]) == -47


def test_partial_then_worse_exit_blends_the_loss_in():
  assert legs_net_pips([
    {"frac": 0.5, "pips": 50},
    {"frac": 0.5, "pips": -30},
  ]) == 10


def test_empty_legs_are_zero():
  assert legs_net_pips([]) == 0
