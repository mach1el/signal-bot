"""EURUSD vs GBPJPY session windows stay distinct while 1:2 stays locked."""

from __future__ import annotations

import pytest

from app.autotrade.killzone import evaluate_reaction_publish_window
from tests.test_config_effective_instrument_context import _load_production_example


pytestmark = pytest.mark.no_database


def _instrument(symbol: str):
  return _load_production_example().config.for_instrument(symbol)


def _window(symbol: str, hour: int):
  """Classify pair windows with require=True (clock math only)."""
  inst = _instrument(symbol)
  return evaluate_reaction_publish_window(
    hour=hour,
    cfg=inst,
    require=True,
  )


def test_fx_pair_windows_diverge_and_overlap_in_london():
  tokyo_eur = _window("EURUSD", 1)
  tokyo_gbp = _window("GBPJPY", 1)
  assert tokyo_eur.allowed is False
  assert tokyo_gbp.allowed is True

  # Mid-Tokyo (05 UTC = 14:00 JST) must stay open for JPY — the old 0-3
  # cut treated this as dead air and left crosses London/NY-shaped.
  mid_tokyo_eur = _window("EURUSD", 5)
  mid_tokyo_gbp = _window("GBPJPY", 5)
  mid_tokyo_usd = _window("USDJPY", 5)
  assert mid_tokyo_eur.allowed is False
  assert mid_tokyo_gbp.allowed is True
  assert mid_tokyo_usd.allowed is True

  ny_eur = _window("EURUSD", 13)
  ny_gbp = _window("GBPJPY", 13)
  assert ny_eur.allowed is True
  assert ny_gbp.allowed is False

  london_eur = _window("EURUSD", 8)
  london_gbp = _window("GBPJPY", 8)
  assert london_eur.allowed is True
  assert london_gbp.allowed is True

  ny_late_eur = _window("EURUSD", 14)
  ny_late_gbp = _window("GBPJPY", 14)
  assert ny_late_eur.allowed is True
  assert ny_late_gbp.allowed is False

  xau = _window("XAU", 13)
  assert xau.allowed is True
  assert _window("XAU", 1).allowed is False


def test_fx_pairs_keep_locked_two_r_while_windows_differ():
  eurusd = _instrument("EURUSD")
  gbpjpy = _instrument("GBPJPY")
  assert eurusd.execution.technique.reaction_publish_windows != (
    gbpjpy.execution.technique.reaction_publish_windows
  )
  assert eurusd.targeting.reward_risk == gbpjpy.targeting.reward_risk == 2.0
  assert eurusd.targeting.entry_clips == gbpjpy.targeting.entry_clips == 2


def test_prod_disables_reaction_publish_window_hard_gate():
  """Owner policy: structure/technique decide; UTC windows are not hard gates."""
  from app.autotrade.killzone import reaction_require_publish_window

  for symbol in ("XAU", "EURUSD", "GBPJPY", "USDJPY", "GBPUSD"):
    assert reaction_require_publish_window(_instrument(symbol)) is False

  # XAU/EURUSD closed at hour 1; soft require still allows with would_block.
  soft = evaluate_reaction_publish_window(
    hour=1, cfg=_instrument("XAU"), require=False,
  )
  assert soft.allowed is True
  assert soft.measured.get("would_block") is True
  soft_gbp = evaluate_reaction_publish_window(
    hour=14, cfg=_instrument("GBPJPY"), require=False,
  )
  assert soft_gbp.allowed is True
  assert soft_gbp.measured.get("would_block") is True


def test_publish_window_still_classifies_when_require_forced():
  dead = evaluate_reaction_publish_window(
    hour=14, cfg=_instrument("GBPJPY"), require=True,
  )
  live = evaluate_reaction_publish_window(
    hour=14, cfg=_instrument("EURUSD"), require=True,
  )
  assert dead.allowed is False
  assert dead.reason_code == "outside_reaction_publish_window"
  assert live.allowed is True
  soft = evaluate_reaction_publish_window(
    hour=14, cfg=_instrument("GBPJPY"), require=False,
  )
  assert soft.allowed is True
  assert soft.measured.get("would_block") is True
