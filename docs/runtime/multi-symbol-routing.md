# Symbol-routed multi-instrument runtime

PR3 introduces **symbol-routed multi-instrument runtime** support while keeping
**XAU as the only production live instrument** and keeping **cTrader
ENV-authoritative**.

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
