"""Manifest V2 instrument runtimes + V1 compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.configuration.runtime_manifest import (
  MANIFEST_VERSION,
  MANIFEST_VERSION_V1,
  RuntimeManifestError,
  build_resolved_runtime_manifest,
  load_manifest_file,
  upgrade_v1_payload_to_v2,
)


pytestmark = pytest.mark.no_database
_CONFIG = Path(__file__).resolve().parents[3] / "config" / "trading-bot.yml"


def test_manifest_v2_has_instrument_runtimes_xau_and_fx_live():
  payload = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  assert payload["manifest_version"] == MANIFEST_VERSION == 2
  assert payload["live_instruments"] == ["EURUSD", "GBPJPY", "XAU"]
  assert set(payload["instrument_runtimes"]) == {"EURUSD", "GBPJPY", "XAU"}
  xau = payload["instrument_runtimes"]["XAU"]
  assert xau["rollout"] == "live"
  assert xau["feed"]["ctrader_symbol"] == "XAUUSD"
  assert xau["feed"]["redis_symbol"] == "XAU"
  assert xau["units"]["pip_size"] == "0.1"
  assert xau["units"]["pip_value_per_lot"] == "10"
  eurusd = payload["instrument_runtimes"]["EURUSD"]
  assert eurusd["rollout"] == "live"
  assert eurusd["feed"]["ctrader_symbol"] == "EURUSD"
  assert eurusd["feed"]["redis_symbol"] == "EURUSD"
  assert eurusd["units"]["pip_size"] == "0.0001"
  assert eurusd["units"]["pip_value_per_lot"] == "10"
  assert float(eurusd["units"]["lot_multiplier"]) == 3.0
  gbpjpy = payload["instrument_runtimes"]["GBPJPY"]
  assert gbpjpy["rollout"] == "live"
  assert gbpjpy["feed"]["ctrader_symbol"] == "GBPJPY"
  assert gbpjpy["units"]["pip_size"] == "0.01"
  assert gbpjpy["units"]["pip_value_per_lot"] == "7"
  assert float(gbpjpy["units"]["lot_multiplier"]) == 3.0
  assert xau["units"]["volume_units_per_lot"] == 10000
  assert eurusd["units"]["volume_units_per_lot"] == 10000000
  assert gbpjpy["units"]["volume_units_per_lot"] == 10000000
  # Deprecated compatibility projections remain and match XAU runtime.
  assert payload["feed"] == xau["feed"]
  assert payload["auto_trade"]["targets_pips"] == xau["auto_trade"]["targets_pips"]


def test_manifest_v1_upgrades_to_xau_only_runtime(tmp_path):
  v2 = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  v1 = dict(v2)
  v1["manifest_version"] = MANIFEST_VERSION_V1
  v1.pop("instrument_runtimes", None)
  path = tmp_path / "v1.json"
  path.write_text(json.dumps(v1), encoding="utf-8")
  loaded = load_manifest_file(path)
  assert loaded["manifest_version"] == 2
  assert set(loaded["instrument_runtimes"]) == {"XAU"}
  upgraded = upgrade_v1_payload_to_v2(v1)
  assert upgraded["instrument_runtimes"]["XAU"]["feed"] == v1["feed"]


def test_unsupported_manifest_version_still_rejected(tmp_path):
  payload = build_resolved_runtime_manifest(config_file=str(_CONFIG))
  payload["manifest_version"] = 99
  path = tmp_path / "bad.json"
  path.write_text(json.dumps(payload), encoding="utf-8")
  with pytest.raises(RuntimeManifestError, match="unsupported"):
    load_manifest_file(path)
