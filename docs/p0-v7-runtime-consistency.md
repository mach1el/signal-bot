# P0 TradePlan V7 Runtime Consistency

## 1. Baseline

- Base branch: `master`
- Baseline SHA: `a22e72078965ceb1b9cafc72c3e9c69f3ff97d52`
- Python baseline: `1124 passed, 1 warning in 58.72s`
- C# baseline: `541 passed`
- Compose baseline: `docker compose config -q` passed

The C# baseline emitted `IL2026` and `IL3050` from
`TradePlanRuntime.cs`: its production path called reflection-based generic
`JsonSerializer` overloads while the project enabled trimming and Native AOT.
A green JIT suite therefore did not prove the production binary could parse a
TradePlan V7 payload.

Recent merged pipeline work inspected from runtime code:

- PR #143: durable `strategy_match_ready` handoff
- PR #142: distance-based execute/retest routing
- PR #141: reaction entry contract
- PR #140: scanner actionability
- PR #139: reward/risk pre-gate
- PR #138: setup-card lifecycle
- PR #137: confluence and M1 confirmation
- PR #136: H1/M15/M5 structure adherence
- PR #135: V7-only autonomous publication
- PR #133: TradePlan V7 runtime

## 2. Root Cause Analysis

Incident A, published plan reported as waiting:

- The ranked strategy publisher awaited `_publish_trade_plan_v7()` without
  assigning its return value, then explicitly reset `published` to `None`.
- Arbitration, route telemetry, worker summary, winner intent and Telegram
  therefore saw a local failure after Redis had durably published the plan.

Incident B, C# session crashed on V7 JSON:

- `TradePlanRuntime` used generic reflection-dependent System.Text.Json
  overloads in a trimmed Native-AOT application.
- The V7 poll shared the broad auto-trade session exception boundary, so a
  payload/runtime exception could terminate and reconnect the whole session.

Incident C, published setup re-entered preflight:

- M1, ready-event and recovery paths did not resolve deterministic V7 state
  before dynamic guards.
- A generic terminal mapping attempted `INVALIDATED` for any non-terminal
  setup, including `PLAN_PUBLISHED`, producing an illegal lifecycle edge.

Incident D, Telegram duplicate status edit:

- Card status was an untyped scalar without atomic priority.
- Cached text was not compared before editing, and all
  `TelegramBadRequest` responses entered fallback behavior, including
  Telegram's successful-equivalent `message is not modified` response.

Additional consistency defects:

- Scanner checked provisional R/R but could ignore `evaluation.allowed`.
- Plan idempotency expired with the short payload TTL.
- V7 cycle publication did not persist the complete winning owner record.
- The C# recovery copy reused Python's short-lived plan key, accidentally
  removing its TTL.
- Feed broker identity was only validated after backfill/subscription when the
  auto-trade task started, allowing a multi-broker token to expose scanner data
  before executor broker validation.

## 3. Production Log Replay

The Python incident sequence was:

```text
phase=in_zone_waiting_m1 reason_code=entry_contract_satisfied
stale M1 trigger ignored
phase=trigger_ready confirmation_source=m5_authoritative
phase=published
v7 plan published id=v7:d870ddc73b268fcb2feaa14a891c2108
state=strategy_match_waiting candidate=-
```

The C# incident was:

```text
Reflection-based serialization has been disabled for this application.
Either use the source generator APIs or explicitly configure the
JsonSerializerOptions.TypeInfoResolver property.
```

The post-publication incident then evaluated
`entry_inside_opposing_zone` and attempted:

```text
plan_published -> invalidated
```

Telegram repeatedly returned:

```text
Bad Request: message is not modified
```

All four sequences now have direct regression coverage.

## 4. Before And After State Flow

Before:

```text
M5 scanner -> StrategyMatch -> ready stream -> worker preflight
-> Redis publishes V7 -> local result discarded -> waiting card
-> later event reruns preflight -> illegal invalidation
-> C# reflection exception reconnects the auto-trade session
```

After:

```text
M5 scanner full-policy pass
-> canonical StrategyMatch and monotonic setup lifecycle
-> durable ready event
-> resolve existing deterministic V7 state
-> final dynamic preflight and arbitration
-> PLAN_BUILT
-> atomic payload + state + dedup tombstone + stream publication
-> PLAN_PUBLISHED and durable cycle owner
-> monotonic Telegram card projection
-> C# source-generated deserialize and validate
-> claim + recovery/runtime state + executor acknowledgement
-> cursor advance
-> mechanical broker execution
```

Duplicate M1/ready/recovery work resolves the same plan and cannot append a
second stream entry.

Before symbol resolution, backfill or subscription, the feed now verifies the
configured account and `CTRADER_EXPECTED_BROKER`. Production pins both feed
and execution to FP Markets account `47977211`.

## 5. C# JSON Source Generation

`AutoTradeJsonContext` generates snake-case metadata for:

- `TradePlan` and all reachable nested V7 contract types
- `TradePlanRejectionRecord`
- `TradePlanExecutorAcknowledgement`

`TradePlanStateJsonContext` preserves the existing runtime-state property and
string-enum representation for restart compatibility.

Every production V7 serialize/deserialize call now uses a generated
`JsonTypeInfo`. `DefaultJsonTypeInfoResolver` is not enabled. Startup parses
and validates a minimal V7 fixture through the exact production context before
readiness and logs either:

```text
V7 JSON contract self-test passed
V7 JSON contract self-test failed
```

## 6. C# Recovery And Acknowledgement Semantics

This repository uses XREAD plus a durable cursor, not a Redis consumer group,
for V7. Cursor advancement is therefore its acknowledgement boundary.

- Malformed/unsupported: persist
  `execution:plan_rejection:{stream_id}`, update plan rejection state/ack when
  identity is extractable, publish `plan_rejected`, then advance the cursor.
- Transient Redis/broker error: do not advance the cursor; bounded backoff
  retries the same entry.
- Valid: deserialize, validate, claim, persist recovery/runtime state and
  `armed` acknowledgement, then advance the cursor.
- Duplicate: reconcile the existing claim/state, do not submit, then advance
  as an idempotent no-op.

The V7 poll has its own retry boundary inside `AutoTradeEngine`; failures log
`auto_trade_consumer_restarting`, recovery logs
`auto_trade_consumer_recovered`, and the enclosing cTrader session stays
connected.

## 7. Python Post-Publication Reconciliation

`resolve_existing_v7_state()` loads:

- deterministic plan ID
- setup lifecycle
- plan payload presence
- canonical plan state
- symbol/cycle owner match

It runs before strategy preflight and publication. Published/armed/submitted/
filled/managing plans return the deterministic existing result. Terminal
states stop processing. `PLAN_BUILT` plus a published payload reconciles to
`PLAN_PUBLISHED`; an incomplete build maps to `CANCELLED`.

Ready-event duplicates are acknowledged without fresh geometry evaluation.
The scanner reloads lifecycle after enqueue and no longer overwrites worker
owned queued/preflight/published route state.

## 8. Lifecycle Transition Mapping

Terminal preflight mapping is explicit:

```text
pre-plan states -> INVALIDATED
PLAN_BUILT -> CANCELLED
PLAN_PUBLISHED / ARMED / CONSUMED -> no preflight transition
terminal states -> no transition
```

No `PLAN_PUBLISHED -> INVALIDATED` edge was added. A canonical terminal plan
state takes precedence over a retained payload during reconciliation.

## 9. Dedup Retention

The publisher's Lua transaction checks and sets:

```text
execution:plan_dedup:{plan_id}
```

in the same operation as plan payload, published state and XADD. Retention is
`max(24h, AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS)`, seven days by default,
which matches the repository's setup/candidate operational retention. Legacy
payloads without a tombstone are backfilled on retry. Removing the short plan
payload/state cannot permit a second XADD.

The winning symbol/cycle owner now stores symbol, cycle ID, intent ID, setup
ID, plan ID and publication timestamp under the existing cycle lock.

## 10. Telegram Monotonic Status

Card state is stored as typed JSON with priorities:

```text
analysis_only=0, queued=10, preflight=20, waiting_retest=30,
trigger_ready=40, plan_published=100, executor_armed=120, terminal=200
```

A Redis Lua compare-and-set prevents delayed lower states from winning.
Legacy scalar statuses remain readable. Identical cached text skips Telegram.
`message is not modified` is success; genuine API errors retain concise
diagnostics and existing card ownership.

Analysis-only detections render `MARKET OBSERVATION`, exact policy reason and
no order-style copy draft. Executable matches retain the one-card setup flow.

## 11. Changed Files

- `algo-bot/app/analysis/scanner.py`: full-policy eligibility, truthful
  analysis cards and scanner/worker telemetry ownership.
- `algo-bot/app/autotrade/worker.py`: durable V7 reconciliation, lifecycle
  mapping and publication-result propagation.
- `algo-bot/app/autotrade/trade_plan_stream.py`: atomic dedup tombstone.
- `algo-bot/app/autotrade/candidate_publish.py`: durable cycle owner.
- `algo-bot/app/autotrade/setup_card.py`: typed monotonic status and Telegram
  idempotency.
- `ctrader-engine/src/TradePlanRuntime.cs`: source-generated JSON,
  per-message durability, acknowledgements, recovery copy and structured logs.
- `ctrader-engine/src/AutoTradeEngine.cs`: startup self-test and bounded V7
  consumer recovery.
- `ctrader-engine/src/FeedRunner.cs`, `FeedOptions.cs`, `BrokerIdentity.cs`:
  fail-closed FP Markets account validation before market-data ingestion.
- `ctrader-engine/src/CTraderFeed.csproj`: compatible Native-AOT/single-file
  production properties.
- Python and C# test files: incident, concurrency, real-Redis, malformed,
  transient, policy and contract regressions.
- `docs/redis-contract.md`, `docs/trade-plan-v7-migration.md`,
  `CHANGELOG.md`: operational contract and release notes.

## 12. Exact Validation Results

```text
Python focused V7/card/stream:
57 passed, 1 dependency warning

Python full:
1139 passed, 1 dependency warning in 50.92s

Python compile:
python -m compileall -q app tests
passed

C# focused V7:
26 passed

C# full:
549 passed

C# Release build:
Build succeeded. 0 Warning(s), 0 Error(s)

Compose:
docker compose config -q
passed using .env.example as the temporary local env file

Production images:
docker compose build ctrader-engine bot
both images built; ctrader-engine completed Native-AOT code generation

General:
git diff --check
passed
```

The Python warning is the baseline `pandas_ta` use of the deprecated pandas
`mode.copy_on_write` option. Native-AOT emits third-party dependency analysis
warnings for System.Reactive, Websocket.Client, OpenAPI.Net and
Google.Protobuf; there is no TradePlan runtime reflection warning.

## 13. Redis-Backed Proof

Real Redis tests prove:

- scanner -> ready stream -> worker publication and pending recovery
- dedup tombstone after payload deletion
- concurrent Telegram status CAS
- malformed C# entry followed by a valid Python fixture

A cross-language smoke then used Python's production `publish_trade_plan()` to
write `plan-001` to Redis after a malformed entry:

```text
python_published ... plan_id=plan-001 stream_len=2
```

The C# runtime consumed that exact stream through the production generated JSON
context; the filtered test passed, asserted durable rejection for the first
entry and `execution:plan_state:plan-001=armed` for the second. The broker
adapter was fake, so this proves the distributed contract and mechanical arm
boundary without placing a cTrader order.

## 14. Migration Notes

- No database migration and no Redis flush are required.
- Existing card scalar values are read and upgraded on the next status write.
- Existing V7 payloads gain a dedup tombstone on their next publication retry.
- New acknowledgement, rejection and recovery keys are created lazily.
- Deploy config must set `CTRADER_EXPECTED_BROKER=fpmarkets`; the field
  defaults to `AUTO_TRADE_EXPECTED_BROKER` for local backward compatibility.
- C# falls back to the old plan key when restoring pre-change runtime state,
  then writes future recovery copies to the dedicated key.
- Deploy Python and C# from the same commit so source contract and lifecycle
  semantics advance together.

## 15. Rollback Notes

Revert this commit and redeploy both services together. Do not delete Redis
data: old binaries ignore the new dedup, acknowledgement, rejection and
recovery keys, while V6/open-position state is untouched. A rollback removes
the new safeguards, so pause new autonomous entries during mixed-version
deployment and let already-open positions continue under the existing
executor reconciliation.

## 16. Remaining Limitations

- The Redis-backed cross-language smoke uses a fake broker; no live/demo
  cTrader order was submitted by this PR.
- V7 uses the repository's durable-cursor XREAD model rather than
  XREADGROUP/XPENDING. Safety is provided by advancing only after a durable
  outcome.
- Executor plan-state monotonicity is read/write because the current store
  interface has no compare-and-set primitive. Production runs one executor
  session; publisher dedup and Telegram multi-writer status are atomic Lua.
- Native-AOT dependency warnings remain in third-party cTrader/WebSocket/
  Protobuf libraries and should be tracked independently.
- Telegram projects `PLAN PUBLISHED` in this PR; automatic projection of every
  later executor acknowledgement can be added without creating new cards.
