# Deterministic StrategyMatch Handoff

## Baseline

- Baseline commit: `159c38629fc24f33a10b477b02e0d584eeeceedb`
  (merge of PR #142).
- Relevant merge chain:
  - #137: confluence merge and M1 trigger
  - #138: forming-card lifecycle
  - #139: confluence card and scanner R/R pre-gate
  - #140: scanner cross-side actionability
  - #141: reaction confirmation entry handoff
  - #142: distance-based execution and soft M1
- Baseline checks:
  - Python: 1112 passed
  - C#: 541 passed
  - `docker compose config -q`: passed

## Incident Map Before P0

Scanner and worker subscribe independently to the lossy `bars:new` Pub/Sub
channel:

```text
bars:new M5
  -> scanner._handle_event
  -> detectors
  -> same-side confluence merge
  -> actionability
  -> provisional reward/risk gate
  -> _sync_strategy_match
  -> StrategyMatch Redis keys + CONFIRMED setup lifecycle
  -> forming card says "Algo bot CHECKING"

bars:new M1
  -> worker._handle_event
  -> load current StrategyMatches
  -> static/dynamic preflight
  -> arbitration
  -> _publish_trade_plan_v7
  -> execution:trade_plans
```

The scanner ignores every timeframe except its M5 execution timeframe. The
worker ignores every timeframe except M1. If the worker consumes the matching
minute's M1 event before the scanner persists the M5 match, the worker sees no
match. Persistence does not wake it, so the confirmed setup waits for a later
M1 event. Redis persistence is durable; the wake-up is not.

PR #142 does not remove that ordering race. It changes both worker preflight
and publication to treat `distance_pips <= 40` as positive authorization.
Consequently it can chase outside the raw confirmed zone while still missing
an in-zone setup when M1 arrived first.

Static eligibility is also split:

- scanner actionability can record invalid target-room or key-role decisions
  as soft observations and continue creating a StrategyMatch;
- worker repeats target-room and opposing-structure checks later;
- a forming card can therefore claim execution checking even though unchanged
  geometry will be rejected by the worker.

## Target Lifecycle

```text
OBSERVED
  -> STATIC_ELIGIBLE
  -> MATCH_PERSISTED
  -> READY_EVENT_ENQUEUED
  -> WORKER_ACKNOWLEDGED
  -> WAITING_RETEST | PLAN_PUBLISHED | INVALIDATED | EXPIRED
```

`auto_trade:strategy_match_ready` is a durable wake-up reference. The
canonical StrategyMatch and setup lifecycle remain the execution source of
truth.

## Durable Ready Contract

- Stream: `auto_trade:strategy_match_ready`
- Schema: version 1
- Consumer group: `algo-worker`
- Consumer identity: `${HOSTNAME}:${PID}`
- Dedup: one normal and one recovery reservation per setup; duplicate stream
  entries remain harmless because setup, plan, route, thesis and confluence
  ownership are deterministic.
- Acknowledgement: only after the setup reaches
  `ARMED_WAITING_TRIGGER`, `PLAN_PUBLISHED`, or a terminal state.
- Recovery: `XAUTOCLAIM` handles pending entries. Startup re-enqueues active,
  unexpired confirmed, queued, acknowledged, or armed matches only when no
  plan is published and no different setup owns the thesis.
- Canonicality: the event is only a reference. Worker reloads the persisted
  `StrategyMatch` and lifecycle, then validates identity, direction, and entry
  bounds before processing.

TradePlan V7 publication uses one Lua operation for the deterministic plan
key, published-state key, and `execution:trade_plans` XADD. A retry sees the
existing plan rather than emitting another executor event.

## Responsibility Matrix

| Boundary | Scanner | Worker | C# |
| --- | --- | --- | --- |
| Detection and structural confirmation | owns | no | no |
| Static actionability, key role, target room, provisional R/R | owns | audits only when context changes | no |
| Ready-event persistence | owns | consumes/acks | no |
| Fresh bid/ask and raw-zone membership | no | owns | mechanical recheck |
| Expiry/invalidation/news/cooldown/claims | no | owns | no |
| Stop and final R/R | no | owns | consumes declared plan |
| Broker spread/slippage/submission | no | no | owns |

## Family Policy Matrix

| Family | Setup/confirmation | Entry authorization | M1 | Expiry/invalidation |
| --- | --- | --- | --- | --- |
| Structural reaction | M5 scanner-confirmed reaction | side-aware quote inside raw zone plus contract tolerance | optional stop refinement | match expiry and structural swing |
| Breakout retest | scanner-confirmed retest | strategy entry/retest contract | required family trigger | match expiry and breakout invalidation |
| Trend pullback | M5 setup under H1/M15 trend context | pullback zone; no generic distance authorization | required family trigger | trend structure/match expiry |
| Breakout continuation | M5 setup under H1/M15 context | continuation zone; no generic distance authorization | required family trigger | breakout structure/match expiry |
| Box/range edge | M5 scanner range episode | confirmed range rail | required family trigger | range withdrawal/break/match expiry |
| Legacy private range/trend | existing M1 private route | unchanged legacy candidate contract; not ready-stream driven | route-specific | existing telemetry lifecycle |

## Migration And Rollback

On startup, active unexpired CONFIRMED/queued/acknowledged/armed matches with a
valid lifecycle and no published deterministic V7 plan are re-enqueued as
recovery events. Every recovered setup is rechecked against current quote and
context; Redis does not need to be flushed.

Rollback may stop the ready-stream consumer and revert the code. Existing
StrategyMatch/setup keys remain compatible. Unacknowledged stream entries are
inert to the previous runtime and can be retained or removed with the
`algo-worker` consumer group; active position and execution-plan state must
not be deleted.

Safe stream-only cleanup after rollback, when no new runtime is consuming it:

```text
XGROUP DESTROY auto_trade:strategy_match_ready algo-worker
DEL auto_trade:strategy_match_ready
```

Do not delete `execution:*`, active thesis/confluence claims, setup lifecycle,
or broker position state.

## Verification

- Focused P0 matrix:
  `125 passed, 1 deselected, 1 warning`
- Redis-backed handoff/restart replay:
  `9 passed, 1 warning`
- Full Python suite:
  `1124 passed, 1 warning`
- Full C# suite with `REAL_REDIS_URL`:
  `541 passed`
- `python -m compileall -q app tests`: passed
- `docker compose config -q`: passed using a temporary copy of
  `.env.example`; the local file was removed afterwards
- `git diff --check`: passed
- C# source changes: none

The Python warning is the existing `pandas_ta` use of pandas'
`mode.copy_on_write` option.
