# Autonomous execution integrity

The autonomous route is a staged ownership pipeline:

```text
closed M1 bar
  -> detector output
  -> typed ExecutionIntent
  -> side-effect-free execution preflight
  -> executable-intent arbitration
  -> atomic Redis ownership + stream publication
  -> C# execution-contract validation
  -> broker order
  -> route and position lifecycle evidence
```

Scanner StrategyMatch, mapped structural reactions, native Private Range, and
native Private Trend enter the same preflight/arbitration boundary. Private
detectors keep their own geometry and eligibility rules; normalization does
not turn them into scanner signals.

## Detection and preflight

Every intent carries stable symbol, cycle, strategy/family, direction,
entry-zone, structural, match/reaction/thesis, group, target, and freshness
identity. Preflight evaluates the execution policy and technical guards before
the arbiter sees the intent.

Preflight may read Redis and current market state, but it does not:

- acquire candidate, reaction, thesis, range-side, cycle, or C# ownership;
- append to the executor stream;
- consume a StrategyMatch;
- create a group or broker order.

Only executable decisions enter arbitration. A non-executable high-ranked
intent therefore cannot suppress a valid lower-ranked opposite intent. Equal
strength executable BUY and SELL sets publish neither side. Same-direction
intents are attempted in rank order, so a final atomic race loss may fall
through to the next executable intent.

One closed M1 cycle can atomically own at most one autonomous initial
candidate. An explicit compatible Private Trend parent-group add is not a new
initial and retains the existing group sizing contract.

## Execution policy contract

### Risk multiplier

Autonomous initial candidates carry `risk_multiplier`. C# requires a finite
value greater than zero and no greater than `1.0`, then sizes with:

```text
effective risk percent = configured risk percent * risk_multiplier
```

The multiplier is applied once across market, single-limit, and zone-split
initial paths. It is persisted in machine-readable group/position/lifecycle
evidence. Parent-group scale-ins inherit an already-sized group and do not
multiply risk again. Owner-entered manual `/algo` orders remain exempt from
autonomous tier-risk modification.

### Order type and entry distribution

These are independent fields:

| Order type | Entry distribution | Result |
| --- | --- | --- |
| `market` | `single` | one market order |
| `limit` | `single` | one pending limit |
| `limit` | `zone_split` | qualified ZoneFillPlanner split |
| `either` | `either` | deterministic geometry/capability selection |

A required limit is never silently converted to market. A required zone split
fails explicitly when broker/config/geometry capability is unavailable. Legacy
autonomous payloads without `entry_distribution` retain the historical
adaptive `either` behavior; current Python candidates emit the typed field.

### Target models

- `fill_relative`: `targets_pips` are converted from the real broker fill.
  Detection price does not invent an absolute TP.
- `absolute`: `absolute_target_price` is the structural target and must remain
  on the profitable side of the planned entry.
- `hybrid`: the fill-relative ladder is capped by
  `absolute_target_price`, so no broker TP extends beyond structure.

Preflight measures structural room from current/planned entry as appropriate.
The executor builds final fill-relative prices only after the broker returns
the actual fill. Manual `/algo` keeps the owner's exact absolute TP prices
through pending-order fill and reconciliation.

## Atomic Redis ownership

One Lua transaction checks and writes:

- `auto_trade:candidate:{candidate_id}`;
- reaction ownership when supplied;
- thesis ownership when supplied;
- `auto_trade:cycle_owner:{symbol}:{cycle_id}` when this is an initial;
- executor stream event and its event-ID key.

Typed results are `published`, `duplicate_candidate`, `duplicate_reaction`,
`duplicate_thesis`, or `conflict`. Redis script/XADD failure rolls the entire
transaction back, so no pre-stream reaction or thesis claim survives. The
non-atomic compatibility path is explicitly test-only and rollback-safe.
Production EVAL failure publishes nothing and sets:

```text
auto_trade:publication_readiness
  ready=false
  reason_code=atomic_publish_unavailable
```

## Range episode identity

Private range IDs identify an auction episode, not only price buckets:

```text
v2|symbol|formation_start|formation_end|lower_bucket|upper_bucket
```

Small compatible movement during one live auction retains its ID through
`continue_range_episode`. A new formation window at the same rails receives a
new ID, so retired ownership from an old auction cannot suppress it. Scanner
and private source contexts remain independent; resolution may expose a
canonical episode without rewriting private detector geometry.

Range Box and Range Edge remain hard-gated by a live range/chop context.
Private Range uses its native box. Range Edge uses the current scanner range.
Side-state output reads the real lifecycle state and ownership IDs rather than
reporting both rails as permanently armed.

## Route evidence

Every considered intent records its exact stage and identity:

- `preflight_reason_code`;
- `arbitration_reason_code`;
- `publication_reason_code`;
- `terminal_reason_code`;
- `current_stage`;
- candidate and executor event IDs;
- group and winner intent IDs;
- source strategy, signal source, family, direction, timestamps, and retained
  state.

Final-claim failures retain the publisher's exact duplicate/conflict/readiness
reason. Arbitration does not overwrite that evidence with a generic winner or
publication-failed reason. Status attribution comes from the candidate that
actually published, including a secondary or private winner.

The executor continues the lifecycle with `executor_received`,
`executor_rejected`, `order_submitted`, and `order_filled`. Existing Telegram
suppression keeps pre-fill and position-managing noise out of operator cards;
machine-readable Redis evidence remains complete.

## Preserved boundaries

- Market Map execution and Market Map guarding remain independent:
  `AUTO_TRADE_MAPPED_ZONE_ENABLED` versus
  `AUTO_TRADE_MARKET_MAP_GUARD_ENABLED`.
- With both disabled, Market Map is execution-neutral.
- Observe-mode outcomes are advisory except explicit technical invalidity.
- Range Edge remains `Range Edge Scalp` at the executor boundary.
- Range Box keeps its existing scale-out and flip-contract behavior.
- Manual `/algo` and `/auto_close_all` bypass autonomous arbitration and
  tier-risk rules.
- Telegram contains no monetary PnL, noisy remaining-lot, or managing cards.
- Position closes after booked TPs report the highest target reached (e.g.
  TP2 = 60 pips), not a volume-weighted blend diluted by a later BE residual.
  Broker-fill weighted net remains the fallback when no target was booked yet.

## Operator Redis evidence

For `XAU`, inspect:

```text
auto_trade:publication_readiness
auto_trade:cycle_owner:XAU:{closed_m1_cycle}
auto_trade:candidate:{candidate_id}
auto_trade:candidate_stream_event:{candidate_id}
auto_trade:last_gate:XAU
auto_trade:last_route_outcome:XAU
auto_trade:route_outcome:XAU:{match_or_structural_id}
auto_trade:route_history:XAU
auto_trade:metrics:XAU
auto_trade:range_context:scanner:XAU
auto_trade:range_context:private:XAU
auto_trade:range_context:XAU
auto_trade:range_side:XAU:{episode_id}:{BUY|SELL}
scanner:last_tick:XAU:M5
```

No new environment variable is required by this integrity pass. The companion
Ansible `AUTO_TRADE_MARKET_MAP_GUARD_ENABLED` contract remains sufficient.
