"""EURUSD vs GBPJPY session windows stay distinct while 1:2 stays locked."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from app.autotrade import worker
from app.autotrade.killzone import evaluate_reaction_publish_window, technique_enforce
from app.autotrade.route_outcome import route_outcome_key
from app.autotrade.setup_lifecycle import CONFIRMED, create_setup, transition_setup
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.core import instrument_geometry
from app.persistence import redis_state
from tests.test_config_effective_instrument_context import _load_production_example


pytestmark = pytest.mark.no_database


def _instrument(symbol: str):
  return _load_production_example().config.for_instrument(symbol)


def _window(symbol: str, hour: int):
  inst = _instrument(symbol)
  return evaluate_reaction_publish_window(
    hour=hour,
    cfg=inst,
    require=technique_enforce(inst),
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


async def _confirm_setup(client, match: StrategyMatch) -> None:
  await create_setup(
    client,
    setup_id=match.match_id,
    thesis_id=match.thesis_id,
    symbol=match.symbol,
    source_structure_id=match.structural_zone_id,
    formation_timeframe=match.structural_timeframe,
    expires_at=match.expires_at,
  )
  for state in ("watching", "touched", "forming", CONFIRMED):
    await transition_setup(client, match.match_id, state)


def _key_level_match(*, symbol: str, match_id: str, **prices: float) -> StrategyMatch:
  now = int(time.time())
  return StrategyMatch(
    version=STRATEGY_MATCH_VERSION,
    match_id=match_id,
    symbol=symbol,
    source_tf="M5",
    event_ts=str(now - 60),
    issued_at=now - 60,
    expires_at=now + 900,
    strategy="Key Level Reaction",
    strategy_mode="with_bias",
    direction="SELL",
    key_level=prices["key_level"],
    entry_low=prices["entry_low"],
    entry_high=prices["entry_high"],
    current_price=prices["current_price"],
    confluence=3,
    reasons=("strong reclaim",),
    atr=prices["atr"],
    structure_swing=prices["structure_swing"],
    targets_pips=(100, 200),
    family="key_level",
    structural_source="key_level",
    structural_zone_id=prices["zone_id"],
    structural_zone_low=prices["entry_low"],
    structural_zone_high=prices["entry_high"],
    structural_kind="resistance",
    structural_timeframe="M5",
    htf_bias="down",
    regime_kind="trend",
    thesis_id=f"{match_id}-thesis",
  )


@pytest.mark.asyncio
async def test_worker_ny_hour_blocks_gbpjpy_window_only(monkeypatch):
  from app.autotrade import killzone as kz

  prod = _load_production_example().config
  monkeypatch.setattr("app.core.config.runtime_config", prod)
  monkeypatch.setattr(instrument_geometry, "runtime_config", prod)

  real_win = kz.evaluate_reaction_publish_window

  def _frozen(*, ts=None, hour=None, cfg=None, require=True):
    return real_win(ts=None, hour=14, cfg=cfg, require=require)

  monkeypatch.setattr(kz, "evaluate_reaction_publish_window", _frozen)
  monkeypatch.setattr(worker, "event_in_window", AsyncMock(return_value=None))

  client = redis_state.get_client()
  gbp = _key_level_match(
    symbol="GBPJPY",
    match_id="gbpjpy-ny-window",
    key_level=190.04,
    entry_low=190.00,
    entry_high=190.08,
    current_price=190.04,
    atr=0.12,
    structure_swing=190.12,
    zone_id="gbpjpy-supply-190.00",
  )
  eurusd = _key_level_match(
    symbol="EURUSD",
    match_id="eurusd-ny-window",
    key_level=1.1604,
    entry_low=1.1600,
    entry_high=1.1608,
    current_price=1.1604,
    atr=0.0008,
    structure_swing=1.1612,
    zone_id="eurusd-supply-1.1600",
  )
  await _confirm_setup(client, gbp)
  await _confirm_setup(client, eurusd)

  gbp_spot = worker.AutoTradeSpot(
    price=190.04, ts=int(time.time()), fresh=True, bid=190.03, ask=190.05,
  )
  eur_spot = worker.AutoTradeSpot(
    price=1.1604, ts=int(time.time()), fresh=True, bid=1.1603, ask=1.1605,
  )

  assert await worker._publish_trade_plan_v8(
    client, "GBPJPY", gbp_spot, gbp,
  ) is None
  gbp_raw = await client.get(route_outcome_key("GBPJPY", gbp.match_id))
  assert gbp_raw is not None
  gbp_outcome = json.loads(gbp_raw)
  assert gbp_outcome["reason_code"] == "outside_reaction_publish_window"
  assert gbp_outcome["status"] == "waiting"
  assert instrument_geometry.instrument_runtime("GBPJPY").identity.canonical_symbol == (
    "GBPJPY"
  )

  await worker._publish_trade_plan_v8(client, "EURUSD", eur_spot, eurusd)
  eur_raw = await client.get(route_outcome_key("EURUSD", eurusd.match_id))
  if eur_raw is not None:
    assert json.loads(eur_raw)["reason_code"] != "outside_reaction_publish_window"
