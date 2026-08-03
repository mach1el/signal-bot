from types import SimpleNamespace

from tests.configuration.canonical_fixtures import map_strategy_cfg

import pandas as pd
import pytest

from app.analysis.market_map import MapEntry, MarketMap
from app.autotrade import map_strategy
from app.autotrade.strategy_match import StrategyMatch


pytestmark = pytest.mark.no_database


def _m1_bar(
  *,
  open_: float = 4150.99,
  high: float = 4152.92,
  low: float = 4150.98,
  close: float = 4151.79,
) -> pd.DataFrame:
  return pd.DataFrame({
    "open": [open_],
    "high": [high],
    "low": [low],
    "close": [close],
    "volume": [500.0],
  }, index=pd.date_range("2026-07-22 14:45", periods=1, freq="1min", tz="UTC"))


def _map(
  *entries: MapEntry,
  bias: str = "down",
  price: float = 4149.0,
  eq: float = 4149.0,
  box_low: float = 4144.0,
  box_high: float = 4153.0,
  actionable_entries: tuple[MapEntry, ...] | None = None,
) -> MarketMap:
  # In production (app/analysis/market_map.py build_market_map),
  # actionable_entries is the uncapped structural pool and entries is the
  # display-capped/round-fallback-padded list Telegram renders - the two
  # are not always equal. Test callers that only care about one entry set
  # (the overwhelming majority of these tests) get both fields populated
  # identically by default so they don't have to think about the split;
  # tests that specifically exercise the display/strategy divergence pass
  # actionable_entries explicitly.
  return MarketMap(
    entries=list(entries),
    price=price,
    eq=eq,
    box_low=box_low,
    box_high=box_high,
    bias=bias,
    bias_tf="H1",
    actionable_entries=(
      list(entries) if actionable_entries is None else list(actionable_entries)
    ),
  )


def _supply() -> MapEntry:
  return MapEntry(
    "sell",
    4152.97,
    4153.37,
    4152,
    4154,
    "zone",
    ["supply", "FVG", "fresh"],
    8.0,
  )


def _cfg(**overrides) -> SimpleNamespace:
  values = {
    "auto_trade_mapped_zone_enabled": True,
    "auto_trade_max_entry_distance_pips": 10,
    "auto_trade_strategy_match_max_age_seconds": 420,
    "auto_trade_tp_pips": "30,60,90,120,200",
    "auto_trade_map_zone_min_width_atr": 0.15,
    "auto_trade_map_zone_min_width_abs": 1.0,
    "auto_trade_map_counter_bias_enabled": True,
    "auto_trade_map_counter_bias_min_score": 6.0,
    "auto_trade_map_counter_bias_min_confluence": 2,
    "auto_trade_map_track_distance_atr": 8.0,
    "auto_trade_map_execute_distance_atr": 1.5,
    "auto_trade_map_execute_tolerance_pips": 0.0,
    "auto_trade_map_execute_tolerance_atr": 0.0,
    "atr_length": 14,
    "proximal_band_atr": 0.5,
  }
  values.update(overrides)
  return map_strategy_cfg(**values)


def test_m1_rejection_no_longer_produces_a_candidate(monkeypatch):
  # M1 mapped-zone reaction is retired as a setup source (H1->M15->M5
  # single-analysis-source cutover, P2): inputs that previously produced a
  # "candidate" (a clean M1 rejection at an executable structural zone) must
  # now stop at "waiting_for_touch" with no match - the structural pool
  # still tracks the zone, it just no longer has any trigger mechanism.
  m1 = _m1_bar()
  frames = {tf: m1 for tf in ("M1", "M5", "M15", "H1")}
  market_map = _map(
    MapEntry("sell", 4150.0, 4150.0, 4150, 4151, "level", ["round"], 1.0),
    _supply(),
  )
  monkeypatch.setattr(
    map_strategy,
    "atr_indicator",
    lambda *args: pd.Series([1.8]),
  )
  cfg = _cfg(
    auto_trade_tp_pips="30,60,90",
    auto_trade_map_zone_min_width_abs=0.3,
  )

  decision = map_strategy.evaluate_market_map_strategy(
    frames,
    symbol="XAU",
    event_ts="1784731500",
    spot_price=4151.79,
    cfg=cfg,
    market_map=market_map,
    now=1784731560,
  )

  assert decision.state == "waiting_for_touch"
  assert decision.match is None
  assert decision.mapped_zone is None
  assert "nearest mapped SELL zone 4152.97-4153.37" in decision.reasons[0]


def test_round_number_fallback_is_never_executable():
  round_only = _map(
    MapEntry("sell", 4150.0, 4150.0, 4150, 4151, "level", ["round"], 1.0),
  )

  selected, state, reasons = map_strategy._select_reaction(
    round_only,
    _m1_bar(high=4150.2, low=4148.8, close=4149.0),
    4149.0,
    1.8,
    0.5,
    _cfg(auto_trade_map_zone_min_width_abs=0.3),
  )

  assert selected is None
  assert state == "waiting_for_zone"
  assert "no structural mapped SELL zone" in reasons[0]


def test_display_capped_zone_is_still_executable_from_actionable_pool(monkeypatch):
  # Reproduces the display/strategy zone bug from
  # docs/adr-trade-plan-v7-boundary.md: the supply zone was ranked out of
  # the Telegram-capped `entries` list, but it is still in the uncapped
  # `actionable_entries` structural pool. Selection must use the pool, not
  # the display list, so a zone Telegram doesn't show can still be traded.
  m1 = _m1_bar()
  frames = {tf: m1 for tf in ("M1", "M5", "M15", "H1")}
  round_level = MapEntry("sell", 4150.0, 4150.0, 4150, 4151, "level", ["round"], 1.0)
  supply = _supply()
  market_map = _map(
    round_level,
    actionable_entries=(round_level, supply),
  )
  monkeypatch.setattr(
    map_strategy, "atr_indicator", lambda *args: pd.Series([1.8]),
  )
  cfg = _cfg(
    auto_trade_tp_pips="30,60,90",
    auto_trade_map_zone_min_width_abs=0.3,
  )

  decision = map_strategy.evaluate_market_map_strategy(
    frames,
    symbol="XAU",
    event_ts="1784731500",
    spot_price=4151.79,
    cfg=cfg,
    market_map=market_map,
    now=1784731560,
  )

  assert decision.state == "waiting_for_touch"
  assert "nearest mapped SELL zone 4152.97-4153.37" in decision.reasons[0]


def test_zone_only_in_display_list_is_not_executable(monkeypatch):
  # Converse of the above: a zone present only in the display-capped
  # `entries` list (e.g. it lost genuine structural status since the map
  # was built) must not be executable just because Telegram still shows it.
  m1 = _m1_bar()
  frames = {tf: m1 for tf in ("M1", "M5", "M15", "H1")}
  round_level = MapEntry("sell", 4150.0, 4150.0, 4150, 4151, "level", ["round"], 1.0)
  supply = _supply()
  market_map = _map(
    supply,
    actionable_entries=(round_level,),
  )
  monkeypatch.setattr(
    map_strategy, "atr_indicator", lambda *args: pd.Series([1.8]),
  )
  cfg = _cfg(
    auto_trade_tp_pips="30,60,90",
    auto_trade_map_zone_min_width_abs=0.3,
  )

  decision = map_strategy.evaluate_market_map_strategy(
    frames,
    symbol="XAU",
    event_ts="1784731500",
    spot_price=4151.79,
    cfg=cfg,
    market_map=market_map,
    now=1784731560,
  )

  assert decision.state != "candidate"
  assert decision.mapped_zone is None


def test_touched_zone_reports_waiting_for_touch_with_no_trigger_source():
  # "waiting_for_reaction" (a touch confirmed but M1 rejection still
  # pending) no longer exists - M1 mapped-zone reaction is retired as a
  # setup source (P2), so any executable-but-untriggered zone reports the
  # same "waiting_for_touch" terminal state regardless of whether price has
  # touched it yet.
  selected, state, reasons = map_strategy._select_reaction(
    _map(_supply()),
    _m1_bar(open_=4151.5, high=4153.2, low=4151.4, close=4153.1),
    4153.1,
    1.8,
    0.5,
    _cfg(auto_trade_map_zone_min_width_abs=0.3),
  )

  assert selected is None
  assert state == "waiting_for_touch"
  assert "no entry trigger source configured" in reasons[0]


def test_bias_selects_only_the_matching_side():
  buy = MapEntry(
    "buy",
    4145.0,
    4146.0,
    4145,
    4146,
    "major",
    ["demand", "OB"],
    12.0,
  )

  selected, state, _ = map_strategy._select_reaction(
    _map(buy, bias="down"),
    _m1_bar(high=4146.0, low=4144.9, close=4145.8),
    4145.8,
    1.0,
    0.5,
    cfg=_cfg(
      auto_trade_allow_counter_bias=False,
      auto_trade_map_counter_bias_enabled=False,
    ),
  )

  assert selected is None
  assert state == "waiting_for_zone"


def test_degenerate_zone_is_filtered_and_warned(caplog):
  entry = MapEntry(
    "sell",
    4102.10,
    4102.13,
    4102,
    4103,
    "zone",
    ["supply", "fresh"],
    8.0,
  )

  with caplog.at_level("WARNING", logger=map_strategy.__name__):
    selected, state, reasons = map_strategy._select_reaction(
      _map(entry),
      _m1_bar(high=4102.2, low=4101.8, close=4101.9),
      4102.0,
      3.0,
      0.5,
      _cfg(),
    )

  assert selected is None
  assert state == "waiting_for_zone"
  assert "degenerate_width=1" in reasons[0]
  assert "lo=4102.10000" in caplog.text
  assert "hi=4102.13000" in caplog.text
  assert "tier=zone" in caplog.text
  assert "tags=['supply', 'fresh']" in caplog.text
  assert "score=8.00" in caplog.text


def test_normal_zone_and_inclusive_width_threshold_are_actionable():
  normal = MapEntry(
    "sell", 4087.0, 4095.0, 4087, 4095,
    "zone", ["supply", "fresh"], 8.0,
  )
  exact = MapEntry(
    "sell", 4100.0, 4101.0, 4100, 4101,
    "zone", ["supply"], 6.0,
  )

  assert map_strategy._actionable(normal, 3.0, _cfg())
  assert map_strategy._actionable(exact, 3.0, _cfg())


def test_unreachable_zone_reports_distance_limit_and_filters():
  far = MapEntry(
    "sell", 4087.0, 4095.0, 4087, 4095,
    "zone", ["supply", "fresh"], 8.0,
  )

  selected, state, reasons = map_strategy._select_reaction(
    _map(far),
    _m1_bar(high=4073.2, low=4071.9, close=4072.88),
    4072.88,
    3.0,
    0.5,
    _cfg(),
  )

  assert selected is None
  assert state == "waiting_for_touch"
  assert "nearest mapped SELL zone 4087.00-4095.00" in reasons[0]
  assert "14.1 away · tracked, execute within 4.5" in reasons[0]
  assert "side=0" in reasons[0]
  assert "actionable=0" in reasons[0]
  assert "degenerate_width=0" in reasons[0]
  assert "distance=0" in reasons[0]


def test_nearest_absent_from_rendered_map_is_flagged():
  live = MapEntry(
    "sell", 4087.0, 4095.0, 4087, 4095,
    "zone", ["supply", "fresh"], 8.0,
  )
  displayed = _map(
    MapEntry(
      "sell", 4108.0, 4116.0, 4108, 4116,
      "major", ["supply", "FVG"], 12.0,
    ),
  )

  _, _, reasons = map_strategy._select_reaction(
    _map(live),
    _m1_bar(high=4081.0, low=4079.0, close=4080.0),
    4080.0,
    3.0,
    0.5,
    _cfg(),
    displayed,
  )

  assert "absent from rendered Market Map" in reasons[0]


def _worked_counter_bias_map(
  *,
  tags: list[str] | None = None,
  score: float = 6.5,
  tier: str = "zone",
  include_level: bool = True,
) -> MarketMap:
  entries = [
    MapEntry(
      "buy",
      4066.0,
      4073.0,
      4066,
      4073,
      tier,
      tags or ["breaker", "demand", "FVG", "fresh"],
      score,
      contains_price=True,
    ),
  ]
  if include_level:
    entries.append(MapEntry(
      "buy",
      4065.7,
      4066.0,
      4065,
      4066,
      "level",
      ["TL support ×3", "support ×9"],
      9.0,
    ))
  entries.extend([
    MapEntry(
      "sell",
      4087.0,
      4095.0,
      4087,
      4095,
      "zone",
      ["OB", "supply", "fresh", "resistance ×9"],
      9.0,
    ),
    MapEntry(
      "sell",
      4102.10,
      4102.13,
      4102,
      4103,
      "zone",
      ["supply", "fresh"],
      8.0,
    ),
  ])
  return _map(
    *entries,
    bias="down",
    price=4072.88,
    eq=4084.0,
    box_low=4073.0,
    box_high=4095.0,
  )


def _counter_rejection_bar() -> pd.DataFrame:
  return _m1_bar(
    open_=4069.5,
    high=4073.0,
    low=4069.0,
    close=4072.88,
  )


def test_counter_bias_flag_off_keeps_opposite_zone_ignored():
  selected, state, _ = map_strategy._select_reaction(
    _worked_counter_bias_map(),
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=False),
  )

  assert selected is None
  assert state == "waiting_for_touch"


def test_worked_counter_bias_zone_reaches_waiting_for_touch_no_match(monkeypatch):
  # Counter-bias zone SELECTION still runs (Market Map's structural pool is
  # kept) but no longer promotes the winning zone to a StrategyMatch - the
  # M1 reaction detector (and the EQ-capped target/tag construction that
  # depended on it) is retired as a setup source (P2).
  monkeypatch.setattr(
    map_strategy,
    "atr_indicator",
    lambda *args: pd.Series([3.0]),
  )
  market_map = _worked_counter_bias_map()

  decision = map_strategy.evaluate_market_map_strategy(
    {"M1": _counter_rejection_bar()},
    symbol="XAU",
    event_ts="1784806680",
    spot_price=4072.88,
    cfg=_cfg(auto_trade_map_counter_bias_enabled=True),
    market_map=market_map,
    now=1784806680,
  )

  assert decision.state == "waiting_for_touch"
  assert decision.match is None
  assert "nearest mapped BUY zone 4066.00-4073.00" in decision.reasons[0]


@pytest.mark.parametrize(
  ("tags", "score"),
  [
    (["breaker", "demand", "FVG"], 6.5),
    (["breaker", "demand", "FVG", "fresh"], 4.0),
    (["demand", "fresh"], 6.5),
  ],
)
def test_counter_bias_uses_same_structural_zone_rules_as_aligned_entries(
  tags,
  score,
):
  # Reaching "waiting_for_touch" (rather than "waiting_for_zone"/
  # "no_zone_in_range") proves the counter-bias zone passed every structural
  # filter and distance check - the M1 trigger that used to promote it
  # further to "candidate" is retired as a setup source (P2).
  selection = map_strategy._select_reaction_detailed(
    _worked_counter_bias_map(
      tags=tags,
      score=score,
      include_level=False,
    ),
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=True),
  )

  assert selection.selected is None
  assert selection.state == "waiting_for_touch"
  assert selection.actionable_entries[0].side == "buy"


def test_nearby_trendline_level_satisfies_counter_bias_confluence():
  market_map = _worked_counter_bias_map(
    tags=["demand", "fresh"],
    include_level=True,
  )

  selection = map_strategy._select_reaction_detailed(
    market_map,
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=True),
  )

  assert selection.state == "waiting_for_touch"
  assert selection.selected is None
  assert selection.actionable_entries[0].tier == "zone"
  assert selection.actionable_entries[0].side == "buy"


def test_counter_bias_tier_is_not_a_quality_criterion():
  market_map = _worked_counter_bias_map(tier="level", include_level=False)

  selection = map_strategy._select_reaction_detailed(
    market_map,
    _counter_rejection_bar(),
    4072.88,
    3.0,
    0.5,
    _cfg(auto_trade_map_counter_bias_enabled=True),
  )

  assert selection.state == "waiting_for_touch"
  assert selection.selected is None
  assert selection.actionable_entries[0].tier == "level"


def test_replay_1938_filters_dead_band_then_selects_counter_bias(monkeypatch):
  monkeypatch.setattr(
    map_strategy,
    "atr_indicator",
    lambda *args: pd.Series([3.0]),
  )
  market_map = _worked_counter_bias_map()
  aligned_only = map_strategy.evaluate_market_map_strategy(
    {"M1": _counter_rejection_bar()},
    symbol="XAU",
    event_ts="1784806680",
    spot_price=4072.88,
    cfg=_cfg(auto_trade_map_counter_bias_enabled=False),
    market_map=market_map,
    now=1784806680,
  )
  counter_enabled = map_strategy.evaluate_market_map_strategy(
    {"M1": _counter_rejection_bar()},
    symbol="XAU",
    event_ts="1784806680",
    spot_price=4072.88,
    cfg=_cfg(auto_trade_map_counter_bias_enabled=True),
    market_map=market_map,
    now=1784806680,
  )

  assert aligned_only.state == "waiting_for_touch"
  assert "nearest mapped SELL zone 4087.00-4095.00" in aligned_only.reasons[0]
  assert "degenerate_width=1" in aligned_only.reasons[0]
  # With counter-bias enabled, the dead/degenerate band is filtered out and
  # the BUY zone at 4066-4073 is selected instead - proving the filtering +
  # counter-bias selection still both work, even though neither path can
  # reach "candidate" anymore (M1 reaction retired as a setup source, P2).
  assert counter_enabled.state == "waiting_for_touch"
  assert counter_enabled.match is None
  assert "nearest mapped BUY zone 4066.00-4073.00" in counter_enabled.reasons[0]


def test_zone_beyond_default_track_limit_is_no_zone_in_range():
  # 22.2 away with atr=2.4 → track 8.0×ATR = 19.2, so still out of range.
  supply = MapEntry(
    "sell", 4075.0, 4087.51, 4075, 4088,
    "major", ["flip", "supply", "FVG", "breakout-retest"], 12.0,
  )
  track_limit = 8.0 * 2.4
  distance = 4075.0 - 4052.0
  assert distance == pytest.approx(23.0)
  # Use the evidence distance 22.2 against the lo edge via price 4052.8.
  price = 4075.0 - 22.2
  assert 22.2 > track_limit

  selected, state, reasons = map_strategy._select_reaction(
    _map(supply, bias="down", price=price, eq=4051.0, box_low=4040.0, box_high=4062.0),
    _m1_bar(open_=price, high=price + 0.5, low=price - 0.5, close=price),
    price,
    2.4,
    0.5,
    _cfg(),
  )

  assert selected is None
  assert state == "no_zone_in_range"
  assert "within track distance" in reasons[0]
  assert "track limit 8.0×ATR = 19.2" in reasons[0]
  assert "distance=1" in reasons[0]


def test_zone_inside_track_outside_execute_is_waiting_for_touch():
  supply = MapEntry(
    "sell", 4075.0, 4087.51, 4075, 4088,
    "major", ["supply", "fresh"], 10.0,
  )
  price = 4070.0  # 5.0 away from lo
  atr = 2.4
  assert 5.0 <= 8.0 * atr
  assert 5.0 > 1.5 * atr

  selected, state, reasons = map_strategy._select_reaction(
    _map(supply, bias="down", price=price),
    _m1_bar(open_=price, high=price + 0.4, low=price - 0.4, close=price),
    price,
    atr,
    0.5,
    _cfg(),
  )

  assert selected is None
  assert state == "waiting_for_touch"
  assert "5.0 away · tracked, execute within 3.6" in reasons[0]


def test_zone_inside_execute_distance_reaches_waiting_for_touch():
  supply = MapEntry(
    "sell", 4075.0, 4087.51, 4075, 4088,
    "major", ["supply", "fresh"], 10.0,
  )
  price = 4072.0  # 3.0 away
  atr = 2.4
  assert 3.0 <= 1.5 * atr

  selected, state, reasons = map_strategy._select_reaction(
    _map(supply, bias="down", price=price),
    _m1_bar(
      open_=4074.0,
      high=4076.5,
      low=4071.5,
      close=4072.0,
    ),
    price,
    atr,
    0.5,
    _cfg(),
  )

  # Inside execute distance, but M1 reaction is retired as a setup source
  # (P2) - so even a within-range zone stops at "waiting_for_touch", not
  # "candidate".
  assert state == "waiting_for_touch"
  assert selected is None
  assert "nearest mapped SELL zone 4075.00-4087.51" in reasons[0]


def test_tracked_zone_has_no_trigger_source():
  supply = MapEntry(
    "sell", 4075.0, 4087.51, 4075, 4088,
    "major", ["supply", "fresh"], 10.0,
  )
  price = 4072.0

  selected, state, reasons = map_strategy._select_reaction(
    _map(supply, bias="down", price=price),
    _m1_bar(open_=price, high=price + 0.2, low=price - 0.2, close=price),
    price,
    2.4,
    0.5,
    _cfg(),
  )

  assert selected is None
  assert state == "waiting_for_touch"
  assert "no entry trigger source configured" in reasons[0]


def test_replay_2213_map_tracks_nearest_sell_without_order(monkeypatch):
  """Fix 2 only: 22:14 map at price 4052, nearest SELL 4075-4087.51."""
  supply = MapEntry(
    "sell", 4075.0, 4087.51, 4075, 4088,
    "major", ["flip", "supply", "FVG", "breakout-retest"], 12.0,
  )
  price = 4052.0
  atr = 2.4
  # Default track 8.0×ATR=19.2 leaves 23.0 out of range; raise track so the
  # motivating session qualifies while keeping execute at 1.5×ATR.
  monkeypatch.setattr(
    map_strategy,
    "atr_indicator",
    lambda *args: pd.Series([atr]),
  )
  decision = map_strategy.evaluate_market_map_strategy(
    {"M1": _m1_bar(open_=price, high=price + 0.4, low=price - 0.4, close=price)},
    symbol="XAUUSD",
    event_ts="1784823180",
    spot_price=price,
    cfg=_cfg(auto_trade_map_track_distance_atr=10.0),
    market_map=_map(
      supply,
      bias="down",
      price=price,
      eq=4051.0,
      box_low=4040.0,
      box_high=4062.0,
    ),
    now=1784823180,
  )

  assert decision.state == "waiting_for_touch"
  assert decision.match is None
  assert "4075.00-4087.51" in decision.reasons[0]
  assert "tracked, execute within 3.6" in decision.reasons[0]
  assert decision.track_limit == pytest.approx(24.0)
  assert decision.execute_limit == pytest.approx(3.6)
