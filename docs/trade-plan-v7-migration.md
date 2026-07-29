# TradePlan V7 migration and operations

See `docs/adr-trade-plan-v7-boundary.md` for the architectural rationale.
This doc covers rollout mechanics, rollback, and current status.

## Status (as of `refactor/v7-only-remove-v6-autonomous-path`)

`AUTO_TRADE_CONTRACT_MODE` is no longer a migration mode with multiple live
values in real deployments - `v7_only` is the only value Python's Settings
accepts (fails closed at startup on anything else), and it's what
`AutoTradeOptions.FromEnvironment()` on the C# side now resolves to by
default too. `AutoTradeOptions.Validate()` on the C# side deliberately
still accepts all four historical values (`legacy_v6` included) rather
than also restricting to `v7_only` - not because a non-`v7_only` value is
meaningful in production, but because hundreds of pre-existing C# tests
construct `AutoTradeOptions` directly (bypassing `FromEnvironment`) via a
shared test helper that never sets `ContractMode`, relying on the record's
bare default. `AutoTradeEngine.ProcessCandidateAsync` rejects every
autonomous (non-manual-algo) candidate outright when `ContractMode ==
"v7_only"` (defense-in-depth, Section L) - so making `v7_only` the bare
record default too would make those tests' autonomous V6 candidates get
rejected at the door, breaking mechanical-execution coverage (sizing,
stops, targets, BE) that has nothing to do with the autonomous-path
boundary. Real deployments are unaffected: they always go through
`FromEnvironment()` (defaults to `v7_only`), and any other C#-side value
still fails closed via the cross-service `AutoTradeConfigHealth`
fatal-mismatch check against Python's `v7_only`-only manifest. TradePlan
V7 is the only autonomous order-creation path either way; the parallel V6
autonomous candidate path (scanner-routed, private M1 range, trend) has
been removed from `worker.py`'s publish wiring outright, not gated behind
a mode. The sections below (mode table, `shadow_v7`/`v7_primary`
follow-up items) describe the migration as it ran up to this point and
are kept for history; treat "Migration modes" as historical, not
configurable today.

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
  unconditionally (no mode gate). Claims the active thesis
  (`claim_active_thesis`) before building - a second setup for an
  already-claimed thesis is rejected, never a second plan. The three
  private V6 autonomous publish call sites (scanner-routed
  `_publish_strategy_match`, the private M1 range gate, the trend detector)
  are removed from the autonomous wiring entirely - not skipped-and-
  recorded, gone - so a confirmed setup can never arm both a V7 plan and a
  V6 candidate. The three functions themselves remain in the codebase only
  because they are still directly unit-tested for their internal guard/veto
  logic; they have no autonomous caller.
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
  `trend.py`) still run and still independently analyze on every event;
  they simply have no autonomous publish call site left to feed - the box
  and trend intents always resolve to `published = None` now, and
  `map_strategy.py`'s Mapped Zone Reaction detector was never wired to an
  autonomous publish call site in the first place. They are not
  architecturally isolated from the V7 pipeline the way Section A of the
  originating task describes; full removal/repurposing of these detectors
  is out of scope for this change (see `refactor/v7-only-remove-v6-
  autonomous-path`'s P1 prompt) and tracked as P2/P3 follow-up.
- No single end-to-end test exercises the full Python -> Redis -> C# chain
  in one run. Both sides have real, non-mocked coverage of their own half
  (`test_publish_trade_plan_v7.py` proves Python publishes a valid,
  parseable plan; `TradePlanRuntimeTests.cs` proves C# consumes a plan JSON
  and places a real order against a fake broker) but a genuine
  cross-process/cross-language integration harness was not built.

## Migration modes (historical)

`AUTO_TRADE_CONTRACT_MODE` supported four values while this migration was
rolling out; as of this document, Python's Settings validation accepts
only `v7_only` and real C# deployments (via `FromEnvironment`) default to
`v7_only` too - see the Status section above for why C#'s `Validate()`
itself stays lenient on the four historical values (a test-suite
constraint, not a production one). The field must still match exactly on
both services - a mismatch is fatal, unchanged. The earlier values are
kept here for history:

| Mode | Python behavior | C# behavior |
|---|---|---|
| `legacy_v6` (former default) | Published only V6 `TradeCandidate`s | Consumed only V6 |
| `shadow_v7` | Also published V7 plans | Parsed/validated/armed V7, placed no orders from it |
| `v7_primary` | V7 and V6 both published | V7 placed orders; V6 remained a parallel path |
| `v7_only` (sole value now) | Publishes only V7 plans | Executes only V7 autonomously (manual /algo exempt) |

## Rollback

There is no config-flip rollback anymore - `legacy_v6`/`shadow_v7`/
`v7_primary` are rejected values on both services (fail closed at startup,
not a silent no-op). Reverting to a parallel V6+V7 publish state requires
reverting the commit that removed the autonomous V6 call sites in
`worker.py` (see `refactor/v7-only-remove-v6-autonomous-path`) and the
matching `AUTO_TRADE_CONTRACT_MODE` validation changes on both services,
then redeploying both together. No data migration is required either way -
V6 candidates, open V6 positions, and V6 Redis keys are untouched by this
change; V7's `execution:*` keys are a separate namespace V6 never reads or
writes. A restart while V7 plans are armed/open recovers from
`execution:plan_runtime:{plan_id}` and the runtime's own copy of the plan
JSON, without re-arming or duplicating an order.

## Legacy compatibility

- V6 `TradeCandidate` and its `Planned*` field family are unchanged.
- `execution:*` keys are a separate namespace - nothing in V6 reads or
  writes them, so V7 work cannot corrupt V6 state by construction.
- Existing open V6 positions remain manageable; nothing in this change
  touches `AutoTradePositionState` or existing position-management code
  paths. The manual `/algo` command path (owner-typed, not autonomous) is
  likewise unaffected - it shares the same V6 `TradeCandidate` stream and
  `IsManualAlgoCandidate` carve-out in `AutoTradeEngine.ProcessCandidateAsync`
  it always has.

## Next work

In priority order:

1. Extend the real-Redis cross-language contract proof into a continuously
   deployed smoke test. The repository now verifies Python publication and
   the production C# source-generated consumer against the shared V7 fixture,
   including malformed-then-valid recovery, but CI does not connect to a real
   cTrader broker.
2. Market Map `entries`/`actionable_entries` -> `display_entries`/
   `strategy_zones` rename, as its own reviewable change.
3. P2/P3: repurpose or fully remove the private M1 range gate and trend
   detector now that neither feeds order creation, per the P1 prompt's
   forward-reference.
4. Only after the above are stable and observed on demo: `feat/analysis-h1-
   structure-map` - deliberately sequenced after the ownership boundary,
   not before it.

No live trading, VPS deployment, or automatic merge is authorized by this
branch or this document.
