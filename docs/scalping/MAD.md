# MAD — Asia phase clock (technique only)

Demo runs **live**. MAD is a **symbol-level** Asia phase clock.

## Owner rule (binding)

- MAD does **not** apply to HFS / scalping ranking or gates.
- The **only** live MAD soft use is **`accum` → Range Edge Scalp** (technique confluence nudge).
- `manip` / `expand` must not rank Impulse, Momentum, Range Sweep, Breakout, or reaction setups.

HFS may still refresh Redis MAD for shared telemetry; it must not score or allow/block from it.

## Redis

| Key | Meaning |
|-----|---------|
| `mad:asia_range:{SYMBOL}` | Building/sealed Asia high–low |
| `mad:phase:{SYMBOL}` | Phase payload (+ `features`, observe `would_gate`) |
| `scalp:last_mad:{SYMBOL}` | Telemetry alias (not an HFS control input) |

## Soft affinity (live)

| Phase | Live use |
|-------|----------|
| `accum` | Soft confluence +1 on **Range Edge Scalp** only |
| `manip` / `expand` / `unclear` | No soft favor |

## Phase rules

1. **manip** — bar sweeps Asia high/low and closes back inside
2. **expand** — sealed Asia box broken by ≥0.35 ATR without reclaim, or impulse ≥1.25 ATR from mid
3. **accum** — price inside Asia box, range structure:
   - sealed box: RQ 0.8–6.0 ATR (`asia_box_accum`)
   - building Asia (unsealed): RQ 0.8–24.0 ATR (`asia_building_accum`)
4. else **unclear**

## Observe-only research

- `features` / `would_gate` on Redis are research stamps — not live HFS hard blocks.
- Offline MAD-2 (`app.scalping.mad_replay`) is counterfactual research only.

## Code

- `app/analysis/mad_phase.py` — seal, classify, features, Redis; `mad_soft_bonus` = accum→Range Edge only
- Scanner attaches `DetectionContext.mad_phase` for technique detectors
- HFS ranking does **not** call `mad_soft_bonus`

## Verify on demo after deploy

```bash
redis-cli GET mad:phase:XAU | jq '.phase, .features'
# Range Edge may show mad_accum in reasons when phase=accum; HFS scores must not.
```
