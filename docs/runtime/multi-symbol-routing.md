# Symbol-routed multi-instrument runtime

The runtime supports symbol-routed multi-instrument execution while keeping
cTrader manifest-authoritative.

## Absolute production boundary

```text
live instruments = XAU + EURUSD + GBPJPY
feed instruments = XAU + EURUSD + GBPJPY
CTRADER_CONFIGURATION_SOURCE = manifest
CTRADER_MANIFEST_PARITY_MODE = off
```

EURUSD and GBPJPY are demo-live with their own pip/lot/zone geometry.
Do not inherit XAU dollar merge/round/FVG widths onto FX.

XAU remains required in `live_instruments`. Additional live symbols are
allowed after explicit trading-policy review.

## Instrument execution policies

Trading policy is explicit per instrument in `config/trading-bot.yml`:

- XAU uses `xau_current_v1`: the existing pip target ladder, partial exits,
  and gold stop geometry remain unchanged.
- EURUSD and GBPJPY use `fx_fixed_2r_v1` plus a **named reaction session**,
  **stop envelope**, **activation**, and **price scale**. Policy locks
  1R / 1.5R / 2R at 25/25/50; the envelope is pair-scale (EURUSD 10–18 pips,
  GBPJPY 15–30). Do not copy dotted stop or geometry paths per pair.
- A new live FX instrument declares `policy`, `reaction_session`
  (`london_ny` or `tokyo_london`, or a raw `7-11,13-16` list),
  `stop_envelope`, `activation`, and `price_scale`. Register a new session
  name in `REGISTERED_REACTION_SESSIONS` instead of duplicating hour strings.
  `overrides` remains an escape hatch for a single leaf.
- FX books 25% at 1R and 25% at 1.5R, then closes the remaining 50% at 2R.
  TP1 enables protected break-even; booking 1.5R trails the runner to 1R.
- Broker-step rules may defer an undersized partial to a later target; they
  never inflate a small close beyond its declared share.
- FX keeps the existing equity sizing and strategy-specific risk multipliers.
  The target policy adds no FX-only lot multiplier to compensate for a shorter
  exit plan.

```yaml
policy: fx_fixed_2r_v1
reaction_session: london_ny   # or tokyo_london
stop_envelope: {min_pips: 10, max_pips: 18, sl_distance: 0.0018}
activation: {require_sweep_body: true, trigger_maximum_age_bars: 3, max_spread_pips: 1}
price_scale:
  round_step: 0.001
  market_map: {change_min: 0.0001, fallback_radius_price: 0.01, scalp_radius_price: 0.006}
  zone_merge_gap_price: 0.0005
  zone_merge_max_width: 0.0015
  opposing_minimum_separation_price: 0.0015
  fvg_entry_max_width_price: 0.0015
targeting:
  mode: fixed_rr
  reward_risk: 2.0
  target_r_multiples: [1.0, 1.5, 2.0]
  close_ratios: [0.25, 0.25, 0.50]
  trail_after_r: 1.5
  trail_to_r: 1.0
  entry_clips: 2
```

The target is computed only after the entry route and protective stop are
final. If the nearest credible opposing structure cannot provide 2R of room,
the plan fails closed instead of shrinking the target. New FX instruments must
declare this policy, targeting block, `reaction_session`, `stop_envelope`,
`activation`, and `price_scale`; symbol-name hard-coding is not used.

## Account-level architecture

```text
CTraderAccountRuntimeHost / FeedRunner
├── one authenticated Open API connection
├── one account authorization state
├── one request serialization gate
├── one account reconciliation coordinator (AccountRiskCoordinator)
├── one candidate stream consumer
└── InstrumentRuntimeRegistry
    └── InstrumentRuntime (XAU, EURUSD, GBPJPY live on demo)
```

## Instrument runtime registry

Python: `app.runtime.InstrumentRuntimeRegistry` built from resolved
configuration (no ENV/YAML parse inside the registry).

C#: `InstrumentRuntimeRegistry` with alias maps and symbol-id binding.

## Manifest V2

`manifest_version = 2` adds `instrument_runtimes`.

Top-level `feed` / `auto_trade` remain as **deprecated XAU compatibility
projections**. Manifest V1 mounts upgrade to an XAU-only V2-equivalent shape;
V1 is never silently interpreted as multi-symbol.

## Rollout semantics

| Rollout | Feed | Analysis | Public Telegram | Candidates | Broker orders |
|---|---|---|---|---|---|
| disabled | no | no | no | no | no |
| feed_only | yes | no | no | no | no |
| analysis_only | yes | yes | no | no | no |
| paper | yes | yes | no | paper only | no |
| live | yes | yes | yes | yes | yes |

Paper is **not** mapped to the global dry-run flag.

## Redis isolation

Existing XAU keys and shared streams
(`auto_trade:candidates`, `auto_trade:events`, `execution:trade_plans`) are
unchanged. New symbols must use canonical instrument IDs; aliases normalize
before key construction. Unknown pip/digits fail closed (no `1.0` / 2-digit
fallback).

## Candidate dispatch

One shared candidate consumer routes by `candidate.symbol` → canonical
instrument → execution runtime. Alias forms on the stream are rejected until
normalized. Paper instruments never place broker orders.

## Reconciliation

Account positions/orders are fetched once, then partitioned by resolved
instrument. Unknown-symbol broker positions are logged as unmanaged and are
not adopted into XAU.

## Source-mode boundary

| Mode | Behaviour |
|---|---|
| `environment` | XAU-only compatibility; legacy trading ENV loaders (rollback inventory required) |
| `manifest` | Authoritative ResolvedRuntimeManifest V2; secrets + bootstrap ENV only |

PR4 flips production to `manifest` with `CTRADER_MANIFEST_PARITY_MODE=off`.
See [manifest authority cutover](../configuration/manifest-authority-cutover.md).

## Future activation procedure

1. Observe and approve manifest parity.
2. Flip production cTrader authority to manifest.
3. Onboard a second instrument as feed-only.
4. Validate analysis-only.
5. Validate paper.
6. Activate live only after explicit trading-policy review.
7. Remove duplicated shared ENV after successful cutover.
