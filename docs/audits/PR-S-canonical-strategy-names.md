# PR-S: canonical strategy names

## Precondition

Trend Pullback removal does not remove demand/supply coverage. The M5
`AnalysisContext` builds `st.zones` and `st.order_blocks` once, and exposes
those same views to `collect_technique_instances`. The enabled
`supply_demand_technique_reaction` and `order_block_technique_reaction`
publishers consume that pool and the shared structural-reaction confirmation
path. The legacy demand/supply fallback publishers remain disabled by default.

`_candidate_zones` was left unchanged. It still has two callers; changing
its mitigation behavior would alter the other two callers and is outside this
PR.

## Production label inventory

The distinct non-null `setup_type` values observed in production
`auto_trade_results` and `auto_trade_fills` were:

```text
Breakout Retest Scalp, CRT, Confluence Zone, FVG, Fade Scalp, Flip Zone,
HFS Impulse Pullback, HFS Momentum Chase, HFS Range Sweep,
Impulse Pullback Scalp, Key Level Reaction, Momentum Chase Scalp,
Range Sweep Scalp, Session Level Reaction, Supply Demand, Trend Pullback,
Trendline Reaction, Zone Reaction, breakout-retest, confluence, confulence,
demand, flip-zone, golden-fibo, iFVG, key-level, momentum, ob, supply
```

Every value resolves through `strategy_names.py`. `<NULL>` is intentionally
left null; unlabelled-fill attribution belongs to PR-L.

## Naming decisions

- Reaction strategies retain the `Reaction` suffix.
- Named techniques remain `Supply Demand`, `Order Block`, `FVG`, `iFVG`, and
  `CRT`.
- `Momentum Ride` and `Snap-Back` remain unchanged: the detector registry
  classifies them as M5 continuation/liquidity sources, not M1 scalp sources.
- `Break & Retest` now has canonical family `breakout_retest`, and `Momentum
  Ride` has canonical family `momentum_continuation`; both previously fell
  through to `unknown`.
- HFS and manual spellings are aliases. No historical backfill is performed,
  so reports can temporarily show canonical and legacy forms together.
- `Trend Pullback` and the legacy zone/report-only labels are retired but
  remain resolvable for history and in-flight management.

## C# audit

The existing C# engine has case-sensitive setup switches for target policy and
an existing scalp-name compatibility set. The lowercase `demand zone`, `supply
zone`, and `zone reaction` comparisons in `StrategyFamilyFromSetup` are live:
the method lowercases the incoming contract value before comparing, so they
are not dead variants. This PR does not add a second C# naming registry; the
engine continues to receive setup/family through the existing trade-plan
contract. The compatibility comparisons remain for historical/in-flight
plans.
