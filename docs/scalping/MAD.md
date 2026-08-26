# MAD — shared Asia phase (technique + HFS)

Demo runs **live**. MAD is a **symbol-level** phase clock shared by:

- Technique ZoneWatch / scanner detectors (Range Edge, Key Level, S/D, …)
- HFS scalping archetypes

Accumulation is treated as favorable for **range scalping** (Range Edge + HFS Range Sweep). Expansion / manipulation favor impulse-style setups (soft ranking in MAD-0; shadow gates in MAD-1).

## Redis

| Key | Meaning |
|-----|---------|
| `mad:asia_range:{SYMBOL}` | Building/sealed Asia high–low |
| `mad:phase:{SYMBOL}` | Full phase payload (+ MAD-1 `features`, `would_gate`) |
| `scalp:last_mad:{SYMBOL}` | HFS alias (same payload) |
| `scalp:last_math_shadow:{SYMBOL}.mad` | Nested under HFS math sidecar |
| `math_shadow.buy/sell.measured.mad_gates` | Per-strategy gate preview (MAD-1) |

## Phase → soft affinity (MAD-0)

| Phase | Prefers |
|-------|---------|
| `accum` | Range Edge Scalp, HFS Range Sweep (+ confluence / score) |
| `manip` | Reactions + range (raid/reclaim) |
| `expand` | Impulse / Momentum / breakout families |

Hard allow/block is **not** changed in MAD-0/1 — soft boost + observe-only ``would_gate`` stamps.

## Phase rules

1. **manip** — bar sweeps Asia high/low and closes back inside
2. **expand** — sealed Asia box broken by ≥0.35 ATR without reclaim, or impulse ≥1.25 ATR from mid
3. **accum** — price inside Asia box, range structure:
   - sealed box: RQ 0.8–6.0 ATR (`asia_box_accum`)
   - building Asia (unsealed): RQ 0.8–24.0 ATR (`asia_building_accum`)
4. else **unclear**

## MAD-1 shadow gates (observe-only)

Continuous scores in ``mad.features``: ``accum``, ``manip``, ``expand`` (0–1).

``mad.would_gate`` previews MAD-4 rules without blocking publish:

| Strategy family | Would block when |
|-----------------|------------------|
| Impulse / Momentum | phase ∉ {manip, expand} |
| Range sweep / edge / liquidity sweep | phase = expand |

## Code

- `app/analysis/mad_phase.py` — seal, classify, features, ``mad_hard_gate``, Redis
- Scanner attaches `DetectionContext.mad_phase` every exec-TF cycle
- HFS ranks with `mad_soft_bonus`; math shadow stamps ``mad_gates`` per strategy

## Verify on demo after deploy

```bash
redis-cli GET mad:phase:XAU | jq '.phase, .features, .would_gate'
redis-cli GET scalp:last_math_shadow:XAU | jq '.mad.features, .buy.measured.mad_gates'
```
