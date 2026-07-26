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

## Final protective-stop pipeline

Python and C# share one final protective-stop planner (`stop_plan_version=2`).
The planner includes every deterministic transformation before broker
submission:

1. base structure stop;
2. ATR structure buffer;
3. sweep/wick invalidation;
4. min/max stop envelope;
5. symbol rounding (midpoint away from zero);
6. opposing-zone evaluation;
7. optional push beyond an execution-grade opposing zone;
8. final envelope validation after the push;
9. final stop distance and stop pips;
10. final reward/risk calculation.

### Base stop versus final broker stop

- **Base stop** is the structure/wick result after envelope clamp and rounding.
- **Final stop** equals the base stop unless an execution-grade opposing zone
  contains the base stop and push is enabled.
- Context-only opposing zones (wider than configured pip/ATR limits) remain
  telemetry and never mutate the stop.
- Push disabled with stop inside the zone rejects with
  `stop_inside_opposing_zone`.
- Push enabled moves BUY to `zone_low - buffer_atr×ATR` and SELL to
  `zone_high + buffer_atr×ATR`, then re-validates side-of-entry and max
  envelope. A pushed stop that exceeds the envelope is rejected; it is never
  clamped back into the zone.

### Candidate contract fields

Candidate payload v5 with `stop_plan_version=2` carries:

```text
planned_stop_entry_price
planned_base_stop_price / planned_base_stop_pips
planned_final_stop_price / planned_final_stop_distance / planned_final_stop_pips
planned_stop_price / planned_stop_distance / planned_stop_pips   # final aliases
planned_stop_raw_price
planned_stop_clamped
stop_source
stop_adjustment                    # none | opposing_zone_push
stop_adjustment_zone_id/low/high
stop_plan_version                  # 2
```

Legacy `planned_stop_*` values are the **final** stop so older readers still
see the broker stop. Candidate contract advertisement remains v6 during
rolling deploy; stop-plan version is the authoritative planner schema.

### Executor validation and entry tolerance

The executor recomputes the complete final plan at the current executable
entry. Prices/distances may differ by at most one symbol tick; boolean,
version, source, adjustment, and zone identity must match exactly. Mismatch
rejects with `final_protective_stop_contract_mismatch`.

No function may mutate the stop after contract validation. The validated
final stop is the broker stop.

If the market moves enough that the recomputed final contract diverges beyond
tolerance, reject. Do not silently widen the stop or preserve stale Python RR.

### Final reward/risk

Python preflight RR uses `final reward distance / final stop distance`.
C# independently rechecks RR after final-stop reconstruction for every
autonomous initial strategy with a policy minimum RR, using the same
`fill_relative` / `absolute` / `hybrid` target model. Pullback adds keep their
dedicated stop model and are not validated against the initial candidate stop
contract.

## Structured candidate execution state

Redis key `auto_trade:candidate:{id}` stores a structured record:

```json
{
  "candidate_id": "candidate-123",
  "stream_event_id": "1720000000000-0",
  "state": "published",
  "lease_token": null,
  "lease_expires_at": null,
  "outcome": null,
  "updated_at": 1720000000,
  "version": 1
}
```

States include `published`, `processing`, `broker_submitting`, `ordered`,
`completed`, `rejected`, `retryable_error`, and `broker_outcome_unknown`.
Readers also parse legacy plain strings (`published`, `processing`,
`ordered:<id>`, `rejected:<reason>`, `dry_run`).

## Publication authority and all-or-nothing recovery

Production candidate publication uses Redis Lua for isolated claim checks and
one `XADD`. Redis script execution is isolated from other clients, but it does
not roll back commands already executed when a later command fails. The
candidate stream event is therefore the authoritative publication record.

Every event embeds the candidate, reaction, thesis, and cycle-ownership
identities needed for recovery. Reconciliation is two-phase:

1. **Validate** every target key (candidate state, stream-event marker,
   reaction claim, thesis claim, cycle owner). Conflict if any key belongs to
   a different identity. Compatible executor-progress states for the same
   candidate and stream event (`processing`, `ordered`, `rejected`, …) are
   not conflicts.
2. **Restore** missing keys only when every key is compatible. Never reset a
   progressed executor state back to `published`. Never call `XADD`. Never
   partially restore if a later key conflicts.

## Token-owned fenced executor leases

`TryClaimCandidateAsync` returns a `CandidateExecutionLease` with a
cryptographically strong token, stream event id, and expiry. Acquire is
allowed only from `published` or an expired processing lease for the same
stream event.

Atomic Lua operations:

- **Renew** — token + candidate + stream event + processing/submitting;
- **Transition** — token must match (for example `processing → broker_submitting`);
- **Complete** — token must match; clears lease; retains outcome TTL;
- **Release** — token must match; transitions to `retryable_error` rather than
  deleting the record.

Before every broker side effect the executor renews/verifies the lease and
transitions to `broker_submitting`. Ownership loss before submission aborts.
Ownership loss after a possibly-accepted broker request enters
`broker_outcome_unknown` and reconciles by deterministic client order id
instead of blind retry. Stale owners cannot release, complete, or transition
a successor's lease.

## Lifecycle events versus lifecycle transitions

Event history is append-only. Only explicit state-changing events update
lifecycle snapshots. Unknown or telemetry types (`warning`, `config_health`,
`account_capability`, `ready`, `range_flip_attempted`, `stop_moved`, …)
append history with `mutates_lifecycle=false` and **do not** default to
`managing`.

Candidate lifecycle keys are written only for a real candidate or group
owner. Service readiness uses readiness/config keys and must never create
`lifecycle_state:service`.

Current-state records are structured JSON (`owner_id`, `state`,
`previous_state`, `event_type`, `event_id`, identities, `terminal`,
`version`). Impossible backward transitions are rejected as telemetry/
invalid metrics; documented recovery transitions
(`executor_received → order_filled`, missing → managing with evidence)
remain allowed. Range-side state updates only for mapped lifecycle
transitions.

```text
executor_received → routing_selected → order_planned → order_submitted
  → order_accepted → order_filled → managing → partially_closed → closed
rejected | expired | cancelled | invalidated | error   (terminal)
```

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

## Route recovery

Route history remains append-only evidence. The current route snapshot clears
an old `terminal_reason_code` when a later transition reaches `checking`,
non-terminal `waiting`, `candidate_published`, `executor_received`,
`order_submitted`, or `order_filled`. A new terminal transition replaces the
current terminal reason without deleting the prior history.

## Rollout compatibility

Recommended sequence:

1. Deploy Redis-compatible readers that parse legacy and structured candidate
   states.
2. Deploy executor lease/fencing support.
3. Deploy updated Python publication/reconciliation.
4. Switch publishers to `stop_plan_version=2` final-stop fields.
5. Verify config health; keep execution demo-only during observation.
6. Remove legacy compatibility only in a later PR.

Old executors must not misread a structured candidate JSON blob as a claimable
plain `published` string. New claim Lua only treats explicit `published`
(legacy or structured) or expired leases as claimable.

## Production-path Redis tests

Tests marked `real_redis` / `REAL_REDIS_URL` require a real disposable Redis
server (Redis 7 via `docker run --rm -p 6379:6379 redis:7-alpine`). They
exercise the actual Lua path with no FakeRedis fallback and verify concurrent
candidate/cycle ownership, reaction/thesis consistency, fail-closed
behaviour, readiness on invalid script output, orphan reconciliation without
a second stream event, executor-progress-compatible recovery, and fenced
lease acquire/renew/release. FakeRedis tests cover compatibility only and are
not evidence of production Lua atomicity.
