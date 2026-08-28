# Scalp unify on M1 + M5 (drop HFS product tag)

## Intent

Centralize M1 scalp strategies on **M5 context + M1 micro**, drop the **HFS**
display tag, and keep open-plan / historical compatibility.

## What shipped

| Layer | Change |
| --- | --- |
| Display | `Range Sweep Scalp`, `Impulse Pullback Scalp`, `Breakout Retest Scalp` |
| Legacy | `HFS *` names still accepted in taxonomy, C#, protective stop, confirmation |
| Publish | `family=scalp`, `strategy_mode=scalp_m1`; `structural_source=hfs` kept for thesis compatibility |
| Context | `app.scalping.unified_context` loads shared M1/M5/M15/H1 windows for the M1 loop |
| MAD | Unchanged — MAD does not rank/gate scalping; only Range Edge may soft-favor `accum` |

## Kept (no hard delete)

- **Fade Scalp** / **Range Edge Scalp** — ZoneWatch technique path
- Legacy **`Momentum Chase Scalp`** display alias for historical fills only (strategy removed)
- Config section / env prefix `HFS_*` — operational rename later

## Overlaps (documented, not collapsed)

| Overlap | Keep both because |
| --- | --- |
| Range Sweep vs Range Edge vs Fade | Sweep is M1 reclaim of micro range; Edge/Fade are ZoneWatch edge reactions |

## Out of scope

- Merging ZoneWatch into one ScalpEngine
- Renaming Redis keys / config env prefixes
- CI allowlist / workflow edits
