"""FX 1:2 targeting helpers (no gold 5-rung ladder)."""

from __future__ import annotations

import pytest

from app.core.instrument_geometry import (
  FX_REWARD_RISK,
  is_fx,
  one_to_two_targets,
)
from app.scalping.strategies import _select_target

pytestmark = pytest.mark.no_database


def test_is_fx_canonical_names():
  assert is_fx("EURUSD")
  assert is_fx("gbpjpy")
  assert not is_fx("XAU")
  assert not is_fx("XAUUSD")


def test_one_to_two_targets_rounds_full_close():
  assert one_to_two_targets(12) == (24,)
  assert one_to_two_targets(15.4) == (31,)
  assert FX_REWARD_RISK == 2.0


def test_hfs_fx_selects_only_two_r_not_one_r():
  # Room fits 1R (15) but not 2R (30) → FX has no trade; gold would 1:1.
  gold = _select_target(
    direction="BUY",
    entry=1.16,
    room_pips=18,
    stop_pips=15,
    min_net=10,
    pip_size=0.0001,
    symbol="XAU",
  )
  fx = _select_target(
    direction="BUY",
    entry=1.16,
    room_pips=18,
    stop_pips=15,
    min_net=10,
    pip_size=0.0001,
    symbol="EURUSD",
  )
  assert gold is not None
  assert gold[1] == 15.0
  assert fx is None


def test_hfs_fx_takes_two_r_when_room_fits():
  target = _select_target(
    direction="SELL",
    entry=216.0,
    room_pips=40,
    stop_pips=15,
    min_net=10,
    pip_size=0.01,
    symbol="GBPJPY",
  )
  assert target is not None
  assert target[1] == 30.0


def test_fx_reaction_stop_envelope_is_twelve_to_twenty_five():
  from app.autotrade.protective_stop import stop_bounds_for_reaction_room
  from tests.test_config_effective_instrument_context import (
    _load_production_example,
  )

  cfg = _load_production_example().config
  fx_min, fx_max, fx_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=30,
    pip_size=0.0001,
    cfg=cfg,
    symbol="EURUSD",
  )
  gold_min, gold_max, gold_measured = stop_bounds_for_reaction_room(
    strategy="Key Level Reaction",
    primary_tp_pips=90,
    pip_size=0.1,
    cfg=cfg,
    symbol="XAU",
  )
  assert (fx_min, fx_max) == (12, 25)
  assert fx_measured["fx_one_to_two"] is True
  assert (gold_min, gold_max) == (60, 60)
  assert gold_measured["fx_one_to_two"] is False
