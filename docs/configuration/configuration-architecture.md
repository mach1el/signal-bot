# Configuration architecture

Catalog V2 is the active identity model: every catalog entry is identified by
its canonical path (`actionability.contested_corridor.gap_atr`). Display IDs
are derived as `config:<path>`.

## Runtime flow

1. Source bundle (schema → profile → file secrets → dotenv → process ENV → init)
2. Canonical resolver
3. `PythonRuntimeConfig`
4. `app.core.config.runtime_config`
5. Typed production consumers

There is exactly one Python configuration authority. There is no Settings
singleton, flat facade, or runtime legacy selector.

## Fingerprints

- **configuration_contract_fingerprint** — behaviorally relevant metadata used
  by startup and config-health.
- **configuration_document_fingerprint** — contract fields plus descriptions
  and evidence notes for documentation freshness.

`catalog_fingerprint()` remains as a compatibility alias of the contract
fingerprint.

## Artifacts and commands

See `configuration-governance.md`.
