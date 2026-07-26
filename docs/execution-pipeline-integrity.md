# Autonomous execution integrity

The autonomous path follows one ownership chain:

```text
closed-bar detectors
  -> StrategyMatch / native Private Range / native Private Trend
  -> ExecutionIntent arbiter
  -> strategy policy and structural guards
  -> atomic Redis candidate publication
  -> cTrader initial-group ownership checks
  -> broker order
```

## Invariants

1. Private Range geometry comes only from the native M1 auction detector.
   Scanner or resolved range context may confirm, veto, or retire it, but may
   not create a private box.
2. Scanner and private observations own separate Redis source keys. Resolution
   happens after both final observations for the cycle and is persisted once.
3. A range ID identifies one formation episode. Small compatible rail changes
   retain the ID; a materially different auction creates a new ID.
4. Range Box and Range Edge require a live range and `chop`. `warming_up`,
   `data_gap`, and invalid ATR are not tradable range regimes.
5. `observe` guards are advisory. Only an explicit hard eligibility or geometry
   failure blocks publication in that mode.
6. Market Map execution and Market Map guarding are independent:

   ```text
   AUTO_TRADE_MAPPED_ZONE_ENABLED
   AUTO_TRADE_MARKET_MAP_GUARD_ENABLED
   ```

   When the guard variable is omitted, it follows the execution variable.
7. The arbiter may publish at most one initial candidate per closed M1 cycle.
   Equal-strength opposite intents publish none. Lower-ranked intents remain
   available for a later confirmation.
8. Candidate claim and Redis stream append are one Lua transaction. The cTrader
   executor then rejects duplicate reaction, thesis, group, pending ownership,
   or opposite autonomous exposure before broker submission.
9. Range Edge remains a `Range Edge Scalp` StrategyMatch. It is not rewritten
   as Private `Range Box Scalp` at the executor boundary.
10. Route status names the exact published candidate and strategy. History is
    appended only for a state transition or material measurement change, and
    funnel counters are unique per match and status.

## Operator evidence

For symbol `XAU`, inspect:

```text
auto_trade:last_gate:XAU
auto_trade:last_route_outcome:XAU
auto_trade:route_history:XAU
auto_trade:metrics:XAU
auto_trade:range_context:scanner:XAU
auto_trade:range_context:private:XAU
auto_trade:range_context:XAU
scanner:last_tick:XAU:M5
```

The Python and C# config manifests must agree on
`market_map_guard_enabled`. A mismatch is surfaced by the existing config
health contract before autonomous execution is considered ready.
