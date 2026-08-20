"""Tests for instrument pack merge and live symbol helpers."""

import pytest

from app.configuration.config_file import load_config_file
from app.configuration.instrument_packs import (
  expand_instrument_declarations,
  live_instrument_symbol_csv,
)


pytestmark = pytest.mark.no_database


def test_expand_instrument_pack_merges_defaults_and_overrides():
  packs = {
    "fx_usd_major_v1": {
      "policy": "fx_fixed_2r_v1",
      "reaction_session": "london_ny",
      "targeting": {
        "mode": "fixed_rr",
        "reward_risk": 2.0,
        "target_r_multiples": [1.0, 1.5, 2.0],
        "close_ratios": [0.25, 0.25, 0.50],
        "trail_after_r": 1.5,
        "trail_to_r": 1.0,
        "entry_clips": 2,
      },
      "stop_envelope": {"min_pips": 10, "max_pips": 18, "sl_distance": 0.0018},
      "activation": {
        "require_sweep_body": True,
        "trigger_maximum_age_bars": 3,
        "max_spread_pips": 1,
      },
      "price_scale": {
        "round_step": 0.001,
        "market_map": {
          "change_min": 0.0001,
          "fallback_radius_price": 0.01,
          "scalp_radius_price": 0.006,
        },
        "zone_merge_gap_price": 0.0005,
        "zone_merge_max_width": 0.0015,
        "opposing_minimum_separation_price": 0.0015,
        "fvg_entry_max_width_price": 0.0015,
      },
      "contract": {
        "contract_units_per_lot": 100000.0,
        "max_lots": 10.0,
        "pip_size": 0.0001,
        "price_digits": 5,
        "volume_units_per_lot": 10000000,
      },
      "analysis": {
        "zones": {
          "major_maximum_width_price": 0.003,
          "minimum_width_price": 0.0006,
          "preferred_maximum_width_price": 0.0015,
          "preferred_minimum_width_price": 0.0008,
        },
      },
      "market_data": {
        "lookbacks": {
          "h1_bars": 400,
          "m15_bars": 250,
          "m1_bars": 150,
          "m5_bars": 150,
        },
      },
      "rollout": "live",
      "timeframes": ["H1", "M15", "M5", "M1"],
    },
  }
  expanded = expand_instrument_declarations(
    {
      "TESTUSD": {
        "pack": "fx_usd_major_v1",
        "broker_symbol": "TESTUSD",
        "canonical_symbol": "TESTUSD",
        "enabled": True,
      },
    },
    packs,
  )
  body = expanded["TESTUSD"]
  assert body["policy"] == "fx_fixed_2r_v1"
  assert body["broker_symbol"] == "TESTUSD"
  assert body["stop_envelope"]["min_pips"] == 10
  assert "pack" not in body


def test_production_config_instrument_packs_parse():
  loaded = load_config_file("/root/Research/apexvoid-trading-bot/config/trading-bot.yml")
  assert loaded.instruments.get("EURUSD") is not None
  assert loaded.instruments.get("GBPUSD") is not None


def test_live_instrument_symbol_csv_includes_xau():
  csv = live_instrument_symbol_csv(
    {
      "EURUSD": {"enabled": True, "rollout": "live", "canonical_symbol": "EURUSD",
                 "broker_symbol": "EURUSD", "contract": {"pip_size": 0.0001,
                 "contract_units_per_lot": 100000, "price_digits": 5,
                 "volume_units_per_lot": 10000000}},
    }
  )
  assert "EURUSD" in csv.split(",")
