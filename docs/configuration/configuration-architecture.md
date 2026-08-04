# Configuration architecture

Catalog V2 is the active identity model: every catalog entry is identified by
its canonical path (`actionability.contested_corridor.gap_atr`). Display IDs
are derived as `config:<path>`.

## Runtime flow

1. Source bundle (schema → profile → file secrets → **config file** → dotenv →
   process ENV → init)
2. Canonical resolver (compatibility rules + instrument registry projection)
3. `PythonRuntimeConfig` (includes typed `instruments` registry)
4. `app.core.config.runtime_config`
5. Typed production consumers

There is exactly one Python configuration authority. There is no Settings
singleton, flat facade, or runtime legacy selector.

See also `config-file-and-instrument-registry.md` for the YAML CONFIG_FILE
layer, XAU leaf projection, and deprecated ENV alias policy.

## Fingerprints

- **configuration_contract_fingerprint** — behaviorally relevant metadata used
  by startup and config-health.
- **configuration_document_fingerprint** — contract fields plus descriptions
  and evidence notes for documentation freshness.

`catalog_fingerprint()` remains as a compatibility alias of the contract
fingerprint.

## Artifacts and commands

See `configuration-governance.md`.
