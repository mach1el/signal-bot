"""FX owner manual /algo parsing and contract defaults."""

from __future__ import annotations

import pytest

from app.signals.fx_manual_algo import build_fx_manual_contract
from app.signals.parsing import _parse_manual
from tests.test_config_effective_instrument_context import _load_production_example


pytestmark = pytest.mark.no_database


@pytest.fixture(autouse=True)
def _production_config(monkeypatch):
  cfg = _load_production_example().config
  for target in (
    "app.core.config.runtime_config",
    "app.core.symbols.runtime_config",
    "app.signals.parsing.runtime_config",
    "app.signals.fx_manual_algo.runtime_config",
  ):
    monkeypatch.setattr(target, cfg, raising=False)


def test_eurusd_buy_single_price_algo_sets_fixed_rr_ladder():
  parsed = _parse_manual("eurusd buy 1.15007 / algo")

  assert parsed is not None
  assert parsed["symbol"] == "EURUSD"
  assert parsed["action"] == "BUY"
  assert parsed["entry"] == pytest.approx(1.15007)
  assert parsed["entry_end"] == pytest.approx(1.15007)
  assert parsed["execution_mode"] == "algo"
  assert parsed["manual_single_entry"] is True
  assert parsed["target_weights"] == [25, 25, 50]
  assert parsed["sl"] == pytest.approx(1.14867)
  assert parsed["tps"] == [
    pytest.approx(1.15147),
    pytest.approx(1.15217),
    pytest.approx(1.15287),
  ]


def test_eurusd_sell_without_algo_suffix_is_notify_only():
  parsed = _parse_manual("eurusd sell 1.15007")

  assert parsed is not None
  assert parsed["action"] == "SELL"
  assert parsed["execution_mode"] == "notify"


def test_eurusd_explicit_sl_and_tp_override_defaults():
  parsed = _parse_manual(
    "eurusd buy 1.15007 / sl 1.14900 / tp 1.15100/1.15200/1.15300 / algo"
  )

  assert parsed is not None
  assert parsed["sl"] == pytest.approx(1.14900)
  assert parsed["tps"] == [
    pytest.approx(1.15100),
    pytest.approx(1.15200),
    pytest.approx(1.15300),
  ]


def test_xau_single_price_algo_accepted():
  parsed = _parse_manual("xau buy 4078 / algo")

  assert parsed is not None
  assert parsed["action"] == "BUY"
  assert parsed["entry"] == pytest.approx(4078.0)
  assert parsed["entry_end"] == pytest.approx(4078.0)
  assert parsed["execution_mode"] == "algo"
  assert parsed["sl"] == pytest.approx(4072.0)


def test_usdjpy_buy_single_price_algo():
  parsed = _parse_manual("usdjpy buy 148.520 / algo")

  assert parsed is not None
  assert parsed["symbol"] == "USDJPY"
  assert parsed["action"] == "BUY"
  assert parsed["execution_mode"] == "algo"
  assert parsed["manual_single_entry"] is True
