# MAD — Asia phase clock (structure analysis + entry quality)

MAD is ApexVoid's **operational** Asia session phase clock: **accumulation →
manipulation → expansion**. It stamps every watched symbol in Redis and informs
**technique entry quality / structure analysis** via soft confluence and reason
tags. It must **not** block trade-plan publish or ZoneWatch activation.

Conceptual lineage: [ICT Power of Three / AMD](MAD_SOURCES.md) (Asia box →
London sweep → delivery). ApexVoid thresholds are **owner + prod-calibrated** —
see [MAD_SOURCES.md](MAD_SOURCES.md).

## Owner rules (binding)

| Lane | MAD policy |
|------|------------|
| **M1 scalping** | No ranking or hard gates. Redis refresh for shared telemetry only. |
| **Technique soft** | Entry quality: `accum` → Range Edge (+1 confluence); `manip` → structural reaction / liquidity (+1). Stamp `mad_{phase}` on detections when phase is clear. |
| **Hard gate** | **Off.** `mad_hard_gate_enabled` defaults **false**; activation path does not call the gate. `mad_hard_gate` / `would_gate` are research / replay only. |
| **Gold ladder (XAU)** | Same soft stamps/telemetry; no activation veto. |

## Phase classification

| Phase | Rule |
|-------|------|
| **manip** | Bar sweeps sealed/building Asia H/L and closes back inside |
| **expand** | Sealed box broken ≥0.35 ATR without reclaim, or impulse ≥1.25 ATR from mid |
| **accum** | Inside Asia box, range structure, RQ 0.8–6.0 ATR (building: up to 24 ATR) |
| **unclear** | Else — **neutral** for soft favor |

## Soft entry quality (technique detectors)

Applied in `detectors._finish` via `mad_soft_bonus` — **nudge only**, never a
plan veto:

| Phase | Soft favor |
|-------|------------|
| **accum** | Range Edge / range_scalp families |
| **manip** | Reaction / liquidity families (post-sweep reclaim window) |
| **expand** | No soft favor (continuation not MAD-ranked) |

Detections also stamp `mad_{phase}` in reasons when phase ≠ `unclear` for
structure visibility.

## Research matrix (observe-only)

`mad_hard_gate` / Redis `would_gate` keep a phase × strategy affinity matrix for
`mad_replay` and shadow stamps. Do **not** wire it back into activation.

### Reversal families — prefer avoid `expand` (research)

| Setup (taxonomy) | Gate key |
|------------------|----------|
| Key Level / Session / Trendline Reaction | `structural_reaction` |
| Demand / Supply / Zone / Flip / Confluence | `structural_reaction` |
| Order Block / FVG / iFVG / CRT / S&D | `structural_reaction` |
| Liquidity Sweep / Snap-Back | `liquidity_sweep_reversal` |
| Range Edge / One-Sided / Fade / Chop | `range_edge_mean_reversion` |

### Continuation families — prefer `manip` or `expand` (research)

| Setup | Gate key |
|-------|----------|
| Impulse / Momentum Pullback | `impulse_pullback_continuation` |
| Breakout Retest | `breakout_retest` |

## Redis

| Key | Meaning |
|-----|---------|
| `mad:asia_range:{SYMBOL}` | Building/sealed Asia high–low |
| `mad:phase:{SYMBOL}` | Phase + `features` + `would_gate` previews |
| `scalp:last_mad:{SYMBOL}` | Telemetry alias (not a scalping control input) |

## Research tools

- `app.scalping.mad_replay` — counterfactual expectancy by phase × session
- Math shadow stamps `would_gate`; ranking / activation do **not** apply MAD as a veto

## Code

- `app/analysis/mad_phase.py` — seal, classify, soft bonus, research gate helpers
- `app/autotrade/strategy_taxonomy.py` — exact strategy → family registry
- Scanner attaches `DetectionContext.mad_phase` for technique detectors

## Verify on demo

```bash
redis-cli GET mad:phase:EURUSD | jq '.phase, .would_gate.structural_reaction'
```
