"""Effective instrument context: parity, rollout, policy, and fail-closed rules."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.configuration.effective_instrument import (
  EffectiveInstrumentError,
  build_effective_instrument,
)
from app.configuration.models.instruments import (
  InstrumentConfig,
  InstrumentContractConfig,
  InstrumentLookbacksConfig,
  InstrumentMarketDataConfig,
  InstrumentRollout,
  InstrumentsConfig,
  XAU_CURRENT_V1_POLICY,
  effective_rollout,
)
from app.configuration.python_loader import load_python_canonical_settings
from app.configuration.python_sources import load_python_runtime_source_bundle
from app.configuration.source_policy import PythonConfigurationSourcePolicy


pytestmark = pytest.mark.no_database

_CONFIG_FILE = (
  Path(__file__).resolve().parents[2] / "config" / "trading-bot.yml"
)


def _load_production_example():
  policy = PythonConfigurationSourcePolicy(config_file=str(_CONFIG_FILE))
  return load_python_canonical_settings(
    load_python_runtime_source_bundle(policy=policy),
  )


def _parity_payload(config, effective) -> dict:
  """Structured comparison surface for XAU global leaves vs effective context."""
  return {
    "canonical_symbol": (
      config.contract.instrument.canonical_symbol,
      effective.identity.canonical_symbol,
    ),
    "broker_symbol": (
      config.instruments.root["XAU"].broker_symbol,
      effective.identity.broker_symbol,
    ),
    "pip_size": (
      config.contract.instrument.pip_size,
      effective.units.pip_size,
    ),
    "price_digits": (
      config.contract.instrument.price_digits,
      effective.units.price_digits,
    ),
    "contract_units_per_lot": (
      config.contract.instrument.contract_units_per_lot,
      effective.units.contract_units_per_lot,
    ),
    "timeframes": (
      tuple(config.instruments.root["XAU"].timeframes),
      effective.identity.timeframes,
    ),
    "lookbacks": (
      config.market_data.lookbacks.model_dump(mode="python"),
      effective.market_data.lookbacks.model_dump(mode="python"),
    ),
    "zones": (
      config.analysis.zones.symbol_contract.model_dump(mode="python"),
      effective.analysis.zones.model_dump(mode="python"),
    ),
    "execution": (
      config.execution.model_dump(mode="python"),
      effective.execution.model_dump(mode="python"),
    ),
    "risk": (
      config.risk.model_dump(mode="python"),
      effective.risk.model_dump(mode="python"),
    ),
    "lifecycle": (
      config.lifecycle.model_dump(mode="python"),
      effective.lifecycle.model_dump(mode="python"),
    ),
    "strategies": (
      config.strategies.model_dump(mode="python"),
      effective.strategies.model_dump(mode="python"),
    ),
    "actionability": (
      config.actionability.model_dump(mode="python"),
      effective.actionability.model_dump(mode="python"),
    ),
  }


def test_production_yaml_xau_effective_parity():
  loaded = _load_production_example()
  cfg = loaded.config
  effective = cfg.for_instrument("XAU")
  assert effective.identity.rollout is InstrumentRollout.LIVE
  assert effective.policy_name == XAU_CURRENT_V1_POLICY
  assert "XAUUSD" in effective.identity.aliases
  for name, (left, right) in _parity_payload(cfg, effective).items():
    assert left == right, name
  assert cfg.enabled_instruments() == ("EURUSD", "GBPJPY", "XAU")
  assert cfg.live_instruments() == ("EURUSD", "GBPJPY", "XAU")
  assert cfg.instrument_for_broker_symbol("xauusd").identity.canonical_symbol == "XAU"


def test_production_yaml_fx_live_executable_units():
  loaded = _load_production_example()
  cfg = loaded.config
  eurusd = cfg.for_instrument("EURUSD")
  gbpjpy = cfg.for_instrument("GBPJPY")
  xau = cfg.for_instrument("XAU")
  assert eurusd.identity.rollout is InstrumentRollout.LIVE
  assert gbpjpy.identity.rollout is InstrumentRollout.LIVE
  assert eurusd.units.pip_size == 0.0001
  assert eurusd.units.price_digits == 5
  assert eurusd.units.contract_units_per_lot == 100000.0
  assert eurusd.units.pip_value_per_lot == 10.0
  assert gbpjpy.units.pip_size == 0.01
  assert gbpjpy.units.price_digits == 3
  assert gbpjpy.units.contract_units_per_lot == 100000.0
  assert gbpjpy.units.pip_value_per_lot == 7.0
  assert xau.units.pip_value_per_lot == 10.0
  assert eurusd.analysis.runtime.levels.round_step == 0.001
  assert gbpjpy.analysis.runtime.levels.round_step == 0.1
  assert eurusd.is_live()
  assert eurusd.is_executable()
  assert gbpjpy.is_live()
  assert gbpjpy.is_executable()
  assert cfg.instrument_for_broker_symbol("EURUSD").identity.canonical_symbol == "EURUSD"
  assert cfg.instrument_for_broker_symbol("GBPJPY").identity.canonical_symbol == "GBPJPY"


def test_for_instrument_case_normalization():
  loaded = _load_production_example()
  a = loaded.config.for_instrument("xau")
  b = loaded.config.for_instrument("XAU")
  assert a.identity.canonical_symbol == b.identity.canonical_symbol
  assert a.units.model_dump() == b.units.model_dump()


@pytest.mark.parametrize(
  ("payload", "expected"),
  [
    ({"enabled": True}, InstrumentRollout.LIVE),
    ({"enabled": False}, InstrumentRollout.DISABLED),
    ({"rollout": "disabled"}, InstrumentRollout.DISABLED),
    ({"rollout": "feed_only"}, InstrumentRollout.FEED_ONLY),
    ({"rollout": "analysis_only"}, InstrumentRollout.ANALYSIS_ONLY),
    ({"rollout": "paper"}, InstrumentRollout.PAPER),
    ({"rollout": "live"}, InstrumentRollout.LIVE),
    ({"enabled": True, "rollout": "live"}, InstrumentRollout.LIVE),
    ({"enabled": False, "rollout": "disabled"}, InstrumentRollout.DISABLED),
  ],
)
def test_rollout_compatibility_mapping(payload, expected):
  body = {
    "canonical_symbol": "XAU",
    "broker_symbol": "XAU",
    "contract": {
      "pip_size": 0.1,
      "price_digits": 2,
      "contract_units_per_lot": 100.0,
    },
    **payload,
  }
  if expected is InstrumentRollout.DISABLED and "contract" in body:
    # disabled may omit contract
    if payload.get("enabled") is False or payload.get("rollout") == "disabled":
      pass
  instrument = InstrumentConfig.model_validate(body)
  assert effective_rollout(instrument) is expected


@pytest.mark.parametrize(
  "payload",
  [
    {"enabled": True, "rollout": "disabled"},
    {"enabled": False, "rollout": "live"},
    {"enabled": False, "rollout": "paper"},
    {"enabled": False, "rollout": "feed_only"},
  ],
)
def test_conflicting_enabled_and_rollout_fail_closed(payload):
  with pytest.raises(ValidationError):
    InstrumentConfig.model_validate(
      {
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
        "contract": {
          "pip_size": 0.1,
          "price_digits": 2,
          "contract_units_per_lot": 100.0,
        },
        **payload,
      }
    )


def test_unknown_policy_rejected():
  with pytest.raises(ValidationError, match="unknown instrument policy"):
    InstrumentConfig.model_validate(
      {
        "enabled": True,
        "policy": "not_a_real_policy",
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
        "contract": {
          "pip_size": 0.1,
          "price_digits": 2,
          "contract_units_per_lot": 100.0,
        },
      }
    )


def test_override_wins_and_records_provenance():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"]
  updated = xau.model_copy(
    update={
      "overrides": {"execution.entry.maximum_chase_distance_pips": 12.5},
    },
  )
  instruments = InstrumentsConfig(root={"XAU": updated})
  runtime = cfg.model_copy(update={"instruments": instruments})
  effective = build_effective_instrument(runtime, "XAU")
  assert effective.execution.entry.maximum_chase_distance_pips == 12.5
  assert cfg.execution.entry.maximum_chase_distance_pips != 12.5
  paths = {item.path for item in effective.provenance.entries}
  assert "execution.entry.maximum_chase_distance_pips" in paths


def _make_second_instrument(**kwargs) -> InstrumentConfig:
  from app.configuration.models.instruments import (
    InstrumentAnalysisConfig,
    InstrumentZoneWidthConfig,
  )

  defaults = {
    "enabled": True,
    "rollout": InstrumentRollout.FEED_ONLY,
    "canonical_symbol": "XAG",
    "broker_symbol": "XAGUSD",
    "policy": XAU_CURRENT_V1_POLICY,
    "contract": InstrumentContractConfig(
      pip_size=0.01,
      price_digits=3,
      contract_units_per_lot=5000.0,
    ),
    "market_data": InstrumentMarketDataConfig(
      lookbacks=InstrumentLookbacksConfig(
        h1_bars=100,
        m15_bars=100,
        m5_bars=100,
        m1_bars=100,
      ),
    ),
    "analysis": InstrumentAnalysisConfig(
      zones=InstrumentZoneWidthConfig(
        minimum_width_price=0.05,
        preferred_minimum_width_price=0.05,
        preferred_maximum_width_price=0.2,
        major_maximum_width_price=0.5,
      ),
    ),
  }
  defaults.update(kwargs)
  return InstrumentConfig(**defaults)


def test_multi_instrument_feed_only_second_symbol():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"]
  silver = _make_second_instrument()
  instruments = InstrumentsConfig(root={"XAU": xau, "XAG": silver})
  runtime = cfg.model_copy(update={"instruments": instruments})
  xau_eff = runtime.for_instrument("XAU")
  xag_eff = runtime.for_instrument("XAG")
  assert xau_eff.units.pip_size == 0.1
  assert xag_eff.units.pip_size == 0.01
  assert xag_eff.units.price_digits == 3
  assert xag_eff.identity.rollout is InstrumentRollout.FEED_ONLY
  assert not xag_eff.is_live()
  assert not xag_eff.is_executable()
  assert runtime.live_instruments() == ("XAU",)
  assert runtime.enabled_instruments() == ("XAG", "XAU")


def test_duplicate_broker_symbol_rejected():
  with pytest.raises(ValidationError, match="duplicate broker_symbol"):
    InstrumentsConfig.model_validate(
      {
        "XAU": {
          "enabled": True,
          "canonical_symbol": "XAU",
          "broker_symbol": "SAME",
          "contract": {
            "pip_size": 0.1,
            "price_digits": 2,
            "contract_units_per_lot": 100.0,
          },
        },
        "XAG": {
          "enabled": True,
          "rollout": "feed_only",
          "canonical_symbol": "XAG",
          "broker_symbol": "SAME",
          "policy": XAU_CURRENT_V1_POLICY,
          "contract": {
            "pip_size": 0.01,
            "price_digits": 3,
            "contract_units_per_lot": 5000.0,
          },
          "market_data": {
            "lookbacks": {
              "h1_bars": 100,
              "m15_bars": 100,
              "m5_bars": 100,
              "m1_bars": 100,
            },
          },
        },
      }
    )


def test_missing_and_invalid_contract_rejected():
  with pytest.raises(ValidationError):
    InstrumentConfig.model_validate(
      {
        "enabled": True,
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
      }
    )
  with pytest.raises(ValidationError):
    InstrumentContractConfig.model_validate(
      {"pip_size": 0, "price_digits": 2, "contract_units_per_lot": 100}
    )
  with pytest.raises(ValidationError):
    InstrumentContractConfig.model_validate(
      {"pip_size": 0.1, "price_digits": -1, "contract_units_per_lot": 100}
    )
  with pytest.raises(ValidationError):
    InstrumentContractConfig.model_validate(
      {"pip_size": 0.1, "price_digits": 2, "contract_units_per_lot": 0}
    )


def test_unknown_override_path_rejected():
  loaded = _load_production_example()
  cfg = loaded.config
  xau = cfg.instruments.root["XAU"].model_copy(
    update={"overrides": {"not.a.catalog.path": 1}},
  )
  runtime = cfg.model_copy(
    update={"instruments": InstrumentsConfig(root={"XAU": xau})},
  )
  with pytest.raises(EffectiveInstrumentError, match="unknown"):
    runtime.for_instrument("XAU")


def test_secret_and_protocol_constant_overrides_rejected():
  loaded = _load_production_example()
  cfg = loaded.config
  from app.configuration.catalog import iter_catalog_entries

  secret_path = next(entry.path for entry in iter_catalog_entries() if entry.secret)
  protocol_path = next(
    entry.path
    for entry in iter_catalog_entries()
    if entry.protocol_constant or entry.kind == "protocol_constant"
  )
  for path in (secret_path, protocol_path):
    xau = cfg.instruments.root["XAU"].model_copy(
      update={"overrides": {path: "x"}},
    )
    runtime = cfg.model_copy(
      update={"instruments": InstrumentsConfig(root={"XAU": xau})},
    )
    with pytest.raises(EffectiveInstrumentError):
      runtime.for_instrument("XAU")


def test_unknown_symbol_fails_closed():
  loaded = _load_production_example()
  with pytest.raises(EffectiveInstrumentError, match="unknown instrument"):
    loaded.config.for_instrument("BTCUSD")


def test_disabled_instrument_without_contract_allowed_in_registry():
  InstrumentsConfig.model_validate(
    {
      "XAU": {
        "enabled": True,
        "canonical_symbol": "XAU",
        "broker_symbol": "XAU",
        "contract": {
          "pip_size": 0.1,
          "price_digits": 2,
          "contract_units_per_lot": 100.0,
        },
      },
      "EUR": {
        "enabled": False,
        "canonical_symbol": "EUR",
        "broker_symbol": "EURUSD",
      },
    }
  )
