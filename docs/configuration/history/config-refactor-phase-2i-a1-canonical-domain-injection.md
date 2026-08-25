> Historical Phase 2A–2I-A artifact; superseded by Phase 2I-B structural completion. No observation evidence was fabricated.

# Phase 2I-A.1 — Canonical typed domain injection

## 1. Scope and non-goals

Phase 2I-A.1 replaces legacy-shaped runtime configuration projections with typed
canonical domain injection throughout production Python code.

This phase is structural configuration-access work only. It does **not**:

- change trading, analysis, scanner, execution, risk, or lifecycle formulas;
- change configuration defaults, profiles, ENV names/aliases, or Compose values;
- remove the legacy rollback stack (`Settings`, `LegacySettings`,
  `LegacyCanonicalConfigView`, `CanonicalSettingsFacade`, `settings`,
  `runtime_config_facade`, `DIRECT_LEGACY_PATHS`);
- begin Phase 2I-B;
- modify C# or CI workflows.

## 2. Phase 2I-A baseline

Phase 2I-A made canonical configuration the managed-deployment authority and
introduced a temporary compatibility bridge:

`project_runtime_config(...)` → `project_from(...)` → `DIRECT_LEGACY_PATHS` →
flat `SimpleNamespace` with legacy field names.

Production no longer called `runtime_config_facade()`, but consumers still read
flat legacy attribute names from projected snapshots.

## 3. Temporary runtime projection architecture

The bridge preserved value parity while callers still used patterns such as:

```python
_RUNTIME_SOME_CFG_FIELDS = ("auto_trade_...", "scanner_...")
cfg = project_runtime_config(_RUNTIME_SOME_CFG_FIELDS)
value = cfg.auto_trade_...
```

Phase 2I-A.1 removes that bridge entirely.

## 4. Projection inventory

The AST inventory lives in:

- `algo-bot/app/configuration/canonical_consumer_surface.py`
- `contracts/configuration/canonical-consumer-surface-phase-2i-a1.generated.json`

End-state production counts:

- `project_runtime_config` calls = 0
- `project_from` calls = 0
- legacy projection tuples = 0
- production config `SimpleNamespace` projections = 0
- production `DIRECT_LEGACY_PATHS` imports = 0
- unknown blockers = 0

## 5. Canonical typed injection policy

Priority order:

1. Pass an existing grouped node (`runtime_config.actionability`, etc.).
2. Pass the narrowest existing submodel (`runtime_config.execution.policy`).
3. Define a narrow Protocol with canonical semantic names when DI requires it.
4. Create an immutable function-specific input object only when a call genuinely
   combines multiple roots — constructed at a composition boundary with no
   defaults, ENV parsing, or legacy mapping.

## 6. Protocol and submodel decisions

Most consumers now default `cfg=None` to `runtime_config` and read nested paths
such as `cfg.actionability.target_room.barrier_buffer_atr`.

The analysis engine retains its flat `AnalysisSettings` DTO for internal
orchestration and projects it into the nested shape expected by trendlines /
session / scalp helpers via an internal composition adapter
(`_nested_cfg_from_analysis_settings`). That adapter is not a second
configuration system.

## 7. Test fixture migration

Reusable builders live under `algo-bot/tests/configuration/canonical_fixtures.py`:

- domain builders (`actionability_cfg`, `execution_cfg`, `market_map_cfg`, …)
- `canonical_ns_from_flat` — test-only adapter marked for Phase 2I-B removal

Production packages must never import these helpers.

## 8. Removed projection modules

Deleted:

- `algo-bot/app/core/runtime_projection.py`
- `algo-bot/app/configuration/runtime_projection.py`

`generated/legacy_access.py` is retained for rollback and Phase 2I-B tooling.

## 9. DIRECT_LEGACY_PATHS boundary

After Phase 2I-A.1, `DIRECT_LEGACY_PATHS` may be imported only by:

- legacy rollback / facade implementation;
- compatibility audits and generators;
- migration tooling;
- tests validating legacy parity.

Production trading, analysis, execution, delivery, and lifecycle modules must
not import it. Architecture guards enforce this.

## 10. Value/type parity

Typed consumers resolve the same leaves under both authorities:

- `APEXVOID_CONFIG_AUTHORITY=canonical` → `ApexVoidConfig` grouped models
- `APEXVOID_CONFIG_AUTHORITY=legacy` → `LegacyCanonicalConfigView` grouped nodes

Facade flat reads remain available for rollback and tests. Guard tests assert
sample and batch value/type parity against `DIRECT_LEGACY_PATHS`.

## 11. Legacy rollback preservation

Kept intact for Phase 2I-B:

- `Settings` / `LegacySettings` / `settings`
- `LegacyCanonicalConfigView`
- `CanonicalSettingsFacade` / `runtime_config_facade`
- `DIRECT_LEGACY_PATHS` and derived maps

Canonical remains the managed deployment authority.

## 12. Phase 2I-B readiness impact

Phase 2I-A.1 clears the production projection surface so Phase 2I-B can focus on
removing the flat Settings facade and ENV/legacy rollback stack without also
rewiring every consumer. Observation windows for canonical authority should use
the release that includes this phase.
