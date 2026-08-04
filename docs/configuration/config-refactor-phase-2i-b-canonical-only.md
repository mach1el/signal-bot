# Phase 2I-B / final — Canonical-only Python configuration

Status: **PHASE_2I_COMPLETE** (structural)

## Authorization

Phase 2I was authorized as an **explicit structural architecture decision**.
No production observation evidence was fabricated.

Baseline after PR #216: `8f2aef8138ca93582fa2c89e665e92df4302bb61`

## Final architecture

```
app.core.config
  -> load_python_runtime_source_bundle()
  -> load_python_canonical_settings()
  -> PythonRuntimeConfig
  -> runtime_config
```

- One authority: `canonical`
- No `Settings` / `settings` singleton
- No `APEXVOID_CONFIG_AUTHORITY` selector
- No flat facade / legacy view / legacy access maps
- Deprecated catalog-backed ENV aliases remain supported

## Recovery

1. Revert/redeploy the previous known-good image, or
2. Correct invalid configuration input and restart the service

Do not set `APEXVOID_CONFIG_AUTHORITY=legacy` — the selector does not exist.

## Active commands

```
python -m app.configuration.catalog_validation ...
python -m app.configuration.generate --check
python -m app.configuration.diagnostic_cli --check
python -m app.configuration.phase2i_completion_gate --check
```

Successful completion gate status: `PHASE_2I_COMPLETE`

## Historical artifacts

Phase 2A–2I-A migration ledgers live under
`docs/configuration/history/artifacts/` and are not regenerated.
