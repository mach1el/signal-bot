# Phase 2D2: selectable canonical Python authority

## 1. Scope and non-goals

Phase 2D2 makes canonical configuration production-selectable for the Python
bot behind one explicit, restart-only bootstrap control. The default remains
legacy. This phase does not change a strategy, risk parameter, execution
decision, existing ENV name, C# runtime binding, CI workflow, or database
behavior. It does not remove the legacy `Settings` class or implement hot
reload.

## 2. Python service projection

`PythonRuntimeConfig` is derived mechanically from the canonical Pydantic
model tree and each leaf's typed ownership metadata. It includes all
Python-owned and shared leaves and excludes every cTrader-only leaf.

| Inventory | Count |
|---|---:|
| Complete canonical leaves | 437 |
| Included Python leaves | 292 |
| Included shared leaves | 95 |
| Python runtime projection | 387 |
| Excluded cTrader-only leaves | 50 |
| Included direct legacy fields | 316 |
| Included secrets | 6 |

Homogeneous nested models are reused, preserving their validators. Mixed-owner
models are derived from their existing `FieldInfo` declarations, which keeps
types, aliases, defaults, constraints, and strict frozen model configuration.
Generation fails if a mixed-owner model acquires a validator without explicit
review. Runtime never reads a generated JSON artifact.

The deterministic projection inventory is checked in at
`contracts/configuration/python-runtime-projection.generated.json`. The full
`ApexVoidConfig` remains unchanged and continues to own complete catalog and
cross-service generation.

## 3. Why cTrader-only fields are excluded

The Python bot does not authenticate an Open API session. Requiring
`CTRADER_ACCESS_TOKEN`, account id, client id/secret, refresh token, engine
timeouts, or cTrader-only execution controls would couple bot startup to a
different service. Canonical Python startup therefore validates only the 387
leaves it owns or shares. It injects no placeholder credential and does not
make cTrader credentials optional in the complete root.

## 4. Authority selector and legacy default

`APEXVOID_CONFIG_AUTHORITY` is a loader bootstrap control outside the
437-field catalog. Accepted values are `legacy` and `canonical`, after trim
and case normalization. Omission selects `legacy`. An explicit blank or any
other value fails startup.

Legacy startup constructs the existing `Settings()` singleton with unchanged
Pydantic Settings behavior. `Settings` remains importable and constructible by
tests and tools.

## 5. Canonical startup and fail-closed behavior

Canonical startup collects the exact `.env` setting declared by
`Settings.model_config`, current process ENV, and the existing empty
file-secret layer. It then applies:

```text
schema defaults -> profile -> file secrets -> dotenv -> process ENV
  -> empty init values -> compatibility rules -> PythonRuntimeConfig validation
```

The validated frozen root is exposed through the same immutable
`CanonicalSettingsFacade`: 316 direct attributes and four derived properties.
It is both selected and authoritative in canonical mode.

Missing required Python input, parse error, alias conflict, unsupported
profile, cross-field validation error, or projection/catalog drift terminates
startup. The error reports safe category/path/source metadata, the catalog
fingerprint, and this action:

```text
set APEXVOID_CONFIG_AUTHORITY=legacy and restart the service
```

There is no catch-and-fallback path and no legacy `Settings` construction in
canonical startup.

## 6. Restart rollback

Authority is resolved once during `app.core.config` import. The active state
is frozen and the module exports exactly one `settings` object. Changing ENV
inside a running process does nothing.

Subprocess tests prove canonical startup produces
`CanonicalSettingsFacade`, a new process with legacy selected produces
`Settings`, no canonical state persists, and the 320 exposed values retain
value and exact-type parity across the restart boundary.

## 7. Facade and production usage compatibility

The production AST audit remains at 373 supported reads, zero writes, zero
deletes, and zero activation blockers. Restricted dynamic reads remain within
the generated access domain. No production consumer requires `Settings` type
identity or a `BaseSettings` method.

## 8. Readiness policy change

Activation remains blocked by catalog/source/facade/derived parity,
provenance, generated drift, redaction, compatibility usage, rollback,
configuration tests, or a worsened Python behavior baseline. Full unrelated
trading-suite success and local C# test execution are informational only.

When absent, they produce `PYTHON_BEHAVIOR_TESTS_NOT_GREEN` and
`CSHARP_TESTS_NOT_RUN` warnings without setting `ready=false`. No failure count
is encoded in runtime logic, and no CI status is consulted.

## 9. Startup diagnostics

After logging is configured, startup emits one secret-safe authority record.
Legacy logs only `configuration_authority=legacy`. Canonical additionally logs
profile, catalog fingerprint, 316 facade fields, and four derived fields. No
configuration value, credential, source candidate, or `.env` content is
logged.

## 10. Local verification results

On the Phase 2D2 branch:

- catalog validation: 437 items;
- generated drift check: 11 artifacts current;
- targeted activation/config tests: 116 passed;
- complete configuration selection: 251 passed;
- projection: 387 included / 50 excluded / 316 direct legacy fields;
- production usage: 373 reads / 0 writes / 0 deletes / 0 blockers;
- Docker Compose syntax: valid;
- isolated legacy/canonical startup and restart rollback: passed.

The full trading suite, CI, dotnet, and C# tests are not Phase 2D2 activation
gates and were not represented as passing.

## 11. Phase 2E entry criteria

Phase 2E may consider making canonical the default only after operational
canonical activation is observed, startup diagnostics and bot health are
stable, rollback is rehearsed against deployment configuration, the usage
audit remains mutation-free, and no configuration parity regression exists.
It must be a separate reviewed change; Phase 2D2 keeps legacy as default.
