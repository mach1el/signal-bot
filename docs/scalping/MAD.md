# MAD-0 / MAD-0+ — shared Asia phase (technique + HFS)

Demo runs **live**. MAD is a **symbol-level** phase clock shared by:

- Technique ZoneWatch / scanner detectors (Range Edge, Key Level, S/D, …)
- HFS scalping archetypes

Accumulation is treated as favorable for **range scalping** (Range Edge + HFS Range Sweep). Expansion / manipulation favor impulse-style setups (soft ranking only in this PR).

## Redis

| Key | Meaning |
|-----|---------|
| `mad:asia_range:{SYMBOL}` | Building/sealed Asia high–low |
| `mad:phase:{SYMBOL}` | Full phase payload |
| `scalp:last_mad:{SYMBOL}` | HFS alias (same payload) |
| `math_shadow.mad` | Nested under HFS math sidecar |

## Phase → soft affinity

| Phase | Prefers |
|-------|---------|
| `accum` | Range Edge Scalp, HFS Range Sweep (+ confluence / score) |
| `manip` | Reactions + range (raid/reclaim) |
| `expand` | Impulse / Momentum / breakout families |

Hard allow/block is **not** changed in MAD-0 — soft boost only so demo data can teach later gates.

## Phase rules (v0)

1. **manip** — bar sweeps Asia high/low and closes back inside
2. **expand** — sealed Asia box broken by ≥0.35 ATR without reclaim, or impulse ≥1.25 ATR from mid
3. **accum** — price inside Asia box, range quality 0.8–6.0 ATR, range structure
4. else **unclear**

## Code

- `app/analysis/mad_phase.py` — shared seal + classify + Redis
- Scanner attaches `DetectionContext.mad_phase` every exec-TF cycle
- HFS ranks with `mad_soft_bonus`

## Verify on demo after deploy

```bash
redis-cli GET mad:phase:XAU
redis-cli GET mad:asia_range:XAU
# Technique cards may show reason tag mad_accum / mad_manip / mad_expand
```
