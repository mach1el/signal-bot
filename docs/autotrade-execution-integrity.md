# Auto-trade execution integrity

This contract keeps ranked intent selection, risk validation, Redis ownership,
and executor recovery consistent across the Python publisher and C# executor.
When any ownership or stop-plan evidence is uncertain, execution fails closed.

## Ranked cycle ownership

One closed M1 event is one arbitration cycle. The worker ranks all executable
intents first, then acquires:

```text
auto_trade:cycle_route_lock:{SYMBOL}:{cycle_id}
```

The cycle lock serializes evaluation of the complete ranked list. A separate
per-route lock:

```text
auto_trade:route_lock:{SYMBOL}:{match_id}
```

prevents two workers from evaluating the same StrategyMatch concurrently. The
route lock does not choose the cycle winner. Both lock types use random owner
tokens, a finite TTL, and compare-and-delete Lua release so an expired owner
cannot delete a successor's lock.

Publication returns a typed result. `duplicate_candidate`,
`route_in_progress`, `cycle_conflict`, `duplicate_reaction`,
`duplicate_thesis`, and `publication_unavailable` all preserve the preferred
route and block lower-ranked intents. Only an intent-specific
`terminal_reject` permits fallback. The authoritative cycle winner is stored
at:

```text
auto_trade:cycle_owner:{SYMBOL}:{cycle_id}
```

Duplicate event delivery therefore cannot replace the first valid winner.

## Shared protective-stop plan

Python plans the protective stop with Decimal arithmetic before reward/risk
approval. BUY and SELL use mirrored formulas:

```text
BUY structure = swing - structure_buffer_atr × ATR
SELL structure = swing + structure_buffer_atr × ATR
BUY wick      = sweep_low  - wick_buffer_atr × ATR
SELL wick     = sweep_high + wick_buffer_atr × ATR
```

When a sweep exists, BUY selects the lower structure/wick price and SELL the
higher one. An invalid-side raw stop is rejected. A wick beyond the maximum
envelope is rejected; an ordinary raw stop is clamped to the strategy's
minimum and maximum stop bands. The selected price is rounded to symbol digits
with midpoint-away-from-zero behaviour, then distance and pips are recomputed.
Reward/risk uses this final `planned_stop_pips`, never raw swing distance.

Candidate payload v5 carries:

```text
planned_stop_entry_price
planned_stop_price
planned_stop_distance
planned_stop_pips
planned_stop_raw_price
planned_stop_clamped
planned_stop_source
stop_plan_version
```

Candidate contract v6 advertises support for this schema. The executor
recomputes the plan from the candidate and broker symbol metadata. Entry,
stop, distance, and raw stop may differ by at most one symbol tick; stop pips
use the equivalent one-tick pip tolerance. Clamp state, stop plan version, and
the selected `structure`, `wick`, or `structure_and_wick` source must match
exactly. A mismatch rejects with
`protective_stop_contract_mismatch`.

## Group-plan retention

`auto_trade:group_plan:{group_id}` is written only immediately before a broker
request that needs reconnect/adoption metadata. It expires after
`AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS` (with a five-minute floor), never
persistently.

The plan is retained for accepted pending orders, newly filled limits, and
reconnect-adopted positions. It is deleted after submission failure, partial
zone-placement rollback, owner cancellation, expiry with no remaining order or
position, range-flip cancellation/closure, full TP, owner close, or any other
terminal group state with no pending order or tracked position. Paused,
exposure-rejected, and dry-run candidates never create a plan.

## Publication authority and recovery

Production candidate publication uses Redis Lua for isolated claim checks and
one `XADD`. Redis script execution is isolated from other clients, but it does
not roll back commands already executed when a later command fails. The
candidate stream event is therefore the authoritative publication record.

Every event embeds the candidate, reaction, thesis, and cycle-ownership
identities needed for recovery. Reconciliation finds the exact event, restores
missing or expected-prior secondary claims with TTLs, and never calls `XADD`.
Conflicting ownership fails closed. Current publication readiness is marked
unready when Lua or reconciliation is unavailable; compatibility fallback is
allowed only for explicitly marked test doubles.

## Route recovery

Route history remains append-only evidence. The current route snapshot clears
an old `terminal_reason_code` when a later transition reaches `checking`,
non-terminal `waiting`, `candidate_published`, `executor_received`,
`order_submitted`, or `order_filled`. A new terminal transition replaces the
current terminal reason without deleting the prior history.

## Production-path Redis tests

Tests marked `real_redis` require `REAL_REDIS_URL` and must run against a real,
disposable Redis server. They exercise the actual Lua path with no FakeRedis
fallback and verify concurrent candidate/cycle ownership, reaction/thesis
consistency, fail-closed behaviour, readiness on invalid script output, and
orphan reconciliation without a second stream event. FakeRedis tests cover
compatibility only and are not evidence of production Lua atomicity.
