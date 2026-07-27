# ADR: TradePlan V7 — separate Python planning from C# execution

## Status

Proposed. Phase 1 of a multi-phase migration (see "Migration phases" below).
No broker execution behavior changes in this phase.

## Context

`telegram-bot` (Python) and `ctrader-engine` (C#) both currently make
trade-planning decisions, then cross-validate their independently derived
outputs. This produces contradictory contracts and rejects trades whose
underlying setup was sound. Confirmed by direct code inspection on this
branch:

**Execution route is resolved twice.** Python's
`app/autotrade/execution_route.py:resolve_execution_route_plan` computes
market vs. single-limit vs. zone-split from the candidate's zone/ATR/quote
inputs. C#'s `AutoTradeEngine.cs:1711 ResolveExecutionRoute` recomputes the
same decision independently from the same raw fields, then checks it against
Python's precomputed `PlannedExecutionRoute` (`AutoTradeEngine.cs:1867-2064`).
Drift between the two rejects the candidate.

**The protective stop is computed up to three times and cross-checked.**
Python's `app/autotrade/protective_stop.py:plan_protective_stop` produces a
`FinalProtectiveStopPlan` whose own docstring says it must "mirror
`StructureStopPlanner.PlanFinal` operation-for-operation" — an explicit,
intentional duplication of C#'s `StructureStopPlanner.cs`. C# then
recomputes the stop a second time from candidate-embedded inputs and a
third time from live executor inputs (`RecomputeStructureStopPlan`,
`AutoTradeEngine.cs` ~3892-3924), comparing every recomputation against
Python's declared plan via `StructuralStopIdentityMatches` and
`PlansMatchWithinTolerance`. Any disagreement throws
`VolumePlanningException("final_protective_stop_contract_mismatch")`
(`AutoTradeEngine.cs:3841/3856/3892/3924`). Three independent
implementations of the same math are required to agree bit-for-bit within
tolerance before a trade — that this ever passes is closer to luck than
guarantee.

**The Market Map's execution zone silently widens around the current
price.** `app/autotrade/map_strategy.py:632-633`, inside
`_select_reaction_detailed`:

```python
entry_low = float(min(entry.lo - tolerance, price))
entry_high = float(max(entry.hi + tolerance, price))
```

The comment directly above it acknowledges this is deliberate: waiting for
an M1 rejection means price has already left the raw HTF zone by the time
the reaction confirms, so the code folds the current price into the
"structural" entry band to keep the trade executable. The effect is that
the executable entry zone is not a fixed Python output — it is redefined at
selection time using whatever the quote happens to be. This is the
zone-widening anti-pattern described in the originating task; it lives in
`map_strategy.py`, not `market_map.py` as originally assumed — `market_map.py`
itself already separates `MarketMap.entries` (display-capped) from
`MarketMap.actionable_entries` (uncapped), but `_select_reaction_detailed`'s
zone-selection loop (`map_strategy.py:468-469`) iterates `market_map.entries`
— the display-capped list — not `actionable_entries`. Telegram display
capping can therefore change which zones are structurally reachable for
execution.

**Root cause.** All three problems share one shape: Python and C# each hold
a partial, independently-derived opinion about the same decision, and the
system tries to reconcile them post hoc instead of having one owner. There
is no case where the reconciliation step catches a real error that a single
owner wouldn't have avoided by construction — it only catches the two sides
disagreeing with each other, which happens precisely because both sides are
allowed to decide.

## Decision

Python owns all market intelligence and complete trade planning: structure,
regime, strategy selection, direction, entry zone, the exact execution
instruction (market/single-limit/limit-ladder with concrete legs), the
absolute initial stop, absolute target prices, setup expiry, and quality
score. C# owns only broker mechanics: consume a complete, versioned
`TradePlan`, arm the exact declared entry instruction, submit exactly what
was declared, capture broker-confirmed fills, size volume mechanically from
the approved risk contract, apply the exact declared stop, manage the exact
declared targets, manage break-even from the broker-confirmed fill price,
and enforce broker-side execution safety (margin, spread, freeze level,
minimum stop distance, duplicate ownership).

C# may reject or defer a `TradePlan` only for execution-safety reasons —
never because it disagrees with Python's analysis. C# must not classify
regime, select or change strategy, resolve a different execution route,
choose an entry price within a zone, build limit legs, resize an entry
zone, detect structure, calculate a structural stop, or compare a
Python-derived value against a C#-derived recomputation of the same thing.
`StructureStopPlanner`, `ResolveExecutionRoute`,
`StructuralStopIdentityMatches`, and `PlansMatchWithinTolerance` are not
called from the new path at all — there is nothing left for them to
recompute or compare against.

This is a new contract (`TradePlan` V7) rather than another field bolted
onto the existing `TradeCandidate` (contract version 6). V6's `Planned*`
fields (`PlannedStopEntryPrice`, `PlannedStopDistance`, `PlannedStopPips`,
`PlannedStopRawPrice`, `PlannedStopClamped`, `PlannedBaseStopPrice`,
`PlannedBaseStopPips`, `PlannedFinalStopPrice`, `PlannedFinalStopDistance`,
`PlannedFinalStopPips`, `StopAdjustment*`, `PlannedExecutionRoute`,
`PlannedEntryPrice`, `PlannedLegEntryPrices` — full list at `Models.cs:207-245`)
exist specifically to carry two sides' worth of ambiguous, partially
redundant stop/route data. V7 carries one absolute stop price and one
concrete entry instruction, with no "planned vs. final vs. base vs.
recomputed" family of fields, because there is only one computation left.

The Market Map becomes a Python-only canonical structure snapshot with an
explicit split: `display_entries` (Telegram-facing, capped, may include
round-number display fallbacks, never used for execution) and
`strategy_zones` (the complete structurally executable set, stable IDs,
full provenance — the only thing a `TradePlan` may be built from). The
current quote is never folded into a zone's boundaries; a confirmation
close outside the structural zone produces a separate, explicitly named
`execution_zone`, not a widened `structural_zone`.

## Migration phases

1. **Contracts** (this change): ADR, `TradePlan` V7 schema, Python/C#
   models, shared fixtures, config modes, `execution:trade_plans` stream.
   No broker execution from V7.
2. **Python planning boundary**: V7 `TradePlan` builder from already-confirmed
   strategies; Market Map display/strategy split; setup lifecycle state
   machine.
3. **C# pure V7 executor**: parse/arm/execute V7 plans only; dependency
   tests proving no analysis/stop/route recomputation is reachable from
   this path.
4. **Shadow mode**: publish V7 alongside V6; C# validates but places no
   orders from V7; divergence recorded, not acted on.
5. **V7 primary, demo canary**: a small allow-listed set of strategies
   trades live on demo via V7; legacy V6 remains the fallback path. No
   production deployment in this phase.
6. **Legacy removal preparation**: deprecate V6 fields; do not delete V6
   code until parity is demonstrated over a real sample.

## Consequences

- A `final_protective_stop_contract_mismatch`-shaped rejection becomes
  structurally impossible on the V7 path, because there is nothing to
  mismatch against — not because the tolerance was loosened.
- C# gets simpler over the migration (three stop implementations collapse
  to zero in C#), not more complex; the mechanical-execution surface it
  keeps (sizing, fill handling, BE, target closes) is unchanged in kind.
- V6 candidates and open V6 positions must keep working unmodified through
  every phase above; nothing in this ADR authorizes closing or reinterpreting
  an existing V6 position as V7.
- This ADR does not authorize live trading, VPS deployment, new strategies,
  or H1 analysis work. Those are out of scope for this migration and are
  tracked separately (see `docs/` for the follow-up H1 structure-map work).
