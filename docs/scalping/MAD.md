# MAD — Asia phase clock (FX technique + telemetry)

MAD is ApexVoid's **operational** Asia session phase clock: **accumulation →
manipulation → expansion**. It stamps every watched symbol in Redis and gates
**FX fixed_rr** technique publish/activation when
`execution.technique.mad_hard_gate_enabled: true` (default).

Conceptual lineage: [ICT Power of Three / AMD](MAD_SOURCES.md) (Asia box →
London sweep → delivery). ApexVoid thresholds and gate matrix are **owner +
prod-calibrated** — see [MAD_SOURCES.md](MAD_SOURCES.md).

## Owner rules (binding)

| Lane | MAD policy |
|------|------------|
| **HFS / M1 scalp** | No ranking or hard gates. Redis refresh for shared telemetry only. |
| **Technique soft** | `accum` → +1 confluence on **Range Edge Scalp** only (`mad_soft_bonus`). |
| **FX technique hard** | Live `mad_hard_gate` at ZoneWatch activation when enforce + enabled. |
| **Gold ladder (XAU)** | Phase telemetry only; no FX-style hard gate. |

## Phase classification

| Phase | Rule |
|-------|------|
| **manip** | Bar sweeps sealed/building Asia H/L and closes back inside |
| **expand** | Sealed box broken ≥0.35 ATR without reclaim, or impulse ≥1.25 ATR from mid |
| **accum** | Inside Asia box, range structure, RQ 0.8–6.0 ATR (building: up to 24 ATR) |
| **unclear** | Else — **neutral** for all gates |

## Live hard gate (FX `fixed_rr` only)

Applied in `zone_execution_cutover._prepare_activation()` after activation
passes, before publish.

### Reversal families — block on `expand`

Do not fade / mean-revert while price is distributing away from the Asia box.

| Setup (taxonomy) | Gate key |
|------------------|----------|
| Key Level / Session / Trendline Reaction | `structural_reaction` |
| Demand / Supply / Zone / Flip / Confluence | `structural_reaction` |
| Order Block / FVG / iFVG / CRT / S&D | `structural_reaction` |
| Liquidity Sweep / Snap-Back | `liquidity_sweep_reversal` |
| Range Edge / One-Sided / Fade / Chop | `range_edge_mean_reversion` |

Allowed phases: **`accum`**, **`manip`**, **`unclear`**.

### Continuation families — require `manip` or `expand`

Need displacement (Judas reclaim or accepted break) before continuation entry.

| Setup | Gate key |
|-------|----------|
| Impulse / Momentum Pullback | `impulse_pullback_continuation` |
| Breakout Retest | `breakout_retest` |

Blocked when phase is **`accum`** or **`unclear`** only.

## Redis

| Key | Meaning |
|-----|---------|
| `mad:asia_range:{SYMBOL}` | Building/sealed Asia high–low |
| `mad:phase:{SYMBOL}` | Phase + `features` + `would_gate` previews |
| `scalp:last_mad:{SYMBOL}` | Telemetry alias (not an HFS control input) |

## Research (observe-only on HFS)

- `app.scalping.mad_replay` — MAD-2 counterfactual expectancy by phase × session
- HFS math shadow stamps `would_gate`; HFS ranking does **not** apply MAD

## Code

- `app/analysis/mad_phase.py` — seal, classify, `mad_gate_strategy_for_setup`, gates
- `app/autotrade/strategy_taxonomy.py` — exact strategy → family registry
- Scanner attaches `DetectionContext.mad_phase` for technique detectors

## Verify on demo

```bash
redis-cli GET mad:phase:EURUSD | jq '.phase, .would_gate.structural_reaction'
```
