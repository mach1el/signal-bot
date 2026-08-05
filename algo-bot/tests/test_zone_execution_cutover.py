from __future__ import annotations
from app.core.config import runtime_config
from tests.configuration.canonical_fixtures import install_runtime_overrides, leaf

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.autotrade.strategy_match import StrategyMatch
from app.autotrade import worker
from app.autotrade import zone_execution_cutover as cutover
from app.autotrade import zone_watch as zw


pytestmark = [pytest.mark.no_database, pytest.mark.real_redis]


@pytest_asyncio.fixture
async def client():
  url = os.getenv("REAL_REDIS_URL")
  if not url:
    pytest.skip("REAL_REDIS_URL is required for ZoneWatch cutover tests")
  redis = Redis.from_url(url, decode_responses=True)
  await redis.ping()
  await redis.flushdb()
  try:
    yield redis
  finally:
    await redis.flushdb()
    await redis.aclose()


def _match(*, strategy: str = "Key Level Reaction") -> StrategyMatch:
  return StrategyMatch(
    version=1,
    match_id="setup-1",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy=strategy,
    strategy_mode="with_bias",
    direction="SELL",
    key_level=4114.5,
    entry_low=4113.0,
    entry_high=4116.0,
    current_price=4114.5,
    confluence=3,
    reasons=("supply rejection",),
    atr=4.0,
    structure_swing=4116.0,
    targets_pips=(300,),
    family="key_level" if strategy != "Range Edge Scalp" else "range",
    structural_source="key_level",
    confluence_zone_id="zone-1",
    structural_zone_id="zone-1",
    structural_zone_low=4113.0,
    structural_zone_high=4116.0,
    touch_bar_ts="1785390000",
    confirmation_bar_ts="1785390300",
    reaction_type="rejection_choch",
  )


def _result(
  *,
  low: float = 4113.0,
  high: float = 4116.0,
  setup="Key Level Reaction",
  structural_source: str = "key_level",
):
  return SimpleNamespace(
    setup=setup,
    direction="SELL",
    entry_zone=SimpleNamespace(low=low, high=high),
    structural_low=low,
    structural_high=high,
    structural_timeframe="M15",
    structural_source=structural_source,
    structural_id="zone-1",
    confluence_zone_id="zone-1",
    confluence_tags=("key_level", "supply"),
    confluence=3,
    source_score=12.0,
    confirmation_type="rejection_choch",
    confirmation="rejection_choch",
    execution_eligibility=SimpleNamespace(allowed=True, market_map_id="map-1"),
  )


def test_grade_policy_is_explicit():
  assert cutover._grade(_result(), "M15") == zw.GRADE_A
  assert cutover._grade(_result(setup="Range Edge Scalp"), "M5") == zw.GRADE_B


@pytest.mark.asyncio
async def test_structural_zone_width_rejects_when_gate_enabled(client, monkeypatch):
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": True})
  eligible = await cutover._record_width_telemetry(
    client,
    symbol="XAU",
    zone_id="narrow",
    result=_result(
      low=4113.0, high=4113.5, structural_source="supply_demand",
    ),
    source_tf="M5",
  )
  assert eligible is False
  raw = await client.get(cutover.width_telemetry_key("XAU", "narrow"))
  assert raw is not None
  assert '"rejection_reason":"zone_too_narrow"' in raw


@pytest.mark.asyncio
async def test_structural_zone_width_gate_disabled_flag_actually_disables_it(
  client, monkeypatch,
):
  """Section 4: SCANNER_ZONE_WIDTH_GATE_ENABLED=false must actually disable
  scanner-stage structural width rejection - the cutover previously ignored
  this flag entirely ("the cutover makes the width contract canonical"),
  silently re-enabling a gate the operator explicitly turned off.
  """
  install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": False})
  eligible = await cutover._record_width_telemetry(
    client,
    symbol="XAU",
    zone_id="narrow",
    result=_result(
      low=4113.0, high=4113.5, structural_source="supply_demand",
    ),
    source_tf="M5",
  )
  # Telemetry still records the true (failing) width result...
  raw = await client.get(cutover.width_telemetry_key("XAU", "narrow"))
  assert raw is not None
  assert '"rejection_reason":"zone_too_narrow"' in raw
  assert '"eligible":false' in raw
  # ...but with the gate off, this must not be able to reject the zone.
  assert eligible is True


@pytest.mark.asyncio
async def test_level_band_is_never_width_rejected_regardless_of_the_gate(
  client, monkeypatch,
):
  """Section 4: a key level / session level / trendline reaction is a
  LEVEL_BAND, not a merged STRUCTURAL_ZONE - it must never be rejected only
  for being narrower than XAU_ZONE_MIN_WIDTH_PRICE, whether the width gate
  is on or off.
  """
  for gate_enabled in (True, False):
    install_runtime_overrides(monkeypatch, legacy_overrides={"scanner_zone_width_gate_enabled": gate_enabled,})
    eligible = await cutover._record_width_telemetry(
      client,
      symbol="XAU",
      zone_id=f"level-{gate_enabled}",
      result=_result(
        low=4113.0, high=4114.2, structural_source="key_level",
      ),
      source_tf="M5",
    )
    assert eligible is True


@pytest.mark.asyncio
async def test_zone_presence_counts_one_touch_per_visit(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    now=100,
  )
  await zw.transition_zone_watch(client, record.zone_id, zw.WATCHING_RETEST)
  first, entered = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=200, htf_evidence=True,
  )
  second, entered_again = await zw.record_zone_presence(
    client, record.zone_id, inside=True, now=260, htf_evidence=True,
  )
  assert entered is True
  assert entered_again is False
  assert first.touch_count == second.touch_count == 1
  assert first.episode_id == second.episode_id


@pytest.mark.asyncio
async def test_rediscovery_refreshes_metadata_without_resetting_touch(client):
  record, _ = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
    now=100,
  )
  touched = await zw.record_zone_touch(client, record.zone_id, now=150)
  refreshed, created = await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4112.9,
    high=4116.1,
    source_timeframe="M15",
    structural_sources=("key_level", "supply_demand"),
    confluence_tags=("key_level", "supply"),
    grade=zw.GRADE_A,
    score=14.0,
    now=300,
  )
  assert created is False
  assert refreshed.touch_count == touched.touch_count == 1
  assert refreshed.last_confirmed_at == 300
  assert refreshed.low == pytest.approx(4112.9)
  assert refreshed.score == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_active_plan_state_race_is_reconciled(monkeypatch):
  match = _match()
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(return_value=worker.PublishResult(
      status=worker.PUBLISH_STATUS_REMAINED_WATCHING,
      plan_id="v7:setup-1",
      reason_code="zone_watching_retest",
      zone_id="zone-1",
      setup_id="setup-1",
    )),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v7_state",
    AsyncMock(return_value=worker.ExistingV7State(
      plan_id="v7:setup-1",
      setup_state="plan_published",
      plan_state="armed",
      plan_exists=True,
      already_published=True,
      already_terminal=False,
      owner_matches=True,
    )),
  )
  result = await cutover._safe_direct_publish(
    SimpleNamespace(), match, symbol="XAU",
  )
  assert result.status == worker.PUBLISH_STATUS_DUPLICATE_RECONCILED


@pytest.mark.asyncio
async def test_direct_exception_returns_durable_fallback(monkeypatch):
  match = _match()
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(side_effect=RuntimeError("redis blip")),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v7_state",
    AsyncMock(return_value=worker.ExistingV7State(
      plan_id="v7:setup-1",
      setup_state="worker_acknowledged",
      plan_state=None,
      plan_exists=False,
      already_published=False,
      already_terminal=False,
      owner_matches=False,
    )),
  )
  result = await cutover._safe_direct_publish(
    SimpleNamespace(), match, symbol="XAU",
  )
  assert result.status == worker.PUBLISH_STATUS_REMAINED_WATCHING
  assert result.reason_code == "direct_publish_failed_durable_fallback"


@pytest.mark.asyncio
async def test_waiting_zone_creates_no_setup_or_ready_event(client, monkeypatch):
  from app.analysis import scanner

  result = _result()
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (_match(), None, {}),
  )
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4100.0, 4100.2, 1_785_390_000)),
  )

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is None
  assert await client.get("analysis:setup:setup-1") is None
  assert await client.xlen("auto_trade:strategy_match_ready") == 0
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state == zw.WATCHING_RETEST


@pytest.mark.asyncio
async def test_grade_b_zone_publishes_without_waiting_for_an_m1_trigger(
  client, monkeypatch,
):
  """Shadow/default activation mode preserves legacy zone-touch publish.

  Enforce mode (separate tests) requires a fresh episode-scoped M1 reaction
  before StrategyMatch persistence / direct publication.
  """
  from app.analysis import scanner

  result = _result(setup="Range Edge Scalp")
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (_match(strategy="Range Edge Scalp"), None, {}),
  )
  # Quote is inside the 4113-4116 zone: this candidate is executable now.
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, 1_785_390_000)),
  )
  monkeypatch.setattr(
    cutover, "_m1_trigger_for_zone", AsyncMock(return_value=None),
  )
  direct_publish = AsyncMock(return_value=worker.PublishResult(
    status=worker.PUBLISH_STATUS_PUBLISHED,
    plan_id="v7:setup-1",
    reason_code="candidate_published",
    zone_id="zone-1",
    setup_id="setup-1",
  ))
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )

  assert published is not None
  direct_publish.assert_awaited_once()
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state == zw.PUBLISHED_LOCKED
  assert watched.last_plan_id == "v7:setup-1"


@pytest.mark.asyncio
async def test_enforce_reaction_waits_for_m1_without_persisting(
  client, monkeypatch,
):
  """Quote-inside-zone alone must not activate reaction setups in enforce."""
  from app.analysis import scanner
  from app.analysis.m1_trigger import M1TriggerResult

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "shadow",
    },
  )
  result = _result(setup="Key Level Reaction")
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (_match(), None, {}),
  )
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, 1_785_390_100)),
  )
  monkeypatch.setattr(
    cutover, "_m1_trigger_for_zone", AsyncMock(return_value=None),
  )
  direct_publish = AsyncMock()
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)
  persist = AsyncMock(side_effect=AssertionError("must not persist"))
  monkeypatch.setattr(cutover, "_persist_match", persist)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is None
  direct_publish.assert_not_awaited()
  persist.assert_not_awaited()
  assert await client.get("analysis:setup:setup-1") is None
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state not in zw.TERMINAL_ZONE_WATCH_STATES | zw.LOCKED_ZONE_WATCH_STATES
  last = await client.get("auto_trade:last_entry_activation:XAU")
  assert last is not None
  assert "reaction_trigger_missing" in last


@pytest.mark.asyncio
async def test_enforce_reaction_activates_with_fresh_m1_once(
  client, monkeypatch,
):
  from dataclasses import replace
  from app.analysis import scanner
  from app.analysis.m1_trigger import M1TriggerResult

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "shadow",
    },
  )
  result = _result(setup="Key Level Reaction")
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  match = replace(
    _match(),
    range_low=4100.0,
    range_high=4200.0,
    confirmation_bar_ts=None,
    reaction_type=None,
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (match, None, {}),
  )
  entered_at = 1_785_390_050
  now_ts = 1_785_390_200
  await zw.discover_zone_watch(
    client,
    zone_id="zone-1",
    symbol="XAU",
    direction="SELL",
    low=4113.0,
    high=4116.0,
    source_timeframe="M15",
    structural_sources=("key_level",),
    confluence_tags=("key_level",),
    grade=zw.GRADE_A,
    now=entered_at - 10,
  )
  await zw.record_zone_presence(
    client, "zone-1", inside=True, now=entered_at, htf_evidence=True,
  )
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4114.4, 4114.6, now_ts)),
  )
  trigger = M1TriggerResult(
    "wick_rejection", "SELL", 4116.0, entered_at + 30, "sell rejection",
  )
  monkeypatch.setattr(
    cutover, "_m1_trigger_for_zone", AsyncMock(return_value=trigger),
  )
  direct_publish = AsyncMock(return_value=worker.PublishResult(
    status=worker.PUBLISH_STATUS_PUBLISHED,
    plan_id="v7:setup-1",
    reason_code="candidate_published",
    zone_id="zone-1",
    setup_id="setup-1",
  ))
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:02:00+00:00",
    ctx,
    [result],
    require_static_eligibility=True,
  )
  assert published is not None
  direct_publish.assert_awaited_once()
  watched = await zw.load_zone_watch(client, "zone-1")
  assert watched is not None
  assert watched.state == zw.PUBLISHED_LOCKED


@pytest.mark.asyncio
async def test_enforce_location_blocks_buy_after_premium_rally(
  client, monkeypatch,
):
  from app.analysis import scanner
  from app.analysis.m1_trigger import M1TriggerResult

  install_runtime_overrides(
    monkeypatch,
    overrides={
      "execution.activation.mode": "enforce",
      "actionability.entry_location.mode": "enforce",
    },
  )
  buy_result = SimpleNamespace(
    setup="Zone Reaction",
    direction="BUY",
    entry_zone=SimpleNamespace(low=4078.0, high=4082.0),
    structural_low=4078.0,
    structural_high=4082.0,
    structural_timeframe="M15",
    structural_source="key_level",
    structural_id="zone-buy",
    confluence_zone_id="zone-buy",
    confluence_tags=("key_level", "demand"),
    confluence=3,
    source_score=12.0,
    confirmation_type="wick_rejection",
    confirmation="wick_rejection",
    execution_eligibility=SimpleNamespace(allowed=True, market_map_id="map-1"),
  )
  buy_match = StrategyMatch(
    version=1,
    match_id="setup-buy",
    symbol="XAU",
    source_tf="M5",
    event_ts="2026-07-30T06:00:00+00:00",
    issued_at=1_785_390_000,
    expires_at=1_785_390_420,
    strategy="Zone Reaction",
    strategy_mode="with_bias",
    direction="BUY",
    key_level=4080.0,
    entry_low=4078.0,
    entry_high=4082.0,
    current_price=4080.0,
    confluence=3,
    reasons=("demand",),
    atr=4.0,
    structure_swing=4078.0,
    targets_pips=(300,),
    family="key_level",
    structural_source="key_level",
    confluence_zone_id="zone-buy",
    structural_zone_id="zone-buy",
    structural_zone_low=4078.0,
    structural_zone_high=4082.0,
    range_low=4000.0,
    range_high=4100.0,
  )
  ctx = SimpleNamespace(
    indicators={"M5": SimpleNamespace(atr=pd.Series([4.0]))},
  )
  monkeypatch.setattr(scanner, "_result_rank", lambda _result: (0,))
  monkeypatch.setattr(scanner, "_pip_size", lambda _symbol: 0.01)
  monkeypatch.setattr(
    scanner,
    "_build_one_strategy_match",
    lambda *_args, **_kwargs: (buy_match, None, {}),
  )
  now_ts = 1_785_390_200
  # Ask in premium (~0.80) inside the zone band for geometry test.
  monkeypatch.setattr(
    cutover,
    "_load_quote",
    AsyncMock(return_value=(4079.5, 4080.5, now_ts)),
  )
  # Force zone presence "inside" even though dealing range says premium.
  monkeypatch.setattr(
    cutover,
    "_quote_evidence",
    lambda record, quote: SimpleNamespace(
      inside=True,
      executable_quote=quote[1],
      side="ask",
    ),
  )
  monkeypatch.setattr(
    cutover,
    "_m1_trigger_for_zone",
    AsyncMock(return_value=M1TriggerResult(
      "wick_rejection", "BUY", 4078.0, now_ts - 30, "buy rejection",
    )),
  )
  direct_publish = AsyncMock()
  monkeypatch.setattr(cutover, "_ORIGINAL_DIRECT_PUBLISH", direct_publish)

  published = await cutover._sync_strategy_match_cutover(
    client,
    "XAU",
    "M5",
    "2026-07-30T06:00:00+00:00",
    ctx,
    [buy_result],
    require_static_eligibility=True,
  )
  assert published is None
  direct_publish.assert_not_awaited()
  last = await client.get("auto_trade:last_entry_location:XAU")
  assert last is not None
  assert "buy_in_premium" in last or "buy_at_range_extreme" in last


@pytest.mark.asyncio
async def test_safe_direct_publish_ensures_plan_published_root_card(monkeypatch):
  """Direct publish must ensure the PLAN PUBLISHED root card exists.

  ZoneWatch cutover suppresses SETUP FORMING until publish and the M1
  publish path never calls scanner._notify_digest_once, so without this
  ensure the owner can trade a setup with no Telegram root card.
  """
  match = _match()
  ensure = AsyncMock()
  monkeypatch.setattr(cutover, "_ensure_published_root_card", ensure)
  monkeypatch.setattr(
    cutover,
    "_ORIGINAL_DIRECT_PUBLISH",
    AsyncMock(return_value=worker.PublishResult(
      status=worker.PUBLISH_STATUS_PUBLISHED,
      plan_id="v7:setup-1",
      reason_code="candidate_published",
      zone_id="zone-1",
      setup_id="setup-1",
    )),
  )
  monkeypatch.setattr(
    worker,
    "resolve_existing_v7_state",
    AsyncMock(return_value=SimpleNamespace(
      already_published=True,
      plan_id="v7:setup-1",
    )),
  )

  cutover._PUBLISHED_SETUP_IDS.discard(match.match_id)
  result = await cutover._safe_direct_publish(
    object(),
    match,
    symbol="XAU",
    event_ts=match.event_ts,
  )

  assert result.status in {
    worker.PUBLISH_STATUS_PUBLISHED,
    worker.PUBLISH_STATUS_DUPLICATE_RECONCILED,
  }
  assert match.match_id in cutover._PUBLISHED_SETUP_IDS
  ensure.assert_awaited_once()
  assert ensure.await_args.args[1] is match


def test_format_detection_cutover_suppresses_the_card_before_publication(
  monkeypatch,
):
  """Section 12 of the direct-publish spec: no new card may show SETUP
  FORMING/QUEUED/worker-acknowledgement-pending - the first real card is
  PLAN PUBLISHED. Under the cutover there is no worker/preflight/armed
  queue left to describe, so the old formatter's pre-publication text
  (still generated by _ORIGINAL_FORMAT, unmodified) must never reach
  Telegram; _notify_digest_once skips sending on empty text.
  """
  monkeypatch.setattr(
    cutover, "_ORIGINAL_FORMAT", lambda *a, **k: "SETUP FORMING stub",
  )
  match = _match()
  # _PUBLISHED_SETUP_IDS is a module-level set other tests also touch with
  # this same match_id ("setup-1") - start from a clean slate.
  cutover._PUBLISHED_SETUP_IDS.discard(match.match_id)

  unpublished_text = cutover._format_detection_cutover(
    "XAU", "M5", None, None, [], [], None, match,
  )
  assert unpublished_text == ""

  cutover._PUBLISHED_SETUP_IDS.add(match.match_id)
  try:
    monkeypatch.setattr(
      cutover,
      "_ORIGINAL_FORMAT",
      lambda *a, **k: (
        "🟡 <b>QUEUED</b> · worker acknowledgement pending\n"
        "→ Review confirmation, SL &amp; TP before posting."
      ),
    )
    published_text = cutover._format_detection_cutover(
      "XAU", "M5", None, None, [], [], None, match,
    )
  finally:
    cutover._PUBLISHED_SETUP_IDS.discard(match.match_id)

  assert "PLAN PUBLISHED" not in published_text
  assert "QUEUED" in published_text
  assert "Executor owns mechanical entry" in published_text
