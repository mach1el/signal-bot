# Phase 2D1: legacy facade and activation rehearsal

## 1. Scope and non-goals

Phase 2D1 prepares evidence for a later grouped-configuration activation. It
does not activate it. `app.core.config.Settings` and the unchanged module
singleton `settings = Settings()` remain authoritative. The canonical facade
is inactive and no startup, scanner, detector, worker, TradePlan, execution,
or delivery consumer imports it.

This phase changes no ENV name, `.env.example`, Compose value, deployment
template, C# binding, profile behavior, source precedence, trading decision,
database behavior, reload behavior, or CI workflow. There is no runtime
authority ENV, hot reload, generated-JSON runtime read, singleton replacement,
or import-time rehearsal.

## 2. Legacy API usage audit

`app.configuration.usage_audit` parses Python ASTs across production, tests,
and scripts. It tracks direct and aliased imports, module-qualified access,
attribute contexts, restricted dynamic-name domains, introspection, class API,
construction, typed injection, structural dependency passing, and mutation.
The checked-in, deterministic result is
`contracts/configuration/legacy-usage.generated.json`.

| Surface | Reads | Writes | Deletes | Methods | Introspection | Type/dependency |
|---|---:|---:|---:|---:|---:|---:|
| Production | 373 | 0 | 0 | 0 | 38 | 47 |
| Tests | 14 | 29 | 0 | 1 | 1 | 48 |
| Scripts/tools | 2 | 0 | 0 | 0 | 0 | 0 |

There are zero production activation blockers and zero production mutations.
The 29 writes are test-only adaptations. Audit rows use repository-relative
paths, stable ordering, no timestamp, no effective values, and a SHA-256
fingerprint.

## 3. Supported facade surface

`CanonicalSettingsFacade` is a small read-only structural adapter over frozen
`ApexVoidConfig`. It supports exactly:

- 316 direct legacy attributes;
- `signal_vip_channel_id`;
- `telegram_chat_id`;
- `xau_public_channel_id`;
- `xau_vip_channel_id`;
- `get_legacy_value(name)`, `legacy_field_names()`, and `canonical_config`.

It does not inherit `Settings`, `BaseSettings`, or `BaseModel`. It has no
instance `__dict__`, rejects assignment and deletion, raises `AttributeError`
for unknown names, and has a count-only representation. The canonical root is
itself frozen. Derived transformations are explicit functions; no expression
evaluation is used.

## 4. Compatibility table

| Usage pattern | Production locations | Facade support | Migration required | Activation blocker | Notes |
|---|---|---|---|---|---|
| `settings.field` | app startup, bot, signals, scanner, worker, persistence | Yes | No | No | All observed fields exist in the generated map |
| `getattr(settings, literal)` | scanner, worker, delivery | Yes | No | No | Catalogued names preserve exact type |
| `getattr(settings, optional_name, fallback)` | delivery, worker | Yes | No | No | Unknown attribute correctly triggers Python's supplied fallback |
| restricted dynamic `getattr` | OHLC source and static lookup domains | Yes | No | No | Auditor proves the name domain is a subset of generated names |
| Settings passed structurally | analysis/autotrade helpers | Yes | No | No | Downstream reads remain in the audited read surface |
| `model_dump`, class fields, monkeypatch writes | tests/config characterization | Test adaptation | No production migration | No | Not implemented on the facade |
| assignment/deletion/type identity in production | None | No | Yes if introduced | Yes | Facade mutation always raises `TypeError` |

Unsupported Pydantic compatibility such as `model_dump`, `model_copy`,
`model_fields_set`, or a fake `__dict__` is intentionally absent. Any future
production use is classified as a blocker rather than silently emulated.

## 5. Generated Python access contract

The deterministic generator now owns ten artifacts. New artifacts are:

- `contracts/configuration/legacy-usage.generated.json`;
- `algo-bot/app/configuration/generated/__init__.py`;
- `algo-bot/app/configuration/generated/legacy_access.py`.

`legacy_access.py` contains immutable `MappingProxyType` declarations for 316
direct paths, four `DerivedLegacyAccessSpec` records, type names, required
field names, secret field names, and the catalog fingerprint. It is generated
from typed catalog metadata. It contains no resolved secret, environment
value, JSON read, `eval`, or dynamically executed source.

## 6. Four-fixture facade parity

| Fixture | Direct facade parity | Derived parity |
|---|---:|---:|
| Direct conservative | 316/316 | 4/4 |
| Direct `demo_eval` | 316/316 | 4/4 |
| Root Compose `demo_eval` | 316/316 | 4/4 |
| Test/conftest environment | 316/316 | 4/4 |
| **Total** | **1,264/1,264** | **16/16** |

Every comparison checks value and exact Python type. Unknown-name behavior,
immutability, `dir()`, the frozen canonical root, and secret-safe `repr` are
also tested.

## 7. Authority abstraction

`ConfigurationAuthority` defines only `LEGACY` and
`CANONICAL_REHEARSAL`; there is deliberately no production `CANONICAL` mode.
`build_configuration_runtime_bundle` accepts explicit source and legacy
objects, defaults to `LEGACY`, and reads no authority ENV. Even when its local
selected object is the facade, `authoritative_object` remains the supplied
legacy instance and `selected_is_authoritative` is false.

No module singleton or ambient selector is created. The factory is referenced
only by tests, tools, and the rehearsal package.

## 8. Dual-loader rehearsal

The pure rehearsal receives one `ConfigurationSourceBundle`, its corresponding
legacy instance, repository root, active singleton identity, and explicit
verification evidence. It shadow-loads the frozen root, creates the facade,
compares 316 direct and four derived values, runs the usage audit, validates
provenance and generated artifacts, rehearses rollback, and evaluates
readiness.

Compatibility success means the two loaders and facade agree. It never means
production activation is approved. The result enforces `authoritative=false`.

## 9. Rollback rehearsal

The local selector exercises:

```text
LEGACY -> CANONICAL_REHEARSAL -> LEGACY
```

The result proves that the original legacy object identity is returned, all
320 exposed values and exact types remain equal, the active singleton is not
replaced, and no source mapping is mutated. This is not a live hot rollback.
Production rollback remains a process restart onto legacy `Settings` until a
later phase.

## 10. Activation readiness evidence

`ActivationEvidence`, `ActivationReadiness`, and `RehearsalPermit` are frozen,
deterministic records. Evidence covers catalog/source/facade/derived parity,
provenance, generated drift, redaction, usage support, rollback, Python config
tests, behavioral-suite status, and local C# verification.

A permit can only select `CANONICAL_REHEARSAL`; it always records
`production_activation_allowed=false`. There is no `force`, `skip_checks`,
`ignore_readiness`, or environment bypass.

## 11. Current blocker codes

With a complete characterized source fixture and successful Phase 2D1 tests,
internal compatibility evidence is complete. Production activation readiness
is still false because:

- `PYTHON_BEHAVIOR_TESTS_FAILED` — the unchanged 13 behavioral tests fail;
- `CSHARP_TESTS_NOT_VERIFIED` — no supported local .NET SDK was available.

When the repository `.env` lacks required Telegram/cTrader input, the offline
CLI additionally reports source, facade, provenance, rollback, and in-process
test-evidence blockers. This is conservative diagnostic behavior, not a
runtime fallback.

## 12. Diagnostic CLI and verification path

```bash
cd algo-bot
python -m app.configuration.activation_cli \
  --env-file ../.env \
  --report-readiness
```

Output begins with `NON-AUTHORITATIVE ACTIVATION REHEARSAL` and shows only
statuses, parity counts, blocker codes, and fingerprints. Optional switches
report usage, parity, blockers, or write the same secret-safe JSON structure.
The CLI does not connect to Redis, PostgreSQL, Telegram, or cTrader and starts
no application tasks.

`activation_verification_plan()` declares reproducible Python catalog,
generator, and Phase 2D1 test commands plus `dotnet --info` and the cTrader
test project command. It creates no workflow and changes no C# source. Local
C# status is `not_verified_locally` when `dotnet` is absent; this is never
reported as a pass.

## 13. Existing Python behavior failures

The baseline at `d4ef197` was 1,524 passed and the same 13 failures retained
from Phase 2C. They are:

- six `test_auto_scalp_worker.py` expectations covering HTF veto, nearby
  supply, round-level veto, strategy-match round veto, ahead logic, and active
  cooldown;
- `test_confluence_card_rr_pregate.py::test_one_card_and_one_setup_per_merged_zone`;
- `test_strategy_match.py::test_insufficient_target_room_is_rejected_with_a_reason_not_silently`;
- `test_trade_plan_builder.py::test_stop_inside_opposing_zone_surfaces_precise_reason_and_evidence`;
- two parameterizations of
  `test_worker_veto_regression_replay.py::test_entry_inside_opposing_structure_is_preference_telemetry`;
- `test_worker_veto_regression_replay.py::test_counter_bias_barrier_with_no_minimum_room_is_terminal`;
- `test_worker_veto_regression_replay.py::test_news_wait_preserves_active_match`.

Phase 2D1 does not alter, skip, xfail, allowlist, or weaken these tests. They
remain readiness blockers until a separate trading-behavior reconciliation.

## 14. Phase 2D2 entry gate

Phase 2D2 may propose restart-selected authoritative facade activation only
after production usage remains fully supported, generated and parity evidence
stays complete, configuration tests pass, the behavioral failure set is
resolved, C# compatibility is verified on a supported SDK, and an activation/
restart rollback runbook is approved.

Phase 2D2 must preserve an explicit legacy restart path. Phase 2D1 grants no
production activation approval.
