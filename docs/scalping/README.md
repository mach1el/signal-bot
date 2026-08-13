# High-Frequency M1 Scalping Engine

Shadow/paper/live event-driven XAU scalping on closed M1 bars with immutable
M5 context. Separate lane from technique ZoneWatch publishers.

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
shadow / paper / live ScalpSignal
```

Live mode publishes TradePlan V8 via `worker.try_publish_executable_signal`
when gates pass. Requires `runtime.auto_trade.enabled=true` and a live
cTrader consumer on the trade-plan stream.

## Modes

| Mode | Behaviour |
|------|-----------|
| `off` | Loop exits immediately |
| `shadow` | Discover/evaluate/record only |
| `paper` | Paper TradePlan-like records, no broker |
| `live` | Publishes TradePlan V8 when gates pass |

## Archetypes

1. `range_sweep` — micro range edge false-break sweep/reclaim
2. `impulse_pullback` — join displacement after pullback
3. `breakout_retest` — accepted break + retest (evidence required for location bypass)

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
