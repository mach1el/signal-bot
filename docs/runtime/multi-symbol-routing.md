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
- EURUSD and GBPJPY use `fx_fixed_2r_v1`: a structural 12–25 pip stop and one
  target at exactly 2R from the final planned entry and protective stop.
- The FX target closes 100% of the position. Break-even and trailing steps are
  disabled because no runner remains after that target.
- FX keeps the existing equity sizing and strategy-specific risk multipliers.
  The target policy adds no FX-only lot multiplier to compensate for a shorter
  exit plan.

```yaml
policy: fx_fixed_2r_v1
targeting:
  mode: fixed_rr
  reward_risk: 2.0
```

The target is computed only after the entry route and protective stop are
final. If the nearest credible opposing structure cannot provide 2R of room,
the plan fails closed instead of shrinking the target. New FX instruments must
declare this policy and targeting block; symbol-name hard-coding is not used.

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
