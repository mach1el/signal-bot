# MAD-0 — Asia range seal + phase telemetry (live)

Demo host runs HFS in **live**. MAD-0 stamps phase state every M1 cycle
without changing allow/block. Use Redis + ledger to measure expectancy, then
tighten gates in MAD-1+.

## What ships

| Artifact | Redis / field | Meaning |
|----------|---------------|---------|
| Asia box | `scalp:asia_range:{SYMBOL}` | Building/sealed high–low for the Asia day |
| Phase | `scalp:last_mad:{SYMBOL}` | `accum` / `manip` / `expand` / `unclear` |
| Sidecar | `scalp:last_math_shadow:{SYMBOL}.mad` | Same payload nested under math shadow |

## Phase rules (v0)

1. **manip** — bar sweeps sealed (or building) Asia high/low and closes back inside
2. **expand** — sealed Asia box broken by ≥0.35 ATR without reclaim, or impulse ≥1.25 ATR from mid
3. **accum** — price inside Asia box, range quality 0.8–6.0 ATR, M5 structure range
4. else **unclear**

Asia day key wraps midnight (UTC 22 → London 07). Box seals when session leaves `asia`.

## Code

- `app/scalping/mad_phase.py` — pure seal + classify
- `app/scalping/runtime.py` — live M1 stamp (also shadow/paper)

## Not in MAD-0

- No new detector family
- No hard gate on Impulse / Range Sweep from phase yet
- No threshold loosening

## Verify on demo after deploy

```bash
redis-cli GET scalp:asia_range:XAU
redis-cli GET scalp:last_mad:XAU
redis-cli GET scalp:last_math_shadow:XAU   # check .mad
```
