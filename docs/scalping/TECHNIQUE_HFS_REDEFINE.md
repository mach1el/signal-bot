# Technique + HFS redefine (single delivery)

> Naming note: live display names are now ``* Scalp`` (see
> [`SCALP_UNIFY_M1_M5.md`](SCALP_UNIFY_M1_M5.md)). Legacy ``HFS *`` labels
> remain accepted.

One product contract for both lanes. London chart evidence informed this; the
scope is **all sessions**.

## Binding rules

1. **MAD ≠ scalp control plane.** Only `accum` soft-favors **Range Edge Scalp**.
2. **HFS owns structure + session** — Impulse continuation is L1; Range Sweep is
   reclaim-only; Breakout Retest stays.
3. **Few hard gates:** cost, chase, killzone/session, one invalidation, min RR
   after cost, **filled-only** concurrency. Armed contexts expire and never
   count as open positions.
4. **Continuation must not require sweep-reclaim** (`require_sweep_body` is for
   range/reclaim families, not Impulse/Momentum).

## What changed

| Area | Change |
|------|--------|
| HFS ranking | Removed MAD soft bonus |
| MAD soft | `accum` + Range Edge only |
| Impulse | Enabled by default; no sweep-body gate |
| Momentum | Still off by default; ignition default bars 4→2 when on |
| Lifecycle | Prune stale armed/discovered from `scalp:active` (15m) |
| Scanner MAD | Fix DataFrame `or` truthiness crash on refresh |

## Family map

| Family | Lane | Job |
|--------|------|-----|
| L1 Continuation | HFS Impulse (+ optional Momentum) | Pullback / thrust with trend |
| L2 Sweep-reclaim | HFS Range Sweep | Fade raid after reclaim |
| L3 Reaction | ZoneWatch | Key Level / S/D / Trendline / Session — FX `mad_hard_gate` (`structural_reaction`, block `expand`) |
| L3r Range Edge | ZoneWatch | Mean-revert at edge; MAD accum soft only; FX gate blocks `expand` |
| L4 Breakout retest | HFS | Accepted break + retest |

Do not add new hard MAD gates to HFS in follow-ups without a separate owner
decision.
