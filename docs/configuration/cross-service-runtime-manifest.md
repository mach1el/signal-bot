# Cross-service resolved runtime manifest

## Motivation

Python and cTrader must share one deterministic view of effective non-secret
trading configuration. This PR introduces a file-based
`ResolvedRuntimeManifest` compiled from the canonical Python resolver and the
PR1 effective instrument context.

## Distinction from Redis health manifest

| Concept | Role |
| --- | --- |
| `ResolvedRuntimeManifest` | Immutable startup input generated before services start |
| `AutoTradeConfigManifest` | Runtime health/status snapshot published to Redis |

Redis config-health keys and comparison behaviour remain operational.

## Architecture

```text
trading-bot.yml + ENV
        │
        ▼
canonical Python resolver (ApexVoidConfig)
        │
        ▼
effective instrument contexts
        │
        ▼
runtime manifest compiler
        │
        ▼
/runtime/resolved-runtime.json
        │
        ├── Python verifies fingerprint
        └── cTrader loads and compares with ENV options
```

## Shadow-mode deployment (this PR)

PR2 does not change the live cTrader configuration authority. ENV remains
authoritative while the runtime manifest is enforced as a shadow parity source.

```text
CTRADER_CONFIGURATION_SOURCE=environment
CTRADER_MANIFEST_PARITY_MODE=enforce
```

## Manifest authority (PR4)

Production Compose now uses:

```text
CTRADER_CONFIGURATION_SOURCE=manifest
CTRADER_MANIFEST_PARITY_MODE=off
```

`off` applies only to ENV-versus-manifest parity. Manifest validation remains
enforced. See [manifest-authority-cutover.md](manifest-authority-cutover.md).

## Manifest V2 (PR3)

`manifest_version = 2` adds `instrument_runtimes` while retaining top-level
`feed` / `auto_trade` as **deprecated XAU compatibility projections**.

PR3 introduces symbol-routed runtime support but keeps XAU as the only
production live instrument and keeps cTrader ENV-authoritative. See
[multi-symbol routing](../runtime/multi-symbol-routing.md).

Manifest V1 files upgrade to an XAU-only V2-equivalent shape and are never
silently interpreted as multi-symbol.
## Bootstrap ENV

- `APEXVOID_RUNTIME_MANIFEST_FILE`
- `CTRADER_CONFIGURATION_SOURCE` (`environment` | `manifest`)
- `CTRADER_MANIFEST_PARITY_MODE` (`off` | `warn` | `enforce`)

Unknown source/parity values fail closed. Manifest source never falls back to
ENV.

## Fingerprint

SHA-256 over canonical UTF-8 JSON (`sort_keys`, invariant decimal strings,
no generated timestamps inside the fingerprinted payload).

## Secret boundary

Secrets are excluded structurally. Manifest generation fails closed if a secret
path or sentinel leaks into serialization.

## Startup order

Compose runs `config-compiler` to completion before `ctrader-engine` and `bot`.
Invalid configuration leaves trading services stopped (`restart: "no"`).

## Rollback

Set `CTRADER_CONFIGURATION_SOURCE=environment`. Do not rely on silent fallback.

## Future source-flip

A later isolated PR will:

1. observe production parity;
2. flip cTrader authority to `manifest`;
3. remove duplicated non-secret trading ENV from Ansible;
4. keep explicit ENV rollback.
