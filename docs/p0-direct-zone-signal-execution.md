# P0: Direct Zone-Signal Execution (refactor/p0-direct-zone-signal-execution)

Baseline commit: `e21874530e9e74fdc2e8301207151aebd910de70` (master, verified
via `git fetch origin --prune && git switch master && git pull --ff-only`).

## 1. Baseline

Full Python suite at baseline (real Postgres 16 on `localhost:55432`, real
Redis 7 on `localhost:6390`, fakeredis for unit tests):

```
pytest -q -m "not real_redis"   -> 1161 passed, 26 deselected
REAL_REDIS_URL=... pytest -q -m real_redis -> 26 passed, 1161 deselected
```

1187/1187 passing, 0 failures. No baseline test infrastructure existed for
Postgres in this sandbox; it was provisioned (see "Validation environment"
below) specifically to get an honest baseline before editing anything.

## 2. Root cause (verified against source, not docs/CHANGELOG)

The reported symptom - "valid entries have been consumed from Redis without
producing a TradePlan" - has two independent, compounding causes:

**(a) The ready-stream event is ACKed before a plan exists.**
`app/autotrade/worker.py` (`_process_strategy_match_ready_entry`, pre-fix)
treated `ARMED_WAITING_TRIGGER` as a "durable enough to ack" outcome:

```python
durable = setup.state in {ARMED_WAITING_TRIGGER, PLAN_PUBLISHED, *TERMINAL_STATES}
if not durable: return False
await client.xack(READY_STREAM, READY_GROUP, stream_id)
```

But per `setup_lifecycle.py`'s own docstring, `ARMED_WAITING_TRIGGER` is
exactly the node where a retest/M1-timing *wait* begins - no TradePlan has
been built yet. Once ACKed, Redis Streams never redelivers that wake-up.

**(b) The only remaining re-drive for a waiting setup was non-durable
Redis Pub/Sub.** `auto_scalp_loop()` subscribes to `bars:new` (fire-and-forget,
no persistence, no consumer group). If that subscription misses a tick
(deploy, restart, network blip) while a setup is waiting for a retest, the
setup has no other path forward and eventually ages out via
`setup_expiry_sweeper.py`, `EXPIRED`, with **no TradePlan ever built and no
error distinguishing this from an intentionally-unfilled retest**.

Everything else in the pipeline - zone/confluence identity, TradePlan V7's
atomic publish Lua, the executor's duplicate-claim handling - was already
correct and heavily tested; the bug is specifically the handoff orchestration
between "scanner confirms" and "worker publishes."

## 3. What changed

### 3a. Direct publication path (mission section 9)

`app/autotrade/worker.py` adds `try_publish_executable_signal()` and a typed
`PublishResult` (`status`, `plan_id`, `reason_code`, `zone_id`, `setup_id`,
`measured`, `executable_quote`, `quote_side`). It is a thin, honest wrapper
around the **existing, fully-tested** `_handle_event`/`_publish_trade_plan_v7`
pipeline - reused, not duplicated - called synchronously from the scanner's
own confirmation cycle (`app/analysis/scanner.py:_sync_strategy_match`)
instead of via `auto_trade:strategy_match_ready`.

Because `scanner_loop`, `auto_scalp_loop`, and `strategy_match_ready_loop` are
three `asyncio` tasks in **one process** (`app/main.py`), this is a direct
in-process function call, not a new stream/queue - "same processing cycle"
literally means the same await chain, not a different future tick.

Gated by `AUTO_TRADE_DIRECT_PUBLISH_ENABLED` (default **on**). When a
CONFIRMED, structurally-eligible match's live quote is already inside its
zone (an A-grade M5-authoritative reaction, or any match whose retest/M1
requirement is already satisfied this instant), it is evaluated and published
before the scanner cycle returns - no ready-stream write at all for that
match. When it is not yet executable, the direct attempt comes back
`remained_watching` and the scanner falls back to enqueueing the durable
ready-stream event exactly as before (still required - "waiting retest is
allowed and required for strong key levels," mission section 6).

### 3b. Ready-stream ack-timing fix (closes root cause a+b)

`_process_strategy_match_ready_entry` no longer ACKs on `ARMED_WAITING_TRIGGER`
- only on `PLAN_PUBLISHED` or a terminal state. A setup still waiting stays
pending in the consumer group; the steady-state consumer loop already calls
`xautoclaim` every iteration (`recover_pending=True`, `min_idle_time=30s`),
so an unacked entry is durably retried on its own ~30s cadence **regardless
of whether any `bars:new` pub/sub message was ever received**. This is the
direct, minimal fix for the root cause - it does not touch the (already
correct, already well-tested) M1-trigger/episode state machine.

### 3c. Per-timeframe lookback (mission section 3)

Both `scanner._load_frames` and `worker._load_frames` previously fetched the
**same flat bar count** (`settings.scanner_window`, default 500) for every
timeframe, H1 through M1. `app/analysis/ohlc_source.py:window_for_timeframe()`
is now the single place that resolves a timeframe to its configured lookback:

| Setting | Default | XAU-appropriate range |
|---|---|---|
| `XAU_LOOKBACK_H1_BARS` | 400 | 300-500 |
| `XAU_LOOKBACK_M15_BARS` | 650 | 500-800 |
| `XAU_LOOKBACK_M5_BARS` | 1000 | 800-1200 |
| `XAU_LOOKBACK_M1_BARS` | 150 | 100-200 (timing only) |

### 3d. XAU zone-width contract (mission section 4)

`app/analysis/confluence_zone.py:validate_zone_width()` enforces width in
**actual XAU price units** (never pips/digits): a merged zone below
`XAU_ZONE_MIN_WIDTH_PRICE` (default 3.0) is `zone_too_narrow`; above
`XAU_ZONE_PREFERRED_MAX_WIDTH_PRICE` (default 6.0, or
`XAU_MAJOR_ZONE_MAX_WIDTH_PRICE` = 10.0 for an H1-sourced "major" zone) is
`zone_too_wide`. Telemetry carries `raw_zone_width`, `merged_zone_width`,
`min_required_width`, `max_allowed_width`, `merge_sources`,
`rejection_reason`.

Wired into the live scanner merge path
(`scanner._merge_detection_confluence`) behind
`SCANNER_ZONE_WIDTH_GATE_ENABLED` (default **off**). The contract function
itself is fully implemented and unit-tested independent of this flag;
enabling the live gate is deliberately staged so real zone-width telemetry
can be audited (the codebase has hundreds of test fixtures with synthetic
zone widths outside the XAU range - flipping this on by default without an
audit pass would silently change unrelated test/strategy behavior).

### 3e. ZoneWatch domain model (mission section 2)

New `app/autotrade/zone_watch.py`: `ZoneWatch` dataclass with the full
suggested field shape, five states
(`discovered -> watching_retest -> evaluating -> {watching_retest,
invalidated, exhausted}`), touch-based grade decay (section 14: touch 2
downgrades A->B, touch 3+ exhausts unless `htf_evidence=True`), and
`is_actively_watchable()` (only A/B grade, non-terminal). Identity reuses
`confluence_zone.confluence_zone_id` directly - no parallel identity system.
Storage is `analysis:zone_watch:{zone_id}`, fully isolated from
`analysis:setup:*`/`auto_trade:strategy_match*` - a watched zone cannot
create a StrategyMatch, a ready-stream event, or a Telegram card by
construction (there is no code path from this module into any of those).

**Scope limitation, stated plainly**: this module is additive and fully
tested in isolation. Zone discovery itself (`zones.py`, `market_map.py`,
`detectors.py`) is **not yet rewired** to persist its output through
`ZoneWatch` as the sole source of truth - that is a larger, higher-risk
change (touching the core detection pipeline that ~80 existing test files
depend on) left for a follow-up PR. What exists today is the domain model,
state machine, identity reuse, and grade-decay contract, ready for that
wiring.

## 4. Exactly-once guarantees (unchanged, verified)

- `execution:plan_dedup:{plan_id}` tombstone + `execution:plan:{plan_id}` +
  atomic `_PUBLISH_PLAN_LUA` in `trade_plan_stream.py` - untouched.
- `analysis:setup:{setup_id}` CAS transition Lua in `setup_lifecycle.py` -
  untouched.
- C# executor's `TryClaimStringAsync` duplicate-plan-delivery reconciliation
  - untouched (no C# files were modified in this change).
- New coverage: `tests/test_strategy_match_ready_handoff.py` was updated (not
  weakened) to prove duplicate scanner confirmations still publish exactly
  one plan, and pending-recovery still works, **under the new direct-path
  architecture**.

## 5. What was NOT implemented (explicitly, per "do not claim completion for
anything not implemented and tested")

- A/B/C grading model wired into live detectors (only `ZoneWatch`'s
  touch-decay grading exists; initial grade assignment from displacement/
  confluence/room heuristics is not implemented).
- Zone discovery pipeline rewired to source from `ZoneWatch` as the sole
  watchlist (see 3e).
- Legacy ready-stream drain/migration telemetry (section 10's "record how
  many legacy events were reconciled/ignored/terminal") - the ready stream
  is still fully live and used for the waiting-case fallback, so no drain
  was needed; no additional migration counters were added.
- Range Edge Scalp same-cycle M1 publication audit (the mechanism already
  existed pre-refactor per the call-graph map; not independently
  re-verified against every one of section 17's D-group tests).
- C# changes - none were needed or made; `ctrader-engine` was confirmed
  (via `git grep`) to never reference any analysis-only state
  (`WAITING_RETEST`, `ARMED_WAITING_TRIGGER`, etc.) already, satisfying the
  ownership boundary as-is.
- `dotnet build`/`test` and the production Docker image builds could not be
  run in this sandbox (no `dotnet` SDK, no running Docker daemon). No C#
  files changed, and `docker compose config -q` validates cleanly.

## 6. Validation environment (this sandbox)

- Postgres 16 provisioned locally on port 55432 (matching
  `tests/conftest.py`'s expected `DATABASE_URL`), role/db created.
- Redis 7 (`redis-server`) started locally on port 6390 for
  `REAL_REDIS_URL`-marked tests.
- Python 3.12 venv (the repo pins `pandas-ta==0.4.71b0`, which requires
  Python >=3.12; the default `python3` in this image is 3.11).

## 7. Test commands and results (final, after all changes)

```
REAL_REDIS_URL=redis://localhost:6390/0 python -m pytest -q
  -> 1211 passed, 1 warning
python -m compileall -q app        -> exit 0
git diff --check                  -> exit 0
docker compose config -q          -> exit 0 (with a throwaway .env copied
                                      from .env.example, not committed)
dotnet build/test                 -> not runnable in this sandbox (no SDK);
                                      no C# files changed
docker build (bot, ctrader-engine) -> not runnable in this sandbox (no
                                      Docker daemon)
```

## 8. Deployment checklist

1. Deploy with `AUTO_TRADE_DIRECT_PUBLISH_ENABLED=true` (default) - the
   direct path has been validated against the full existing test suite
   (1211/1211) with zero regressions.
2. Leave `SCANNER_ZONE_WIDTH_GATE_ENABLED=false` (default) until live zone
   widths have been audited via the new `raw_zone_width`/`merged_zone_width`
   telemetry (the function is safe to call for telemetry-only observation
   before flipping the gate).
3. `XAU_LOOKBACK_*_BARS` defaults are conservative XAU-appropriate values;
   confirm the Redis `bars:{SYMBOL}:{TF}` ZSETs actually retain >=1000 M5
   bars before relying on the new M5 depth (a shallower ZSET will just
   return fewer bars, not error, but the target lookback won't be met).
4. No schema/key migration is required - `ZoneWatch` uses a brand-new,
   additive key namespace (`analysis:zone_watch:*`); nothing existing is
   read, migrated, or deleted.
5. No Redis flush, no deletion of `execution:plan*`,
   `auto_trade:position:*`, executor recovery state, or plan-dedup
   tombstones - none of those keys or their schemas were touched.

## 9. Rollback checklist

1. Set `AUTO_TRADE_DIRECT_PUBLISH_ENABLED=false` to fully restore the prior
   scanner->ready-stream->worker handoff behavior (the direct-path code is
   additive and gated; disabling the flag returns to the pre-refactor
   enqueue-only call path).
2. The ready-stream ack-timing fix (3b) is NOT flag-gated - it is a pure bug
   fix (acking strictly later than before). Reverting it would require
   reverting the specific commit; there is no operational reason to.
3. `SCANNER_ZONE_WIDTH_GATE_ENABLED` and the lookback settings are
   independently revertible via env var with no code rollback needed.
4. No data migration to reverse - `analysis:zone_watch:*` keys can be left
   in place (they expire on their own TTL) or flushed independently with
   `redis-cli --scan --pattern 'analysis:zone_watch:*' | xargs redis-cli
   del` without touching any other key namespace.
