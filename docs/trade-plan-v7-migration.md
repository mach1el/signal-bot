# TradePlan V7 migration and operations

See `docs/adr-trade-plan-v7-boundary.md` for the architectural rationale.
This doc covers rollout mechanics, rollback, and current status.

## Status (as of `refactor/trade-plan-v7-complete-runtime-cutover`)

Done and merged-to-branch:

- TradePlan V7 contract (Python + C# models, shared fixture, parity tests).
- `AUTO_TRADE_CONTRACT_MODE` migration gate, wired into the config-health
  handshake as a fatal field.
- Python setup lifecycle (`analysis:setup:{setup_id}`) is wired into the live
  scanner path: every successfully-built `StrategyMatch` with a structural
  identity advances through `DISCOVERED -> ... -> CONFIRMED`, and a
  `v7_thesis_id` (symbol/strategy_family/direction/structural_id only -
  deliberately excludes confirmation timestamp) is attached to the match so
  repeated confirmations of the same structure share one thesis.
- Python `TradePlan` builder (`trade_plan_builder.py`) is a real translator,
  not a placeholder: entry type, entry price(s), and stop price come from
  `evaluate_execution_policy()` (the same route/stop planner the V6 path
  already calls); bias/regime/structural kind/timeframe come from real
  `StrategyMatch` fields captured at detection time; a missing
  `structural_zone_id` fails closed (`missing_stable_thesis_id`) instead of
  falling back to a timestamp-dependent id.
- `worker.py::_publish_trade_plan_v7` is wired into the live publish path,
  gated on `AUTO_TRADE_CONTRACT_MODE in {shadow_v7, v7_primary, v7_only}`.
  Claims the active thesis (`claim_active_thesis`) before building - a
  second setup for an already-claimed thesis is rejected, never a second
  plan. In `v7_only`, the three private V6 autonomous publish call sites
  (scanner-routed `_publish_strategy_match`, the private M1 range gate, the
  trend detector) are all skipped in favor of V7, each recording
  `legacy_candidate_disabled_in_v7_only`.
- C# `TradePlanRuntime` (new file, separate from `AutoTradeEngine.cs`) is
  wired into `AutoTradeEngine.RunSessionAsync`'s own loop (one more poll
  alongside the existing manual-command-stream read, sharing the engine's
  readiness/gate machinery rather than owning a second session loop): reads
  `execution:trade_plans` on its own cursor, claims each `plan_id` exactly
  once, arms it, evaluates entry against the live quote via
  `TradePlanExecutionEngine.EvaluateEntry`, submits the exact declared
  market/limit/ladder order via `ICTraderTradeClient`, sizes volume from the
  plan's own risk contract (never a recomputed structural stop), tracks
  fills against each declared target, applies break-even from the
  broker-confirmed fill price, and persists/restores state across a
  restart. `shadow_v7` arms and evaluates for real but never calls a
  broker-mutating method.
- `AutoTradeEngine.ProcessCandidateAsync` rejects any new non-manual-algo V6
  candidate outright when `ContractMode == "v7_only"`
  (`legacy_candidate_disabled_in_v7_only`) - defense-in-depth alongside
  Python no longer publishing them.
- `TradePlanExecutionEngineDependencyTests` now scans `TradePlanRuntime.cs`
  too (previously only `TradePlanExecutionEngine.cs`/`TradePlanV7.cs`) and
  passes - the file that actually places orders still never references
  `ResolveExecutionRoute`, `StructureStopPlanner`, or the other
  dual-planning symbols named in the ADR.
- V7 events (`plan_armed`, `v7_order_submitted`, `order_filled`,
  `tp_booked`, `sl_moved`, reusing `position_closed`) are registered in
  Python's Telegram `_NOTIFY_TYPES`/label vocabulary with the wording
  Section M specifies - found and fixed a real gap where they would have
  been silently dropped (not shown, not an error) before reaching Telegram.
- A live V6 bugfix: Market Map zone selection now uses the uncapped
  structural pool instead of the Telegram-display-capped list.
- A live V6 Telegram-label fix: "Algo bot READY" no longer appears for a
  merely-published candidate.

Not yet done (tracked as follow-up):

- The Market Map `display_entries`/`strategy_zones` naming split described
  in the ADR is implemented functionally (`entries`/`actionable_entries`
  already existed and are now used correctly) but not renamed to the ADR's
  suggested field names - renaming is a larger, more disruptive change
  deferred to avoid mixing a rename with a behavior fix in one commit.
- `worker.py`'s private V6 detectors (the M1 range gate in `gate.py`, the
  Mapped Zone Reaction detector in `map_strategy.py`, the trend detector in
  `trend.py`) still run and still independently analyze on every event; only
  their *publication* is blocked in `v7_only` mode. They are not
  architecturally isolated from the V7 pipeline the way Section A of the
  originating task describes - they remain wired because V6 must keep
  working under every mode below `v7_only` (including `v7_primary`'s
  fallback), and a deeper isolation (e.g. moving them behind an explicit
  compatibility boundary) risks destabilizing the V6 path this migration is
  explicitly not allowed to break.
- No single end-to-end test exercises the full Python -> Redis -> C# chain
  in one run. Both sides have real, non-mocked coverage of their own half
  (`test_publish_trade_plan_v7.py` proves Python publishes a valid,
  parseable plan; `TradePlanRuntimeTests.cs` proves C# consumes a plan JSON
  and places a real order against a fake broker) but a genuine
  cross-process/cross-language integration harness was not built.
- `shadow_v7` mode has no concrete divergence-recording implementation
  (compare what V6 would have done vs. what V7 decided) - it arms and
  evaluates for real without submitting orders, but does not yet log a
  structured comparison against the parallel V6 candidate for the same
  event.
- `v7_primary` demo canary allow-listing (a small strategy set trading live
  on demo via V7 while everything else stays on V6) is not implemented;
  `v7_primary` today means "V7 for every match that reaches CONFIRMED,
  V6 still available as a parallel path", not an allow-listed subset.

## Migration modes

Set identically on both services via `AUTO_TRADE_CONTRACT_MODE`:

| Mode | Python behavior | C# behavior |
|---|---|---|
| `legacy_v6` (default) | Publishes only V6 `TradeCandidate`s | Consumes only V6, unchanged |
| `shadow_v7` | Also publishes V7 plans | Parses/validates/arms V7, places no orders from it |
| `v7_primary` | V7 and V6 both publish | V7 places orders; V6 remains a parallel path |
| `v7_only` | V6 publishing stops | V6 candidates rejected outright (manual /algo exempt) |

Do not set this independently on the two services - `AutoTradeConfigHealth`
treats a `contract_mode` mismatch as fatal and disables auto-trade
entirely, by design (see the ADR's "fail closed on mismatch" requirement).

## Rollback

Because every mode above `legacy_v6` requires an explicit, coordinated env
change on both services, rollback is: set `AUTO_TRADE_CONTRACT_MODE=legacy_v6`
on both services and redeploy. No data migration is required - V6 candidates,
open V6 positions, and V6 Redis keys are untouched by anything in this
branch. `shadow_v7` and later modes are additive; they do not remove or
rewrite `auto_trade:*` state. A restart while V7 plans are armed/open
recovers from `execution:plan_runtime:{plan_id}` and the runtime's own copy
of the plan JSON, without re-arming or duplicating an order.

## Legacy compatibility

- V6 `TradeCandidate` and its `Planned*` field family are unchanged.
- `execution:*` keys are a new, separate namespace - nothing in V6 reads or
  writes them, so V7 work cannot corrupt V6 state by construction.
- Existing open V6 positions remain manageable under every mode above;
  nothing in this branch touches `AutoTradePositionState` or existing
  position-management code paths.

## Next work

In priority order:

1. Build a genuine cross-language end-to-end integration test (real or
   faithfully-faked Redis shared between a Python publisher process and a
   C# consumer process, plus a fake broker) - the strongest remaining proof
   gap, even though both halves are independently well-tested.
2. Implement `shadow_v7` divergence recording (compare V6 candidate vs. V7
   plan outcomes for the same detection, without V7 placing orders) so
   shadow mode is actually observable before flipping to `v7_primary`.
3. `v7_primary` demo canary allow-listing on a small strategy set (Trend
   Pullback, Break & Retest) per the ADR's phase 5, with V6 as the
   explicit fallback for everything else.
4. Market Map `entries`/`actionable_entries` -> `display_entries`/
   `strategy_zones` rename, as its own reviewable change.
5. Only after 1-3 are stable and observed on demo: `feat/analysis-h1-
   structure-map` - deliberately sequenced after the ownership boundary,
   not before it.

No live trading, VPS deployment, or automatic merge is authorized by this
branch or this document.
