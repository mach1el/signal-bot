"""Characterize current resolved XAU configuration before effective-context work.

These assertions freeze the XAU compatibility surface so later commits cannot
change trading values while introducing EffectiveInstrumentConfig.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_policy import PythonConfigurationSourcePolicy


pytestmark = pytest.mark.no_database

_FIXTURE = (
  Path(__file__).resolve().parent
  / "fixtures"
  / "effective_xau_characterization.json"
)
_CONFIG_FILE = (
  Path(__file__).resolve().parents[2] / "config" / "trading-bot.yml"
)


def _load_production_example():
  policy = PythonConfigurationSourcePolicy(config_file=str(_CONFIG_FILE))
  return load_python_canonical_settings(load_python_runtime_source_bundle(policy=policy))


def _snapshot(config) -> dict:
  xau = config.instruments.root["XAU"]
  return {
    "instrument": xau.model_dump(mode="python"),
    "contract_instrument": config.contract.instrument.model_dump(mode="python"),
    "lookbacks": config.market_data.lookbacks.model_dump(mode="python"),
    "symbol_contract": config.analysis.zones.symbol_contract.model_dump(
      mode="python",
    ),
    "execution": config.execution.model_dump(mode="python"),
    "risk": config.risk.model_dump(mode="python"),
    "lifecycle": config.lifecycle.model_dump(mode="python"),
    "strategies": config.strategies.model_dump(mode="python"),
    "actionability": config.actionability.model_dump(mode="python"),
    "market_data_scanner": config.market_data.scanner.model_dump(mode="python"),
    "market_data_ctrader_feed": config.market_data.ctrader_feed.model_dump(
      mode="python",
    ),
  }


def test_characterization_fixture_matches_production_example_yaml():
  expected = json.loads(_FIXTURE.read_text(encoding="utf-8"))
  loaded = _load_production_example()
  actual = _snapshot(loaded.config)
  for key in actual:
    assert actual[key] == expected[key], key


def test_characterized_xau_units_and_geometry():
  loaded = _load_production_example()
  cfg = loaded.config
  assert cfg.contract.instrument.pip_size == 0.1
  assert cfg.contract.instrument.price_digits == 2
  assert cfg.contract.instrument.contract_units_per_lot == 100.0
  assert cfg.contract.instrument.canonical_symbol == "XAU"
  assert cfg.market_data.lookbacks.h1_bars == 400
  assert cfg.analysis.zones.symbol_contract.major_maximum_width_price == 10.0
  xau = cfg.instruments.root["XAU"]
  assert xau.enabled is True
  assert xau.contract.pip_size == cfg.contract.instrument.pip_size
  assert xau.market_data.lookbacks.h1_bars == cfg.market_data.lookbacks.h1_bars


def test_characterized_xau_matches_instrument_projection_leaves():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"]
  assert xau.canonical_symbol == cfg.contract.instrument.canonical_symbol
  assert xau.contract.pip_size == cfg.contract.instrument.pip_size
  assert (
    xau.contract.contract_units_per_lot
    == cfg.contract.instrument.contract_units_per_lot
  )
  assert xau.contract.price_digits == cfg.contract.instrument.price_digits
  assert xau.market_data.lookbacks.model_dump() == cfg.market_data.lookbacks.model_dump()
  assert (
    xau.analysis.zones.model_dump()
    == cfg.analysis.zones.symbol_contract.model_dump()
  )
