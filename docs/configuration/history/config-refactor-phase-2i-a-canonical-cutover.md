> Historical Phase 2A–2I-A artifact; superseded by Phase 2I-B structural completion. No observation evidence was fabricated.

# Config refactor — Phase 2I-A: canonical cutover

Phase 2I-A makes **canonical** the managed-deployment default authority and
removes every remaining *production* `runtime_config_facade()` call, while
keeping the entire legacy stack (`Settings` / `LegacySettings`, the `legacy`
authority value, `LegacyCanonicalConfigView`, and `CanonicalSettingsFacade`)
intact and selectable for rollback. It changes no trading values, profiles,
precedence, aliases, cTrader environment, or CI. It does **not** delete the
legacy stack — that is gated behind a real canonical observation window.

## Entry gate (verified)

- Phase 2H baseline: `phase2h_gate --check` → `READY_FOR_PHASE_2H`
- Production flat `Settings` reads / imports: `0`
- Production `runtime_config_facade()` calls: `0`
- Compatibility-surface unknown blockers: `0`
- `phase2i_removal_gate --check-static` → `READY_FOR_CANONICAL_OBSERVATION`

## What changed

### 1. Compatibility-surface audit

`app.configuration.compatibility_surface_audit` (AST-backed) classifies every
reference to the compatibility surface — `Settings`, `LegacySettings`,
`settings`, `CanonicalSettingsFacade`, `LegacyCanonicalConfigView`,
`runtime_config_facade`, `DIRECT_LEGACY_PATHS`, `DERIVED_LEGACY_PROPERTIES`,
`getattr` with a known legacy field name, and `SimpleNamespace` config
fixtures — into six buckets:

- `PRODUCTION_REMOVE_2I_A` — production facade calls (must be `0`).
- `TEST_COMPATIBILITY_RETAIN_2I_A` — flat overrides tests still rely on.
- `LEGACY_ROLLBACK_RETAIN_2I_A` — legacy authority + rollback types at the root.
- `TOOLING_RETAIN_2I_A` — generators/audits and the narrow-projection builder.
- `REMOVE_2I_B` — the `runtime_config_facade` definition/export (removed in 2I-B).
- `UNKNOWN_BLOCKER` — unaccounted production usage (must be `0`).

`generate.py --write/--check` emits
`contracts/configuration/compatibility-surface-phase-2i-a.generated.json`.

### 2. Production facade removal

Every production default that previously called `runtime_config_facade()` now,
when `cfg is None`, builds a **narrow one-shot `SimpleNamespace`** via
`app.core.runtime_projection.project_runtime_config` (backed by
`app.configuration.runtime_projection.project_from`). That helper reads only the
specific legacy field names each consumer declares off the active
`runtime_config` through the reviewed `DIRECT_LEGACY_PATHS` traversal — the same
traversal `CanonicalSettingsFacade` performs — guaranteeing value/type parity
without a persistent `__getattr__` facade or the full flat surface. When a
`cfg` is passed explicitly (tests), the existing flat `getattr` path is
unchanged.

This is an **interim Phase 2I-A bridge**, not a second permanent facade.
**Phase 2I-A.1 removed this bridge** and replaced projected flat snapshots with
typed canonical domain reads (see
`config-refactor-phase-2i-a1-canonical-domain-injection.md`). Phase 2I-B then
removes the remaining flat Settings / facade rollback surface.

Migrated call sites: `autotrade/worker.py` (the `_cfg()` helper was eliminated
and its 25 call sites now pass `None`), `autotrade/trend.py`,
`autotrade/execution_policy.py`, `autotrade/zone_execution_cutover.py`,
`autotrade/map_strategy.py`, `autotrade/scale_context.py`,
`analysis/market_map.py`, `analysis/scanner.py`, `analysis/detectors.py`,
`analysis/actionability.py`, and `analysis/m1_trigger.py`. No production module
outside the composition root and the configuration package imports
`CanonicalSettingsFacade`, `LegacyCanonicalConfigView`, `Settings`, or
`settings`.

### 3. Canonical deployment cutover

`docker-compose.yml` (`${APEXVOID_CONFIG_AUTHORITY:-canonical}`), the production
`deployment-template/docker-compose.yml.j2` execution defaults, and the
generated `.env.example` (via `env_example_policy.py`) now default to
`canonical`. Profile pins, mapped-zone/guard flags, logging, secrets, and the
cTrader engine environment are unchanged.

### 4/5. Implicit-authority warning + legacy deprecation diagnostic

The parser is unchanged for a missing variable (still resolves to `legacy`), but
`_ActiveConfiguration` now carries `authority_explicit: bool` and the root
exposes:

- `active_configuration_implicit_authority_warning()` →
  `configuration_authority_implicit=true selected_authority=legacy recommended_authority=canonical`
  (absent env var only).
- `active_configuration_deprecation_message()` →
  `configuration_authority=legacy configuration_authority_deprecated=true rollback_mode=true planned_removal_phase=2I-B`
  (explicit `legacy` only).

`app.main` logs both once at startup. Canonical startup remains fail-closed and
its message is unchanged.

### 6. Removal gate

`app.configuration.phase2i_removal_gate`:

- `--check-static` → `READY_FOR_CANONICAL_OBSERVATION` when all static criteria
  pass (production Settings imports `0`, flat reads `0`, facade calls `0`,
  dynamic flat lookups `0`, compose/template/`.env.example` default canonical,
  legacy still startable, artifacts current, unknown compatibility blockers `0`).
  It can **never** return `READY_TO_DELETE_LEGACY`.
- `--check-observation <path>` validates the *structure and completeness* of an
  operator evidence file only (never inspecting live systems, never fabricating
  results) → `READY_FOR_PHASE_2I_B_REVIEW` when complete.

## Rollback

Set `APEXVOID_CONFIG_AUTHORITY=legacy` and restart. The legacy authority, its
runtime type (`Settings`), and its startup message are unchanged; the only new
behavior is the one-line deprecation diagnostic.

## Not in scope (deferred to 2I-B)

Deleting `legacy_settings`, `LegacyCanonicalConfigView`,
`CanonicalSettingsFacade`, the `runtime_config_facade` definition, the `legacy`
authority value, and the flat `settings`/`Settings` exports. Those require the
canonical observation window described in
`canonical-authority-observation-runbook.md`.
