from __future__ import annotations

from dataclasses import replace
import time

import pytest
from pydantic import ValidationError

from app.autotrade.execution_confirmation import (
  IN_ZONE_WAITING_M1,
  ExecutionConfirmationState,
  confirmation_policy_for,
  deterministic_episode_id,
  executable_quote_in_zone,
  executable_within_distance,
  load_execution_confirmation,
  save_execution_confirmation,
)
from app.autotrade.strategy_match import STRATEGY_MATCH_VERSION, StrategyMatch
from app.persistence import redis_state


pytestmark = pytest.mark.no_database
from app.core.config import Settings


def _match(**overrides) -> StrategyMatch:
  now = int(time.time())
  base = dict(
    version=STRATEGY_MATCH_VERSION,
    match_id="reaction-confirmation-1",
    symbol="XAU",
    source_tf="M5",
    event_ts=str(now - 60),
    issued_at=now - 60,
    expires_at=now + 600,
    strategy="Key Level Reaction",
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4040.23,
    entry_low=4038.36,
    entry_high=4042.09,
    current_price=4038.41,
    confluence=3,
    reasons=("strong reclaim",),
    atr=4.0,
    structure_swing=4045.0,
    targets_pips=(100, 200),
    family="key_level",
    structural_source="key_level",
    structural_zone_id="key-level-4040",
    structural_zone_low=4038.36,
    structural_zone_high=4042.09,
    touch_bar_ts=str(now - 120),
    confirmation_bar_ts=str(now - 60),
    reaction_type="strong_reclaim",
    structural_kind="resistance",
    structural_timeframe="M5",
    htf_bias="down",
    regime_kind="trend",
    thesis_id="thesis-key-level-4040",
  )
  base.update(overrides)
  return StrategyMatch(**base)


def test_confirmation_policy_is_authoritative_only_with_complete_reaction_evidence():
  policy = confirmation_policy_for(_match())

  assert policy.reaction_family is True
  assert policy.metadata_valid is True
  assert policy.m5_authoritative is True
  assert policy.m1_required_on_retest is False
  assert policy.allow_same_cycle_publish is True
  assert policy.require_quote_inside_zone is False

  missing = confirmation_policy_for(
    replace(_match(), confirmation_bar_ts=None),
  )
  assert missing.reaction_family is True
  assert missing.metadata_valid is False
  assert missing.m5_authoritative is False
  assert missing.reason_code == "confirmation_metadata_missing"

  non_reaction = confirmation_policy_for(
    replace(
      _match(),
      strategy="Trend Pullback",
      family="trend_pullback",
      reaction_type=None,
      touch_bar_ts=None,
      confirmation_bar_ts=None,
    ),
  )
  assert non_reaction.reaction_family is False
  assert non_reaction.m5_authoritative is False
  assert non_reaction.m1_required_on_retest is False
  assert non_reaction.allow_same_cycle_publish is True
  assert non_reaction.reason_code == "distance_proximity"


@pytest.mark.parametrize(
  ("direction", "bid", "ask", "inside", "quote_side", "quote"),
  (
    ("SELL", 4038.41, 4038.61, True, "bid", 4038.41),
    ("SELL", 4038.05, 4038.41, False, "bid", 4038.05),
    ("BUY", 4038.21, 4038.41, True, "ask", 4038.41),
    ("BUY", 4041.90, 4042.40, False, "ask", 4042.40),
  ),
)
def test_executable_zone_membership_uses_side_aware_quote(
  direction, bid, ask, inside, quote_side, quote,
):
  evidence = executable_quote_in_zone(
    direction,
    bid,
    ask,
    4038.36,
    4042.09,
    0.0,
    pip_size=0.1,
  )

  assert evidence.inside is inside
  assert evidence.quote_side == quote_side
  assert evidence.executable_quote == quote


@pytest.mark.parametrize(
  ("quote", "distance_pips", "eligible"),
  (
    (4036.86, 15.0, True),
    (4032.36, 60.0, False),
  ),
)
def test_execute_or_retest_uses_distance_to_nearest_zone_edge(
  quote, distance_pips, eligible,
):
  evidence = executable_quote_in_zone(
    "SELL",
    quote,
    quote + 0.2,
    4038.36,
    4042.09,
    0.0,
    pip_size=0.1,
  )

  assert evidence.distance_pips == pytest.approx(distance_pips)
  assert executable_within_distance(evidence, 40.0) is eligible


def test_episode_id_is_deterministic_and_changes_on_new_zone_entry():
  first = deterministic_episode_id(
    "setup-1", "SELL", 4038.36, 4042.09, 1_700_000_000,
  )
  repeated = deterministic_episode_id(
    "setup-1", "SELL", 4038.36, 4042.09, 1_700_000_000,
  )
  second = deterministic_episode_id(
    "setup-1", "SELL", 4038.36, 4042.09, 1_700_000_060,
  )

  assert first == repeated
  assert first != second


@pytest.mark.asyncio
async def test_execution_confirmation_state_round_trips_across_restart_contexts():
  client = redis_state.get_client()
  state = ExecutionConfirmationState(
    setup_id="setup-restart",
    phase=IN_ZONE_WAITING_M1,
    episode_id="episode-1",
    zone_entered_at=1_700_000_000,
    zone_exited_at=None,
    last_inside_at=1_700_000_010,
    last_evaluated_m1_ts=1_700_000_000,
    trigger_bar_ts=None,
    trigger_pattern=None,
    trigger_source=None,
    trigger_consumed=False,
    updated_at=1_700_000_010,
  )

  await save_execution_confirmation(
    client,
    state,
    expires_at=int(time.time()) + 300,
  )

  restored = await load_execution_confirmation(client, "setup-restart")
  assert restored == state


def test_retest_trigger_validity_window_is_conservative_and_validated():
  assert Settings(_env_file=None).auto_trade_retest_trigger_validity_bars == 2
  with pytest.raises(ValidationError, match="must be between 1 and 5"):
    Settings(
      _env_file=None,
      AUTO_TRADE_RETEST_TRIGGER_VALIDITY_BARS=0,
    )


def test_distance_confluence_and_actionability_defaults_are_aligned():
  configured = Settings(_env_file=None)

  assert configured.auto_trade_execute_max_distance_pips == 40.0
  assert configured.auto_trade_max_entry_distance_pips == 40.0
  assert configured.zone_merge_max_width == 6.0
  assert configured.scanner_actionability_gate_enabled is False
  assert configured.key_level_role_ambiguity_gate_enabled is False
  assert configured.range_context_disagreement_gate_enabled is False

  with pytest.raises(ValidationError, match="must be equal and positive"):
    Settings(
      _env_file=None,
      AUTO_TRADE_EXECUTE_MAX_DISTANCE_PIPS=40,
      auto_trade_max_entry_distance_pips=10,
    )
