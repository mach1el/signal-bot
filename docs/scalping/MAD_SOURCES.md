# MAD — conceptual sources vs ApexVoid implementation

MAD in this repo is **not** a verbatim copy of any single published model. It is
an **operational clock** inspired by session AMD theory, implemented with
measurable rules and validated on ApexVoid demo/prod tape.

## Conceptual reference (not proof)

| Idea | External framing | ApexVoid use |
|------|------------------|--------------|
| Asia range | ICT Asian session accumulation / BSL–SSL pools | `AsiaRangeSeal` — M5 H/L over Asia window |
| London sweep | ICT Judas Swing / manipulation at London open | `manip` — sweep Asia edge + reclaim close |
| Delivery | ICT distribution / NY expansion | `expand` — accepted break or impulse from mid |
| Phase × setup | AMD PO3 session cycle | `mad_hard_gate` × strategy family |

Useful reading (discretionary methodology, not peer-reviewed):

- [ICT Power of 3](https://www.theinnercircletraders.com/ict-power-of-3/)
- [ICT Asian session](https://www.theinnercircletraders.com/ict-asian-session/)
- [ICT trading sessions (hour-by-hour)](https://liquidityscan.io/blog/ict-trading-sessions-explained-the-algorithmic-day-hour-by-hour)

## ApexVoid-specific choices (binding in code)

These are **engineering calibrations**, not copied from ICT docs:

| Parameter | Value | Where |
|-----------|-------|-------|
| Expand break | ≥0.35 ATR beyond sealed Asia edge | `classify_mad_phase` |
| Expand impulse | ≥1.25 ATR from Asia mid | `classify_mad_phase` |
| Accum RQ (sealed) | 0.8–6.0 ATR width | `_RQ_ACCUM_*` |
| Accum RQ (building) | 0.8–24.0 ATR | `_RQ_BUILDING_ACCUM_MAX` |
| Reversal block | `expand` only | `mad_hard_gate` |
| Continuation require | `manip` or `expand` | `mad_hard_gate` |
| HFS exempt | No MAD rank/gate | Owner 2026-08-26 |

## Gate matrix rationale

Aligned with AMD **intent**, not ICT entry mechanics (FVG, OB, etc.):

1. **Structural reaction** (Key Level, S/D, OB, FVG, Zone) — reversal at HTF
   pools. Block during **expand** (distribution); allow **manip** (post-sweep
   reclaim is the classic London fade window) and **accum** (inside-box levels).

2. **Range / liquidity sweep** — same reversal rule: block **expand**.

3. **Impulse / breakout retest** — continuation after displacement. Require
   **manip** or **expand**; block flat **accum** (no raid yet).

4. **`unclear`** — neutral everywhere. Missing Asia seal must not sterilize
   technique lane.

## Validation path (trusted for *this* stack)

| Stage | What it proves |
|-------|----------------|
| MAD-0 | Phase stamps match tape (Asia seal, sweep, break) |
| MAD-1 | `would_gate` previews on Redis |
| MAD-2 | Counterfactual expectancy on stamped lab events |
| Live FX gate | Block/allow at activation — monitor `auto_trade:entry_activation:*:mad_gate_*` |

Do **not** promote new hard gates from ICT blogs alone. Extend only when MAD-2
or live FX funnel shows improved expectancy on holdout.

## Related docs

- [MAD.md](MAD.md) — live rules and Redis keys
- [TECHNIQUE_HFS_REDEFINE.md](TECHNIQUE_HFS_REDEFINE.md) — family map (L3 Reaction vs L3r Range Edge)
