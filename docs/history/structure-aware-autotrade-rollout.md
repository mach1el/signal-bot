# Structure-aware autotrade — deployment & rollback

## Stage 1 — Shadow (detector only)

```bash
# Keep order placement off.
AUTO_TRADE_ENABLED=false
AUTO_TRADE_DRY_RUN=true
SCALP_BARRIER_FALLBACK_ENABLED=true
SCALP_RANGE_PROVISIONAL_ENABLED=true
SCALP_POST_IMPULSE_RANGE_ENABLED=true
```

Compare `/auto_status` scalp supports/resistances/range_state and
`auto_trade:strategy_matches:{symbol}` candidate counts vs legacy.

## Stage 2 — Dry-run execution

```bash
AUTO_TRADE_ENABLED=true
AUTO_TRADE_DRY_RUN=true
AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED=true
AUTO_TRADE_INSIDE_ZONE_MARKET_ENTRY_ENABLED=true
AUTO_TRADE_MAP_EXECUTE_TOLERANCE_PIPS=3
```

Verify executor accepts inside-zone Breakout Continuation via single-entry
fallback and that Tier B candidates publish with `risk_multiplier=0.5`.

## Stage 3 — Demo, Tier B reduced risk

```bash
AUTO_TRADE_DRY_RUN=false
AUTO_TRADE_TIER_B_RISK_MULTIPLIER=0.5
AUTO_TRADE_POST_IMPULSE_RISK_MULTIPLIER=0.5
AUTO_TRADE_ONE_SIDED_RANGE_RISK_MULTIPLIER=0.5
AUTO_TRADE_MAX_ACTIVE_POSITIONS_PER_SYMBOL=1
```

## Stage 4 — Production controlled

```bash
# Start with Tier A + zone-fill fallback only; enable Tier B after 48h.
AUTO_TRADE_TIER_B_RISK_MULTIPLIER=0.5
SCALP_RANGE_PROVISIONAL_ENABLED=false   # keep off initially
```

## Rollback (independent kill switches)

```bash
SCALP_BARRIER_FALLBACK_ENABLED=false
SCALP_RANGE_PROVISIONAL_ENABLED=false
SCALP_POST_IMPULSE_RANGE_ENABLED=false
AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED=false
AUTO_TRADE_INSIDE_ZONE_MARKET_ENTRY_ENABLED=false
AUTO_TRADE_MAP_EXECUTE_TOLERANCE_PIPS=0
AUTO_TRADE_MAP_EXECUTE_TOLERANCE_ATR=0
AUTO_TRADE_RANGE_TARGETS_PIPS=30,40,50
AUTO_TRADE_TIER_B_RISK_MULTIPLIER=0
```

Redeploy bot + ctrader-engine after env changes.
