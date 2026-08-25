# Config refactor — Phase 2H: environment consolidation

Phase 2H consolidates configuration onto the canonical catalog and
`runtime_config`, quarantines the legacy flat `Settings` model, and removes the
duplicated environment registry from the docker-compose bot service. It changes
no trading behavior and no profile values: the legacy authority remains the
default and stays fully selectable for rollback.

> Superseded default: as of Phase 2I-A, managed deployments default to
> **canonical** and every production `runtime_config_facade()` call is removed.
> The legacy authority stays selectable for rollback. See
> `config-refactor-phase-2i-a-canonical-cutover.md`.

## Entry gate (verified)

- Production flat `Settings` reads: `0`
- Production `settings` imports: `0`
- Phase 2G eligible reads remaining: `0`
- `phase2h_gate --check` → `READY_FOR_PHASE_2H`

## What changed

### Source policy (C)

`app.configuration.source_policy.PythonConfigurationSourcePolicy` declares the
dotenv file, encoding, and reserved secrets directory once.
`load_python_runtime_source_bundle()` now takes an optional policy (defaulting
to `PYTHON_SOURCE_POLICY`) instead of a pydantic `SettingsConfigDict`. Layer
precedence is unchanged: dotenv < process environment < explicit init values.

### Legacy quarantine + composition root (D)

- The entire `class Settings(BaseSettings)` (validators, aliases, computed
  properties, profile semantics) moved verbatim to
  `app.configuration.legacy_settings`, exported as both `Settings` and
  `LegacySettings`. The class name stays `Settings` so the legacy runtime type
  and startup message are byte-identical.
- `app.core.config` is now a composition root. It no longer defines a
  `BaseSettings` subclass; it selects the authority and builds
  `settings` / `runtime_config`, plus diagnostics:
  `active_configuration_resolution_trace`, `active_configuration_warnings`,
  `active_configuration_profile`, `active_configuration_authority`, and
  `active_configuration_catalog_fingerprint`.

### Environment usage audit + contract (B)

- `app.configuration.environment_usage_audit` performs an AST scan of
  `algo-bot/app` for `os.environ` / `os.getenv` / `dotenv_values` /
  `BaseSettings` / `SettingsConfigDict` and classifies every site
  (`BOOTSTRAP_AUTHORITY_ALLOWED`, `CANONICAL_SOURCE_COLLECTION_ALLOWED`,
  `LEGACY_ROLLBACK_ALLOWED`, `SCRIPT_TOOL_ALLOWED`, `EARLY_BOOT_ALLOWED`,
  `DEPLOYMENT_OBSERVABILITY_ALLOWED`, `DUPLICATE_ENV_REGISTRY`,
  `DIRECT_PRODUCTION_ENV_FORBIDDEN`, `UNKNOWN_BLOCKER`). There are zero unknown
  blockers.
- `app.configuration.environment_contract` derives the canonical ENV contract
  from the catalog (`iter_environment_contract_entries`,
  `environment_entry_for_name`, `environment_entry_for_path`).
- Generated artifacts:
  `contracts/configuration/environment-usage.generated.json`,
  `contracts/configuration/environment-contract.generated.json`,
  `contracts/configuration/deprecated-environment.generated.json`, and
  `docs/configuration/environment-reference.generated.md`.

### Metadata-driven alias inspection + `environment_options` deletion (E)

- `app.configuration.environment_aliases` detects deprecated-alias usage and
  conflicting alias values from catalog metadata (`canonical_env` +
  `deprecated_aliases`).
- `app.core.environment_options` is **deleted**. Its conflict/resolution
  behavior moved into
  `app.configuration.environment_option_resolution`, which builds the option
  contracts from the catalog (curated ENV-name selection ×
  catalog-derived aliases + type parsers — no second alias registry). It
  preserves the exact conflict message shape
  (`conflicting environment aliases for {canonical}: ...`), the deprecated
  warning shape (`deprecated_variable:{alias}`), the `parse_bool/int/float/
  string` parsers, and the `canonical_option_health()` /
  `deprecated_option_warnings()` API that `config_health` consumes (still
  filtered to `AUTO_TRADE_*` for `canonical_options`).
- **Composition-time conflict enforcement.** `app.core.config`
  `_build_active_configuration` calls
  `assert_no_environment_alias_conflicts()` on the legacy build, so
  `import app.core.config` fails fast on conflicting canonical/deprecated
  aliases — exactly as when `config.py` imported `environment_options` at
  import time. The canonical resolver (`sources.resolve_source_layer`) remains
  the authority for the canonical path.

### Deployment identity + `config_health` provenance migration (F)

- `app.configuration.deployment_identity` isolates the allowed-observability
  reads (`SERVICE_VERSION`, `GIT_SHA`, `AUTO_TRADE_EXPECTED_BROKER`).
- `config_health` no longer performs **any** ambient environment access. All of
  `import os`, `os.getenv`/`os.environ`, `_LEGACY_ENV_ALIASES`,
  `_PROFILE_DEFAULT_FIELDS`, and `_CANONICAL_ENV_NAMES` are removed. Provenance
  is now derived from metadata only:
  - the merged canonical source bundle (`runtime_environment_mapping()`, i.e.
    dotenv < process environment collected via `python_sources`),
  - the catalog environment contract (`iter_environment_contract_entries`,
    `environment_entry_for_name`), and
  - the selected profile document (`get_profile(profile).assignments`).
- `deprecated_environment_variables()` reports catalog-derived alias presence
  from the source bundle (`present_deprecated_aliases`).
- `resolved_config_sources()` labels each `AUTO_TRADE_*` field as
  `explicit_env`, `deprecated_env:{alias}`, `profile_{name}`, or, when no
  explicit/profile provenance can be proven from metadata,
  `application_default`.
- `required_options_missing` is updated consistently: an option is missing only
  when its catalog entry is genuinely required (no default) and unresolved.
  Because the required strategy options all have catalog defaults or profile
  values, this list is empty in normal operation — matching the pre-cleanup
  `demo_eval` production behavior — so the cross-service
  `required_strategy_key_missing` fatal rule is preserved without misfiring on
  the intentionally default/profile-supplied fields. The numeric/bool/string
  cross-service comparison fields (`compare_manifests` fatal/warning lists) are
  unchanged.

Note: `config_sources` is an informational manifest field (never compared
cross-service). It now enumerates every catalog `AUTO_TRADE_*` ENV field rather
than a hand-maintained subset; content is broader but honest.

### Generated `.env.example` (G)

`app.configuration.env_example_policy` renders a minimal, deterministic
`.env.example` from the catalog plus a small deployment policy. Secrets render
as `changeme` placeholders and the file points at
`docs/configuration/environment-reference.generated.md`.

### Compose cleanup (H)

- The shared YAML anchor was renamed and scoped to the cTrader engine:
  `x-ctrader-auto-trade-environment: &ctrader-auto-trade-environment`.
- `ctrader-engine` continues to inherit the anchor (no C# change).
- `bot` no longer inherits the anchor. It declares only its explicit overrides:
  `APEXVOID_CONFIG_AUTHORITY`, `AUTO_TRADE_PROFILE=demo_eval`,
  `AUTO_TRADE_MAPPED_ZONE_ENABLED=false`,
  `AUTO_TRADE_MARKET_MAP_GUARD_ENABLED` (follows mapped-zone), and the `LOG_*`
  variables.

**Parity proof.** The Python schema defaults plus the `demo_eval` profile
reproduce every value the 90-key anchor previously injected. Constructing the
legacy `Settings` and the canonical `PythonRuntimeConfig` under (a) the full
pre-cleanup bot environment and (b) the reduced explicit environment yields
**zero differences** for both authorities. The mapped-zone execution route and
its context guard are pinned off explicitly because the `demo_eval` profile
would otherwise enable them.

### Environment CLI (K)

`python -m app.configuration.environment_cli --check | --strict |
--report-deprecated | --report-unknown`. `--check` fails on unclassified
(`UNKNOWN_BLOCKER`) reads or alias conflicts in the process environment;
`--strict` additionally fails on directly forbidden production reads.

### Ambient environment reader boundary

After Phase 2H the environment-usage audit reports **zero**
`DIRECT_PRODUCTION_ENV_FORBIDDEN`, **zero** `DUPLICATE_ENV_REGISTRY`, and
**zero** `UNKNOWN_BLOCKER`. The remaining ambient readers are all reviewed
boundaries:

- `bootstrap_authority.py` — `APEXVOID_CONFIG_AUTHORITY` selection.
- `python_sources.py` — canonical source collection (process + dotenv).
- `legacy_settings.py` — legacy `BaseSettings` rollback path (incl. the
  internal BE-buffer conflict check).
- `deployment_identity.py` — deployment observability (`SERVICE_VERSION`,
  `GIT_SHA`, `AUTO_TRADE_EXPECTED_BROKER`).
- Tooling/CLIs (`generate.py`, `phase2h_gate.py`, `environment_cli.py`,
  `shadow_cli.py`, activation tools) — `SCRIPT_TOOL_ALLOWED`.

`strategy_match_ready.ready_consumer_name()` now uses `socket.gethostname()`
instead of reading `HOSTNAME`, and `logging_setup.configure_logging()` takes its
directory from `runtime_config` (no `LOG_DIR` fallback read).

## Completed follow-up (previously deferred)

The two invasive rewrites originally deferred are now complete:

- `app.core.environment_options` is deleted; its behavior lives in
  `app.configuration.environment_option_resolution` (see section E).
- `config_health` is fully de-duplicated and metadata-driven (see section F).
  `_LEGACY_ENV_ALIASES`, `_CANONICAL_ENV_NAMES`, `_PROFILE_DEFAULT_FIELDS`, and
  `import os` are all gone.

## Verification

```bash
cd algo-bot
.venv/bin/python -m app.configuration.generate --check
.venv/bin/python -m app.configuration.phase2h_gate --check
.venv/bin/python -m pytest -q \
  tests/test_config_phase2h*.py tests/test_config_authority*.py -m no_database
cd .. && docker compose config -q
```
