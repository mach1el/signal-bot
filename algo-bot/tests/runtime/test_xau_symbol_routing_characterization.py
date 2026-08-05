"""Characterization of current XAU symbol routing (pre-routing rewrite).

These tests pin production XAU behaviour so the multi-symbol refactor cannot
drift silently. Values must match the current ENV / YAML effective path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.configuration.models.instruments import InstrumentRollout
from app.core.config import runtime_config
from app.core.symbols import (
  CHANNELS,
  SYMBOLS,
  canonical_symbol,
  channel_for_symbol,
  pip_for,
)
from app.runtime.instrument_registry import build_instrument_runtime_registry
from app.runtime.price_format import format_price
from app.runtime.rollout_gates import (
  permits_analysis,
  permits_broker_execution,
  permits_candidate_publication,
  permits_feed,
  permits_public_delivery,
)


pytestmark = pytest.mark.no_database

_CONFIG = Path(__file__).resolve().parents[3] / "config" / "trading-bot.yml"


def test_xau_symbol_units_match_effective_context():
  effective = runtime_config.for_instrument("XAU")
  assert effective.identity.canonical_symbol == "XAU"
  assert effective.identity.broker_symbol in {"XAU", "XAUUSD"}
  assert effective.rollout is InstrumentRollout.LIVE
  assert effective.units.pip_size == 0.1
  assert effective.units.price_digits == 2
  assert effective.units.contract_units_per_lot == 100.0
  assert SYMBOLS["XAU"]["pip"] == effective.units.pip_size
  assert SYMBOLS["XAU"]["digits"] == effective.units.price_digits
  assert pip_for("XAU") == 0.1
  assert pip_for("XAUUSD") == 0.1
  assert canonical_symbol("XAUUSD") == "XAU"
  assert canonical_symbol("xau") == "XAU"


def test_xau_telegram_channels_remain_xau_only():
  symbols = {channel["symbol"] for channel in CHANNELS}
  assert symbols == {"XAU"}
  vip = channel_for_symbol("XAU")
  assert isinstance(vip, int)


def test_xau_live_instruments_only():
  assert runtime_config.live_instruments() == ("XAU",)
  registry = build_instrument_runtime_registry(runtime_config)
  live = registry.live_instruments()
  assert len(live) == 1
  assert live[0].instrument_id == "XAU"
  assert registry.scanner_symbols(compatibility_filter=["XAU"]) == ("XAU",)


def test_format_price_uses_xau_digits_and_rejects_unknown():
  assert format_price("XAU", 2345.67) == "2345.67"
  with pytest.raises(KeyError):
    format_price("NOPE", 1.23)


def test_rollout_gates_for_xau_live():
  rollout = InstrumentRollout.LIVE
  assert permits_feed(rollout)
  assert permits_analysis(rollout)
  assert permits_public_delivery(rollout)
  assert permits_candidate_publication(rollout)
  assert permits_broker_execution(rollout)


def test_rollout_gates_feed_only_and_paper():
  assert permits_feed(InstrumentRollout.FEED_ONLY)
  assert not permits_analysis(InstrumentRollout.FEED_ONLY)
  assert not permits_candidate_publication(InstrumentRollout.FEED_ONLY)
  assert not permits_broker_execution(InstrumentRollout.FEED_ONLY)

  assert permits_analysis(InstrumentRollout.ANALYSIS_ONLY)
  assert not permits_public_delivery(InstrumentRollout.ANALYSIS_ONLY)
  assert not permits_candidate_publication(InstrumentRollout.ANALYSIS_ONLY)
  assert not permits_broker_execution(InstrumentRollout.ANALYSIS_ONLY)

  assert permits_candidate_publication(InstrumentRollout.PAPER)
  assert not permits_broker_execution(InstrumentRollout.PAPER)
  assert not permits_public_delivery(InstrumentRollout.PAPER)
