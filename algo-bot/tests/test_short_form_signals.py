import pytest

from app.core.symbols import pip_for
from app.signals.fx_manual_algo import build_fx_manual_contract
from app.signals.parsing import (
  DEFAULT_SETUP_TYPE,
  DEFAULT_SL_PIPS,
  DEFAULT_TP_PIPS,
  _parse_manual,
)


PIP = pip_for("XAU")


def test_owner_short_form_example_auto_fills_sl_tp_and_setup():
  # Owner's own example: "xau buy 4078-75 / algo" -> sl always 60 pips,
  # that is 4072 (entry_high 4078 - 6.0).
  parsed = _parse_manual("xau buy 4078-75 / algo")

  assert parsed is not None
  assert parsed["action"] == "BUY"
  assert parsed["entry"] == pytest.approx(4075.0)
  assert parsed["entry_end"] == pytest.approx(4078.0)
  assert parsed["sl"] == pytest.approx(4072.0)
  assert parsed["tps"] == [
    pytest.approx(4078.0 + pips * PIP) for pips in DEFAULT_TP_PIPS
  ]
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE
  assert parsed["execution_mode"] == "algo"


def test_owner_manual_sl_example_keeps_explicit_stop():
  # Owner's own example: "xau buy 4078-75 / sl 4070 / algo" must follow the
  # owner's stop price exactly, not the 60-pip default.
  parsed = _parse_manual("xau buy 4078-75 / sl 4070 / algo")

  assert parsed is not None
  assert parsed["sl"] == pytest.approx(4070.0)
  assert parsed["tps"] == [
    pytest.approx(4078.0 + pips * PIP) for pips in DEFAULT_TP_PIPS
  ]
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE


def test_short_form_sell_defaults_sl_above_and_tp_below_entry():
  parsed = _parse_manual("xau sell 4105-4100 / algo")

  assert parsed is not None
  assert parsed["action"] == "SELL"
  # rr_entry for SELL is entry_low (4100).
  assert parsed["sl"] == pytest.approx(4100.0 + DEFAULT_SL_PIPS * PIP)
  assert parsed["tps"] == [
    pytest.approx(4100.0 - pips * PIP) for pips in DEFAULT_TP_PIPS
  ]


def test_short_form_explicit_setup_tag_overrides_default():
  parsed = _parse_manual("xau buy 4078-75 / trend-pullback / algo")

  assert parsed is not None
  assert parsed["setup_type"] == "trend-pullback"
  assert parsed["sl"] == pytest.approx(4072.0)


def test_short_form_explicit_tp_only_still_defaults_sl_and_setup():
  parsed = _parse_manual("xau buy 4078-75 / tp 88/98 / algo")

  assert parsed is not None
  assert parsed["sl"] == pytest.approx(4072.0)
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE
  assert parsed["tps"] == [4088.0, 4098.0]


def test_full_form_signal_with_both_sl_and_tp_defaults_key_level():
  # Setup is always key-level unless the command tags something else —
  # SL/TP being present does not leave it untagged.
  parsed = _parse_manual("gold sell 4100-4105 / sl 4110 / tp 95/90/80")

  assert parsed is not None
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE
  assert parsed["sl"] == pytest.approx(4110.0)
  assert parsed["tps"] == [4095.0, 4090.0, 4080.0]
  assert parsed["execution_mode"] == "notify"


def test_full_form_algo_defaults_setup_to_key_level():
  parsed = _parse_manual(
    "gold sell 4100-4105 / sl 4110 / tp 95/90/80 / algo"
  )

  assert parsed is not None
  assert parsed["execution_mode"] == "algo"
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE


def test_explicit_setup_overrides_default():
  parsed = _parse_manual(
    "gold sell 4100-4105 / sl 4110 / tp 95/90/80 / supply / algo"
  )

  assert parsed is not None
  assert parsed["setup_type"] == "supply"
  parsed = _parse_manual("xauusd buy 4078-75 / algo")

  assert parsed is not None
  assert parsed["action"] == "BUY"
  assert parsed["sl"] == pytest.approx(4072.0)


def test_gbpjpy_frontload_weights_from_policy():
  contract = build_fx_manual_contract("GBPJPY", "BUY", 216.168)

  assert contract["target_weights"] == [40, 25, 35]


def test_xau_single_price_short_form():
  parsed = _parse_manual("xau sell 4100 / algo")

  assert parsed is not None
  assert parsed["action"] == "SELL"
  assert parsed["entry"] == pytest.approx(4100.0)
  assert parsed["entry_end"] == pytest.approx(4100.0)
  assert parsed["execution_mode"] == "algo"


def test_short_form_without_algo_suffix_still_auto_fills():
  parsed = _parse_manual("xau buy 4078-75")

  assert parsed is not None
  assert parsed["execution_mode"] == "notify"
  assert parsed["sl"] == pytest.approx(4072.0)
  assert parsed["setup_type"] == DEFAULT_SETUP_TYPE
