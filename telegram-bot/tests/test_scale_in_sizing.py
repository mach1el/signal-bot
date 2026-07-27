from app.autotrade.scale_in_sizing import ScaleInSizingInputs, plan_scale_in_sizing


def test_size_ratio_caps_momentum_style_add():
  decision = plan_scale_in_sizing(ScaleInSizingInputs(
    balance=5_000,
    risk_percent=2,
    pip_value_per_lot=10,
    add_risk_fraction=0.5,
    add_stop_pips=15,
    booked_pnl=20,
    stop_pnl=0,
    table_lots=0.10,
    open_lots=0.06,
    initial_tranche_lots=0.06,
    add_size_ratio=0.5,
  ))

  assert decision.allowed
  assert decision.lots == 0.03
  assert decision.volume == 300
  assert decision.binding_term == "size-ratio-bound"


def test_missing_initial_tranche_leaves_size_ratio_inactive():
  capped = plan_scale_in_sizing(ScaleInSizingInputs(
    balance=5_000,
    risk_percent=2,
    pip_value_per_lot=10,
    add_risk_fraction=0.5,
    add_stop_pips=15,
    booked_pnl=20,
    stop_pnl=0,
    table_lots=0.10,
    open_lots=0.06,
    initial_tranche_lots=0.06,
    add_size_ratio=0.5,
  ))
  uncapped = plan_scale_in_sizing(ScaleInSizingInputs(
    balance=5_000,
    risk_percent=2,
    pip_value_per_lot=10,
    add_risk_fraction=0.5,
    add_stop_pips=15,
    booked_pnl=20,
    stop_pnl=0,
    table_lots=0.10,
    open_lots=0.06,
    initial_tranche_lots=None,
    add_size_ratio=0.5,
  ))

  assert capped.allowed and uncapped.allowed
  assert uncapped.lots > capped.lots
  assert capped.binding_term == "size-ratio-bound"
