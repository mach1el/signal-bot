# Phase 1 — XAU Scalp Pipeline Audit

Research-only inventory. No trading behaviour changes in this document.
Sequence locked: `research → model → replay → shadow → paper → live`.
Do **not** start by adding indicators or loosening thresholds.

## 1. Lane diagram

Two lanes join at `StrategyMatch → TradePlan V8 → cTrader`:

```text
OHLC (Redis)
  → bar_event_dispatcher.dispatch_closed_bar
       ├─ Technique ZoneWatch lane
       │    engine.analyze → detectors → actionability
       │    → zone_execution_cutover / zone_watch
       │    → m1_trigger + entry_activation + entry_location
       │    → StrategyMatch
       │
       └─ HFS lane (primary XAU scalp)
            scalping/context → microstructure → strategies
            → activation (+ entry_location enforce)
            → ranking → publish → StrategyMatch

  StrategyMatch
    → worker.try_publish_executable_signal
    → structural_target_room / protective_stop / execution_route
    → trade_plan_builder
    → TradePlanRuntime (cTrader)
```

| Stage | File | Key symbols |
|-------|------|-------------|
| Dispatch | `algo-bot/app/analysis/bar_event_dispatcher.py` | `dispatch_closed_bar` |
| Structure | `algo-bot/app/analysis/engine.py` | `analyze`, `_analyze_tf` |
| Detectors | `algo-bot/app/analysis/detectors.py` | `LIVE_DETECTOR_REGISTRY` |
| Actionability | `algo-bot/app/analysis/actionability.py` | `resolve_actionability` |
| ZoneWatch | `algo-bot/app/autotrade/zone_watch.py`, `zone_execution_cutover.py` | `discover_zone_watch`, `evaluate_active_zone_watches` |
| M1 trigger | `algo-bot/app/analysis/m1_trigger.py` | `evaluate_m1_trigger_window` |
| Entry activation | `algo-bot/app/autotrade/entry_activation.py` | `evaluate_entry_activation` |
| Entry location | `algo-bot/app/analysis/entry_location.py` | `evaluate_entry_location` |
| HFS runtime | `algo-bot/app/scalping/runtime.py` | `process_m1_bar`, `handle_closed_bar` |
| HFS context | `algo-bot/app/scalping/context.py` | `build_scalp_context_snapshot` |
| HFS micro | `algo-bot/app/scalping/microstructure.py` | `detect_sweep_reclaim`, `detect_impulse_pullback` |
| HFS discover | `algo-bot/app/scalping/strategies.py` | `discover_all`, `discover_range_sweep` |
| HFS activate | `algo-bot/app/scalping/activation.py` | `evaluate_scalp_activation` |
| HFS rank | `algo-bot/app/scalping/ranking.py` | `score_opportunity`, `rank_opportunities` |
| Publish | `algo-bot/app/scalping/publish.py` | `build_hfs_strategy_match`, `publish_hfs_live` |
| Taxonomy | `algo-bot/app/autotrade/strategy_taxonomy.py` | `HFS_STRATEGIES`, `bypasses_opposing_structure_gates` |
| Target room | `algo-bot/app/autotrade/structural_target_room.py` | `evaluate_structural_target_room` |
| Stop | `algo-bot/app/autotrade/protective_stop.py` | `plan_protective_stop` |
| Route / chase | `algo-bot/app/autotrade/execution_route.py` | `resolve_execution_route_plan` |
| Plan build | `algo-bot/app/autotrade/trade_plan_builder.py` | `build_trade_plan_from_strategy_match` |
| Execute | `ctrader-engine/src/TradePlanRuntime.cs`, `TradePlanExecutionEngine.cs` | `EvaluateEntry`, manage TP |

## 2. Live HFS enablement

From `config/trading-bot.yml` → `strategies.scalping`:

| Archetype | Enabled |
|-----------|---------|
| `range_sweep` (HFS Range Sweep) | **true** |
| `breakout_retest` (HFS Breakout Retest) | **true** |
| `impulse_pullback` | **true** (London/NY killzones; excluded in Asia) |

Mode: `live`. Symbols: XAU / ladder-pip only (`context._scalping_symbols`).

## 3. Threshold inventory (ATR vs pip / $ / tick / fraction)

| Name | Source | Unit | Live / default |
|------|--------|------|----------------|
| HFS stop min/max | `high_frequency_scalp.stop` | **pip** | 12 / 30 |
| HFS stop buffer | `stop.buffer_atr` | **ATR** | 0.10 |
| Preferred ladder | `target.preferred_ladder_pips` | **pip** | 10,15,20 |
| Min net target | `target.minimum_net_target_pips` | **pip** | 10 |
| Max chase | `activation.maximum_chase_pips` | **pip** | 40 |
| Trigger age | `activation.trigger_maximum_age_bars` | bars | 2 |
| Rearm distance | `activation.rearm_distance_atr` | **ATR** | 0.25 |
| Max spread | `policy.maximum_spread_pips` | **pip** | 5 |
| Min RR | `policy.minimum_reward_risk` | ratio | 1.10 |
| Range buy max pos | `location.range_buy_maximum_position` | **fraction** | 0.35 |
| Range sell min pos | `location.range_sell_minimum_position` | **fraction** | 0.65 |
| Pullback buy max | `location.pullback_buy_maximum_position` | **fraction** | 0.60 |
| Pullback sell min | `location.pullback_sell_minimum_position` | **fraction** | 0.40 |
| Technique FVG width | `technique.fvg.entry_max_width_price` | **$ price** | 5.0 |
| Reaction stop envelope | `execution.reaction.stop_*_pips` | **pip** | 40–60 |
| Ranking room normalizer | `ranking.py` `/ 30.0` | **pip** | hardcoded |
| Ranking cost normalizer | `ranking.py` `/ 5.0` | **pip** | hardcoded |
| Chase slippage floor | `trade_plan_builder` immediate market | **tick** | ≥100 |
| Engine slippage / chase-through-TP1 | `TradePlanExecutionEngine.EvaluateMarket` | tick / price | enforced |

## 4. Stop / target coherence

1. HFS discovery clamps stop distance into **12–30 pips**, then targets are typically **1R / ~2R** style vs that stop.
2. `protective_stop.plan_protective_stop` may recompute from structure ± ATR buffer, then **clamp again** into the HFS pip envelope.
3. Published TradePlan absolute TP/SL must stay coherent with fill; chase past TP1 is blocked in engine (`chase_through_target`).

Gap: stop width is **pip-capped**, not ATR·k, so quiet Asia and volatile NY share the same absolute envelope.

## 5. Chase path

```text
activation.maximum_chase_pips (40)
  → execution_route chase_away → full market + immediate_market
  → trade_plan_builder max_slippage_ticks ≥ 100
  → TradePlanExecutionEngine: slippage vs order_price + chase_through_target
  → TradePlanRuntime: skip TP not beyond fill / require favorable exit
```

## 6. Duplication map

| Concern | Copies |
|---------|--------|
| Swings | `analysis/swings.find_swings` (ATR zigzag) vs HFS `microstructure` lookback-3 |
| Location | `entry_location.evaluate_entry_location` (ZoneWatch + HFS enforce) |
| Ranking | scanner `_result_rank` / arbitration vs HFS `ranking.score_opportunity` |
| StrategyMatch build | ZoneWatch cutover early build vs HFS `publish.build_hfs_strategy_match` |

## 7. Quality verdict (best-effort)

| Component | Verdict |
|-----------|---------|
| ATR / regime / dealing-range PD | **good** |
| Swing ATR zigzag | **good** |
| Liquidity ATR bands | **good** |
| `entry_location` fractions | **good** geometry, **duplicated** consumers |
| HFS stop/chase/spread envelopes | **weak** vs ATR model (fixed pips) |
| HFS ranking `/30` `/5` | **weak** |
| HFS opposing-room bypass | **permissive** |
| Killzone + disabled archetypes | **suppressing** (intentional) |

## 8. Gap register (priority for ATR·k)

1. Stop envelope 12–30 pips → ATR·k with session/VR floors
2. Chase 40 pips → ATR·k
3. Spread 5 pips → keep absolute floor + ATR-aware widen
4. Ranking normals 30 / 5 → room_ATR and cost features
5. Min active width 25 pips (idle discovery) → ATR·k
6. Technique `entry_max_width_price: 5.0` → ATR·k (ZoneWatch lane)

## 9. Acceptance metrics (for replay / shadow)

Primary (not win rate):

- Expectancy \(= P(win)\cdot AvgWin - P(loss)\cdot AvgLoss\) in R
- Profit factor \(= GrossProfit / GrossLoss\)
- MAE / MFE in pips and R

Slice by: archetype × session × VR bucket × range position × retracement × spread.

Data discipline: **60% development / 20% validation / 20% untouched holdout**. Prefer wide positive-expectancy regions over single magic parameters.

## 10. Mapping to mathematical program

| Model strategy | Existing | Live | Next PR |
|----------------|----------|------|---------|
| Liquidity Sweep Reversal | HFS Range Sweep | on | PR C math gates (shadow-comparable) |
| Impulse Pullback Continuation | HFS Impulse Pullback | off | PR D (remain off) |
| Range Edge Mean Reversion | range_edge + HFS heuristics | mixed | PR E rewrite |

Shared feature layer: `algo-bot/app/scalping/math_features.py` (PR A).
