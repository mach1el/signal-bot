# High-Frequency M1 Scalping Engine

Shadow/paper/live event-driven scalping on closed M1 bars with immutable M5
context. Separate lane from technique ZoneWatch publishers. Symbols are
gated by `is_hfs_symbol` / HFS config (production focus remains XAU).

## Mathematical program

Research-first roadmap (do not loosen thresholds first):

1. [PHASE1_AUDIT.md](PHASE1_AUDIT.md) — pipeline + threshold inventory
2. `app/scalping/math_features.py` — ATR-normalized \(X_t\) features (PR A)
3. `app/scalping/math_strategies.py` — Liquidity Sweep / Impulse Pullback / Range Edge gates (PR C–E)
4. `app/scalping/ranking.py` — unified score after hard gates (PR F)
5. `app/scalping/replay.py` — paper outcomes + 60/20/20 calibration (PR B)
6. `app/scalping/replay_lab.py` — math-gate event lab + sweeps ([REPLAY_LAB.md](REPLAY_LAB.md))
7. `app/scalping/lab_event_builder.py` — historical M1/M5 → Liquidity Sweep LabEvents
8. `app/scalping/rollout.py` — shadow/paper/controlled-live helpers (PR G–I)
9. [CONTROLLED_LIVE.md](CONTROLLED_LIVE.md) — promotion checklist
10. [MAD.md](MAD.md) — Asia range seal + accum/manip/expand live telemetry (MAD-0)

Hard gates first; ranking second. Holdout data is never used for tuning.
Demo host: ship live and trace performance from Redis/ledger.

## Architecture

```text
H1/M15 refresh on closed bars
        ↓
M5 ScalpContextSnapshot (immutable, Redis-pinned)
        ↓
M1 microstructure + archetypes
        ↓
EntryLocation (enforce inside HFS) + activation + cost + risk
        ↓
shadow / paper / live ScalpSignal → TradePlan V8 (live mode)
        ↓
math_shadow sidecar (live/shadow/paper; observe-only, no broker)
```

Owned by `bar_event_dispatcher_loop` (not a standalone M1 subscriber). Live
mode publishes via `worker.try_publish_executable_signal` when gates pass.
Requires `runtime.auto_trade.enabled=true` and a live cTrader consumer on the
trade-plan stream. Math shadow also records on live M1 cycles
(`scalp:last_math_shadow:{SYMBOL}`) without changing allow/block.

Asia session is permitted for enabled HFS archetypes. Rollover stays empty;
Impulse/Momentum stay off unless explicitly enabled. London/NY still use the
killzone choke.

## Modes

| Mode | Behaviour |
|------|-----------|
| `off` | Loop exits immediately |
| `shadow` | Discover/evaluate/record only + math sidecar |
| `paper` | Paper TradePlan-like records, no broker |
| `live` | Publishes TradePlan V8 when gates pass + math sidecar observe-only |

## Archetypes

1. `range_sweep` — micro range edge false-break sweep/reclaim (Asia + London/NY)
2. `impulse_pullback` — join displacement after pullback (**London/NY killzones only**)
3. `breakout_retest` — accepted break + retest (Asia + London/NY)
4. `momentum_chase` — ignition chase (**London/NY killzones only**; off in Asia)

## Promotion criteria (shadow → paper)

- Stable opportunity density (~15–30/day observation, not a quota)
- Buy-top / sell-bottom block rates reviewed
- Net expectancy and drawdown acceptable on holdout replay
- Spread/cost blocks behaving as expected
- No publication into live candidate streams while still shadow

## Replay

```bash
cd algo-bot
PYTHONPATH=. python -m app.scalping.replay --fixture path.jsonl --output artifacts/scalp-replay.json
```

Report includes `aggregate` plus `calibration` (development / validation / holdout).
