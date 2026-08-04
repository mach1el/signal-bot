# Phase 2I-B — Canonical-only configuration (core path)

Status: **core production path landed**; full Phase 2I completion (test + docs
migration and `PHASE_2I_COMPLETE` gate) remains for follow-up.

## Authorization

Phase 2I-B was authorized as an **explicit structural architecture decision**
by the project owner. No production observation window was fabricated, and no
observation evidence is claimed.

Verified Phase 2I-A.1 baseline ancestor:

`a7d6a6718247d5b4587fc39c10d3f280e08cc9d0`

## Final startup flow (landed)

```
app.core.config
  -> load_python_runtime_source_bundle()
  -> load_python_canonical_settings()
  -> PythonRuntimeConfig
  -> runtime_config
```

There is no authority branch and no flat `Settings` surface.

## Removed (core path)

- `legacy_settings.py` / `Settings` / `LegacySettings`
- `legacy_canonical_view.py`
- `facade.py` / `CanonicalSettingsFacade` / `runtime_config_facade`
- `bootstrap_authority.py` / `APEXVOID_CONFIG_AUTHORITY` selector
- `generated/legacy_access.py` (DIRECT_LEGACY_PATHS maps)
- Activation/parity/shadow/rollback rehearsal modules
- `phase2i_removal_gate.py`

Deployment selector removed from:

- `docker-compose.yml`
- `deployment-template/docker-compose.yml.j2`
- `.env.example` / `env_example_policy.py`

## Leftover ENV policy

If `APEXVOID_CONFIG_AUTHORITY` remains in a host environment, it is an
**unmanaged unknown environment variable**. It is not read, is not a selector,
and does not alter runtime behavior. Do not interpret `=legacy` as canonical
via a retained ignore-selector.

## Recovery

There is no legacy authority rollback.

1. Revert the Phase 2I-B deployment commit;
2. Rebuild/redeploy the previous known-good image;
3. Restart the bot;
4. Verify configuration fingerprint and health.

Canonical validation failures: correct the invalid input and restart.

## Added diagnostics

- `python -m app.configuration.diagnostic_cli --check`
  → `CANONICAL_CONFIGURATION_VALID`
- `python -m app.configuration.phase2i_completion_gate --check`
  → will report `PHASE_2I_COMPLETE` only after remaining test/tooling
  references are cleared (currently `PHASE_2I_INCOMPLETE` by design of the
  core-first cut).

## Follow-up (not in this core-path slice)

- Migrate tests still importing Settings / facade / DIRECT_LEGACY_PATHS /
  authority / shadow / activation helpers
- Tighten completion-gate symbol proofs to zero across tests
- Finish ADR + runbook archival updates from the full Phase 2I-B mission
- Optional retirement of historical `legacy-map` / `legacy-usage` generators

## Explicit non-claims

- No trading behavior change intended
- No configuration default or profile assignment change intended
- No deprecated catalog-backed ENV alias removed
- No C# or CI workflow changes
- No fabricated production observation evidence
