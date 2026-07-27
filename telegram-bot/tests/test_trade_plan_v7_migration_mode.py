"""AUTO_TRADE_CONTRACT_MODE and the TradePlan V7 config-health handshake.

Python and C# must agree on contract mode, TradePlan version, and the
execution stream name before either side trusts a V7 plan; a mismatch is a
fatal config-health finding, not a warning, per
docs/adr-trade-plan-v7-boundary.md.
"""

from __future__ import annotations

import importlib
import os

import pytest

from app.autotrade.config_health import CONTRACT_MODES, compare_manifests, python_manifest
from app.autotrade.trade_plan import TRADE_PLAN_VERSION

pytestmark = pytest.mark.no_database


def test_default_contract_mode_is_legacy_v6():
  from app.core.config import settings

  assert settings.auto_trade_contract_mode == "legacy_v6"


def test_contract_mode_rejects_unknown_value(monkeypatch):
  monkeypatch.setenv("AUTO_TRADE_CONTRACT_MODE", "definitely_not_a_mode")
  config_module = importlib.import_module("app.core.config")
  with pytest.raises(ValueError, match="AUTO_TRADE_CONTRACT_MODE"):
    config_module.Settings()


@pytest.mark.parametrize("mode", CONTRACT_MODES)
def test_every_documented_mode_is_accepted(monkeypatch, mode):
  monkeypatch.setenv("AUTO_TRADE_CONTRACT_MODE", mode)
  config_module = importlib.import_module("app.core.config")
  settings = config_module.Settings()
  assert settings.auto_trade_contract_mode == mode


def test_manifest_carries_trade_plan_version_and_contract_mode():
  manifest = python_manifest()
  assert manifest["trade_plan_version"] == TRADE_PLAN_VERSION
  assert manifest["contract_mode"] in CONTRACT_MODES
  assert manifest["trade_plan_stream"] == "execution:trade_plans"


def test_contract_mode_mismatch_is_fatal_not_a_warning():
  python = python_manifest()
  ctrader = dict(python)
  ctrader["contract_mode"] = "v7_only"
  python["contract_mode"] = "legacy_v6"

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "contract_mode" in health["fatal"]


def test_trade_plan_version_mismatch_is_fatal():
  python = python_manifest()
  ctrader = dict(python)
  ctrader["trade_plan_version"] = 6

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "trade_plan_version" in health["fatal"]


def test_trade_plan_stream_mismatch_is_fatal():
  python = python_manifest()
  ctrader = dict(python)
  ctrader["trade_plan_stream"] = "execution:trade_plans_v2"

  health = compare_manifests(python, ctrader)

  assert health["state"] == "fatal"
  assert "trade_plan_stream" in health["fatal"]


def test_matching_contract_fields_do_not_trigger_v7_fatal_reasons():
  # required_strategy_key_missing entries are a pre-existing, unrelated
  # fatal reason in a bare test environment (no strategy-enable env vars
  # set) - this test only asserts the *new* V7 fields stay quiet when both
  # sides agree, not that the whole manifest comparison is clean.
  python = python_manifest()
  ctrader = dict(python)

  health = compare_manifests(python, ctrader)

  assert "contract_mode" not in health["fatal"]
  assert "trade_plan_version" not in health["fatal"]
  assert "trade_plan_stream" not in health["fatal"]
