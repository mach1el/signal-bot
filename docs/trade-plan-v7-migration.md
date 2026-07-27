# TradePlan V7 migration and operations

See `docs/adr-trade-plan-v7-boundary.md` for the architectural rationale.
This doc covers rollout mechanics, rollback, and current status.

## Status (as of `refactor/trade-plan-v7-analysis-execution-boundary`)

Done and merged-to-branch:

- TradePlan V7 contract (Python + C# models, shared fixture, parity tests).
- `AUTO_TRADE_CONTRACT_MODE` migration gate, wired into the config-health
  handshake as a fatal field.
- `execution:trade_plans` stream and `execution:plan:{plan_id}` /
  `execution:plan_state:{plan_id}` keys (publish/read only).
- Python setup lifecycle state machine (`analysis:setup:{setup_id}`).
- Python `TradePlan` builder from an already-confirmed `StrategyMatch`.
- C# `TradePlanExecutionEngine`: pure decision logic (entry-trigger
  evaluation, mechanical volume/target sizing, break-even calculation) with
  a dependency-boundary test suite.
- A live V6 bugfix: Market Map zone selection now uses the uncapped
  structural pool instead of the Telegram-display-capped list.
- A live V6 Telegram-label fix: "Algo bot READY" no longer appears for a
  merely-published candidate.

Not yet done (tracked as follow-up, see "Next work" below):

- Nothing calls `TradePlan` builder from the live `worker.py` publish path -
  V7 plans are not currently published in production under any mode.
- `TradePlanExecutionEngine` is not wired into `AutoTradeEngine.RunSessionAsync`
  - it has no broker I/O (no order submission, fill reconciliation, or
    restart recovery for V7 plans yet).
- `shadow_v7` mode has no concrete divergence-recording implementation yet;
  only the mode name itself is validated/accepted by both services.
- The Market Map `display_entries`/`strategy_zones` naming split described
  in the ADR is implemented functionally (`entries`/`actionable_entries`
  already existed and are now used correctly) but not renamed to the ADR's
  suggested field names - renaming is a larger, more disruptive change
  deferred to avoid mixing a rename with a behavior fix in one commit.

## Migration modes

Set identically on both services via `AUTO_TRADE_CONTRACT_MODE`:

| Mode | Python behavior | C# behavior |
|---|---|---|
| `legacy_v6` (default) | Publishes only V6 `TradeCandidate`s | Consumes only V6, unchanged |
| `shadow_v7` | Also publishes V7 plans | Parses/validates V7, places no orders from it |
| `v7_primary` | V7 is primary | V7 places orders; V6 remains a fallback |
| `v7_only` | V6 publishing stops | V6 candidates rejected outright |

Do not set this independently on the two services - `AutoTradeConfigHealth`
treats a `contract_mode` mismatch as fatal and disables auto-trade
entirely, by design (see the ADR's "fail closed on mismatch" requirement).

## Rollback

Because every mode above `legacy_v6` requires an explicit, coordinated env
change on both services, rollback is: set `AUTO_TRADE_CONTRACT_MODE=legacy_v6`
on both services and redeploy. No data migration is required - V6 candidates,
open V6 positions, and V6 Redis keys are untouched by anything in this
branch. `shadow_v7` and later modes are additive; they do not remove or
rewrite `auto_trade:*` state.

## Legacy compatibility

- V6 `TradeCandidate` and its `Planned*` field family are unchanged.
- `execution:*` keys are a new, separate namespace - nothing in V6 reads or
  writes them, so V7 work cannot corrupt V6 state by construction.
- Existing open V6 positions remain manageable under every mode above;
  nothing in this branch touches `AutoTradePositionState` or existing
  position-management code paths.

## Next work

In priority order:

1. Wire `TradePlanExecutionEngine`'s decision methods into
   `AutoTradeEngine.RunSessionAsync` behind `shadow_v7`/`v7_primary`: actual
   `ICTraderTradeClient` order submission, fill reconciliation, and restart
   recovery for V7 plans. This is the largest remaining piece and should
   land as its own reviewable change given the broker-order blast radius.
2. Wire the Python `TradePlan` builder into `worker.py`'s publish path
   behind `shadow_v7`, and implement shadow-mode divergence recording
   (compare V6 candidate vs. V7 plan outcomes without V7 placing orders).
3. `v7_primary` demo canary on a small allow-listed strategy set (Trend
   Pullback, Break & Retest) per the ADR's phase 5.
4. Only after 1-3 are stable: `feat/analysis-h1-structure-map` (H1 order
   blocks, supply/demand, support/resistance, liquidity, multi-timeframe
   scoring) - deliberately sequenced after the ownership boundary, not
   before it.

No live trading, VPS deployment, or automatic merge is authorized by this
branch or this document.
