# P0 — baseline map: simplify to one canonical Zone Analysis → M1 Execution pipeline

Branch: `refactor/p0-simple-zone-m1-execution`
Base: `master` @ `dbe7aea9eeb9dce3d01a2c54c60dd699871b9184` (merge of PR #146)

Written before any production code in this branch changed, per the task's requirement to
prove the current behavior from code first. Every claim below is cited to exact
`file:line` in the baseline tree.

## 1. `scanner.py` substitutes analysis-only results into the notification path — TRUE

`app/analysis/scanner.py:2984-2989`:
```python
analysis_only_results = [
  displayed_by_key.get(_telemetry_result_key(result), result)
  for result, decision in actionability.gated
  if decision.hard_block
]
notification_results = digest or analysis_only_results
```
`digest` comes from `_digest_results(reward_risk_eligible_results)` (line 2966).
`notification_results` is passed straight into `_notify_digest_once(...)` (line 2990-2995),
which is the function that actually calls Telegram `notify`/`post_or_edit_forming_card`. When
`digest` is empty, gated/hard-blocked, non-executable results are exactly what gets sent.

## 2. `_notify_digest_once()` sends a standalone message with no resolvable StrategyMatch — TRUE

`app/analysis/scanner.py:2027-2160`. When `match_for_card is None` (no `StrategyMatch`
resolves for that result):
```python
else:
  await notify(text, chat_id=settings.telegram_owner_id)
```
(`scanner.py:2158`) — a standalone, un-tracked Telegram send with no card lifecycle at all.

**MARKET OBSERVATION / ANALYSIS ONLY render sites** (`_format_detection`,
`scanner.py:1219`, used only on the non-executable branch):
- `scanner.py:1255` — `"... MARKET OBSERVATION"` header
- `scanner.py:1260` — `"ANALYSIS ONLY · no executable StrategyMatch"`
- `scanner.py:1262` — `"ANALYSIS ONLY · autonomous execution disabled"`

Both send sites (`scanner.py:2147-2155` via `post_or_edit_forming_card`, and `2158` via
bare `notify`) can render this text. All of this — the fallback (§1), both send sites, and
the label logic — is removed in this refactor.

## 3. `key_level_reaction()` and ambiguous roles — PARTIALLY TRUE (the real problem is *how* it resolves, not that it emits two results)

`app/analysis/detectors.py:1990-2085`. For `ROLE_AMBIGUOUS` the loop evaluates **both**
directions (`("BUY", "SELL")`, `detectors.py:2018-2026`) but the function returns
`DetectionResult | None` and keeps only the higher-confluence candidate
(`best.confluence`, line ~2085) — it never emits two results from one call.

The actual defect the spec is targeting is this **arbitrary confluence-margin tiebreak**
deciding direction for an ambiguous level, instead of the deterministic rule
(price-below-level → BUY, price-above-level → SELL, price-inside → M5-reaction-confirmed-
only) the spec requires. `app/analysis/key_level_role.py` — `ROLE_SUPPORT`,
`ROLE_RESISTANCE`, `ROLE_AMBIGUOUS`, `ROLE_BROKEN_SUPPORT`, `ROLE_BROKEN_RESISTANCE`
(lines 9-13); `classify_key_level_role` (lines 33-78) returns `ROLE_AMBIGUOUS` when neither
an explicit kind nor an accepted-close breakout applies.

Downstream, `app/analysis/actionability.py:339-352` hard-blocks any surviving
`ROLE_AMBIGUOUS` result unconditionally (`hard_block` defaults `True` and is never
overridden at that call site) — so today's system already never *executes* an ambiguous
key level. The gap is that it still reaches `key_level_role_ambiguous` as a
Telegram-visible ANALYSIS ONLY card via the §1/§2 fallback, and the underlying direction
choice (when NOT ambiguous, i.e. one candidate clearly wins) is confluence-margin-based
rather than the new deterministic price-relative-to-level rule.

## 4. Live detector registry — TRUE, full inventory

`DEFAULT_DETECTORS` (`app/analysis/detectors.py:2318-2331`):

| Entry | Function | On removal list | Verdict |
|---|---|---|---|
| Key Level Reaction | `key_level_reaction` (1990) | No — survivor | keep, folded into Zone Reaction |
| Demand Zone Reaction | `demand_zone_reaction`→`_sd_zone_reaction` (2088→2100) | No — survivor | keep, folded into Zone Reaction |
| Supply Zone Reaction | `supply_zone_reaction`→`_sd_zone_reaction` (2094→2100) | No — survivor | keep, folded into Zone Reaction |
| Range-Edge Scalp | `range_edge_scalp` (1358) | No — survivor | keep, becomes Range Edge Scalp family |
| Session Level Reaction | `session_level_reaction` (2176) | **Yes** | **remove from registry** |
| Trendline Reaction | `trendline_reaction` (2244) | **Yes** | **remove from registry** |
| Box Breakout | `box_breakout` (1114) | **Yes** | **remove from registry** |
| Trend Pullback | `trend_pullback` (941) | **Yes** | **remove from registry** |
| Break & Retest | `break_retest` (1000) | **Yes** | **remove from registry** |
| Snap-Back | `snap_back` (1250) | **Yes** | **remove from registry** |
| Momentum Ride | `momentum_ride` (1307) | **Yes** | **remove from registry** |
| Fade Scalp | `fade_scalp` (1514) | **Yes** | **remove from registry** |

Not present at all today (nothing to remove): One-Sided Range Reaction (no matching
function exists), any M1-sourced detector (none exists). `zone_reaction()`
(`detectors.py:1561`, the legacy wrapper) is already **not** in `DEFAULT_DETECTORS` — dead
code, but the string family constant `FAMILY_MAPPED_ZONE_REACTION` is still referenced by
live routing/policy plumbing (`execution_policy.py:220`, `execution_confirmation.py:57`,
`multi_match.py:119-120`, `execution_route.py:193`, `worker.py:2274,2474,2536,7734`) — this
routing reference needs updating/removing alongside the registry change, not left as an
orphaned family label.

There is no standalone Order Block / FVG / Liquidity Sweep *detector function* — `fvg()`/
`order_blocks()` build `StructureSet.fvg_zones`/`order_blocks` (`detectors.py:412-413`),
consumed as merge inputs to `demand_zone_reaction`/`supply_zone_reaction`
(`detectors.py:2114`). This matches the target model treating them as evidence tags, not
detectors — Liquidity Sweep is the one family that needs a genuinely new top-level
detector (`liquidity_sweep_reversal`), since no equivalent exists as a first-class
detector today.

## 5. Gate/filter layer chain — TRUE, ordering confirmed from call sites

1. `_structure_card_gate` (`scanner.py:1862`) — per-result: drops unsupported "round"
   levels, drops source-exhausted touches, suppresses counter-bias inside a fading range.
   Runs inside the per-detector loop (`scanner.py:2776`).
2. `resolve_actionability` (`actionability.py:162`, called `scanner.py:2807`) — cross-side
   semantic/geometry gate, includes calling `evaluate_structural_target_room`
   (`actionability.py:300`).
3. Structural target-room (`structural_target_room.py::evaluate_structural_target_room`) —
   called again standalone from `worker.py:5161` (pre-plan-build), duplicating step 2's
   check on the worker side.
4. `_reward_risk_pre_gate` (`scanner.py:1660`, called `scanner.py:2864`) — R:R gate over
   `actionable_results` only.
5. `evaluate_execution_policy` (`execution_policy.py:408`) — called **repeatedly**:
   scanner-side inside step 4's call path (`scanner.py:1695,1817`), worker preflight
   (`worker.py:2838,3376,5676,6763`), and again inside plan-builder
   (`trade_plan_builder.py:219`) — the same function re-run three times per candidate.
6. Worker structural preflight — `_preflight_strategy_intent` (`worker.py:7196`),
   `_preflight_private_intent` (`worker.py:7501`), both routing into `_common_preflight`
   (`worker.py:6708`) and `_preflight_decision` (`worker.py:6507`); called from the main
   loop (`worker.py:7879/7951`).
7. `arbitrate_preflight_decisions` (`arbitration.py:172`, called `worker.py:7968`) — picks
   one winning intent among competing executable preflights.
8. Plan-builder validation (`build_trade_plan_from_strategy_match`,
   `trade_plan_builder.py:146`, called via `_publish_trade_plan_v7`,
   `worker.py:4381`→`8073/8139`) — re-runs `evaluate_execution_policy` a third time plus
   barrier/cooldown/overlap checks (`worker.py:5350-5375`).

Confirmed duplication: target-room evaluated twice (steps 2 and 3);
`evaluate_execution_policy` evaluated three times (steps 5, 6, 8) over what is, for the
arbitration winner, the same static analysis. This is exactly what the target pipeline
replaces with one synthesis pass + one mechanical plan-sanity validator.

## 6. Market Map has two separate pools — TRUE

`app/analysis/market_map.py:68-80` — `MarketMap.entries: list[MapEntry]` (display, capped
to `max_per_side`, padded with round/fallback candidates when under `min_per_side`,
`market_map.py:232-263,290-291`) vs `MarketMap.actionable_entries: list[MapEntry]`
(execution, uncapped, filtered to `tier in {"zone","major"}` + actionable tag via
`_is_structural_actionable`, `market_map.py:228-231,282,302,864-866`).

Telegram rendering (`render_market_map`, `market_map.py:306-343`) reads `entries` (via the
`.sells`/`.buys` properties, lines 82-88) — the display pool. Execution/target-room code
reads `actionable_entries` exclusively: `actionability.py:174`,
`structural_target_room.py:94/130`, `map_strategy.py:199,274,535,592`,
`worker.py:4363,5167-5170,6305-6315`. `tests/test_auto_map_strategy.py:34-43` documents the
split explicitly: *"the two are not always equal."* This is the exact "hidden
execution-only zone pool" the spec requires eliminating — every zone capable of producing
a plan must be visible in the one Market Map used for both.

## 7. Structural identity vs. TradePlan thesis identity diverge on timestamps — TRUE

`structural_thesis_id` (`app/analysis/structural_reaction_support.py:130-150`) hashes
`touch_bar_ts`/`confirmation_bar_ts` alongside `structural_id`. It feeds `match_id`
(`scanner.py:420-429`), and `setup_id = match.match_id` (`scanner.py:598`) — so **setup
identity is re-hashed on every new confirmation of the same structure.**

`thesis_id` (`structural_reaction_support.py:153-180`) deliberately excludes both
timestamps, with an existing in-code comment explaining exactly why:
> *"Deliberately narrower than structural_thesis_id() above: this excludes
> touch_bar_ts/confirmation_bar_ts on purpose. Those timestamps make
> structural_thesis_id() (and match_id, which reuses it) change on every new confirmation
> of the same structural reaction - correct for V6's per-event dedup, but exactly what a
> TradePlan thesis must NOT do."*

Reinforced at `trade_plan_builder.py:189-201`: *"match_id ... is re-hashed on every new
confirmation timestamp, so using it as a TradePlan thesis_id would silently let repeated
confirmations of the same structure each look like a brand new thesis."*

`confluence_setup_id` (`confluence_zone.py:110-114`) is also timestamp-free (hashes only
`zone_id` + `direction`) — it is the one existing identity scheme already close to what
the new `ZoneOpportunity`/episode identity needs; `structural_thesis_id`/`match_id` is the
one that needs replacing as the setup-id basis for non-confluence-zone detections.

## 8. C# `market_watch` evaluates only quote-in-zone + spread, no M1 trigger contract — TRUE (stronger than stated)

`TradePlanExecutionEngine.EvaluateMarketWatch` (`ctrader-engine/src/
TradePlanExecutionEngine.cs:52-103`): gates are (a) plan not expired, (b) quote (bid/ask
per `PriceSide`) inside `[ZoneLow, ZoneHigh]`, (c) spread ≤ `MaxSpreadTicks`. No bar/candle
object is ever read. No C# code anywhere computes a candle close/rejection/sweep-reclaim
from raw bar data — the one M1-adjacent reference (`ScaleInTriggerPlanner.cs:248`, V6
scale-in path) only consumes a pre-computed `RejectionConfirmed` bool that Python already
decided (`AutoTradeEngine.cs:4767 ← candidate.RejectionConfirmed`).

**Ready-made hook already exists**: `TradePlanEntry.Activation` (`TradePlan.cs:78`, a
`string?`) is required by `ValidateEntryShape` for `market_watch`
(`TradePlan.cs:316-318`, *"market_watch entry requires activation"*) but is **never read
anywhere** — an example plan even shows `"activation":"quote_inside_zone"`
(`TradePlanRuntime.cs:123`). This is exactly where `m1_touch`/`m1_rejection_close`/
`m1_sweep_reclaim` dispatch plugs in additively.

**M1 data is already available, no new feed work needed**: M1 trendbars are already
subscribed (`FeedOptions.cs:41`, default `CTRADER_TIMEFRAMES=M1,M5,M15,H1`); closed bars
are already durably written per symbol/timeframe (`RedisBarSink.cs`, `WriteClosedBarAsync`/
`GetLatestTimestampAsync`/`ReadLatestAsync`). `TradePlanRuntimeState`
(`TradePlanRuntime.cs:29-44`) has **no** bar-timestamp field today — `LastEvaluatedM1BarTs`/
`ActivationEpisodeId`/`TriggerBarTs`/`TriggerType` all need adding.

## Implementation plan derived from this map

1. **Domain model**: `ZoneOpportunity` (new module), stable episode identity function
   (built from canonical source IDs + midpoint/width buckets, replacing
   `structural_thesis_id` as the setup-id basis; `confluence_setup_id`'s approach is the
   template).
2. **Same-side synthesis**: merge key-level/S+D/OB/FVG/liquidity-sweep evidence into one
   `ZoneOpportunity` per side — extends the existing `merge_confluence_zones` machinery
   rather than inventing a new merge algorithm from scratch.
3. **Cross-side synthesis**: replace the arbitrary confluence-margin tiebreak (§3) and the
   `opposing_*` gate family (§5 step 2) with one contested-corridor rule.
4. **Three live families**: `zone_reaction` (folds Key Level + S/D + OB + FVG),
   `liquidity_sweep_reversal` (new detector), `range_edge_scalp` (keep, tightened to
   edges-only per the range-mode rule).
5. **Registry**: strip `DEFAULT_DETECTORS` to the three families (§4); leave the removed
   detector functions defined (replay-safe) but unregistered; update
   `FAMILY_MAPPED_ZONE_REACTION` routing references.
6. **Telegram**: delete the §1 fallback and §2 bare-notify path; delete the MARKET
   OBSERVATION/ANALYSIS ONLY render branch from the live send path (keep a debug-only
   formatter for `/scan_report`).
7. **Market Map**: collapse `entries`/`actionable_entries` (§6) into one pool.
8. **Gate simplification**: delete steps 1-3 and the repeated `evaluate_execution_policy`
   calls (§5) in favor of synthesis (steps 2-3 above) + one plan-sanity validator; keep
   mechanical execution-safety checks (dedup, risk cap, spread/slippage, restart recovery)
   unchanged.
9. **M1 activation contract**: extend `TradePlanEntry`/`TradePlan` (Python + C#
   `TradePlan.cs`) additively with `activation.type`/`activation.direction`/
   `activation.max_trigger_age_bars`; wire `Activation` dispatch into
   `TradePlanExecutionEngine`; add `LastEvaluatedM1BarTs` etc. to
   `TradePlanRuntimeState`.
10. **Config**: remove/deprecate the dead `key_level_role_ambiguity_gate_enabled` flag and
    other detector-specific toggles for the removed families; keep merge/range/M1/risk
    config.
