# Phase 2C: deterministic non-authoritative shadow loading

## 1. Scope and non-goals

Phase 2C wraps the inactive, frozen `ApexVoidConfig` catalog with immutable
profile documents and a pure source resolver. It proves that the grouped model
can reproduce the active flat loader without activating it.

The authoritative runtime remains `app.core.config.Settings`, including the
module-level `settings = Settings()` instance. Phase 2C does not import the
shadow loader from `app.core.config`, `app.main`, scanners, workers, or trade
planning. It changes no ENV name, Compose value, deployment template, C#
binding, strategy, risk threshold, execution policy, database configuration,
or reload behavior. There is no background task and no import-time shadow
load.

## 2. Source precedence

The pure resolver accepts four supplied mappings and reads no ambient state:

```text
schema default
  -> selected immutable profile
  -> file-secret mapping
  -> dotenv mapping
  -> process-environment mapping
  -> explicit init mapping
  -> source-aware compatibility rules
  -> ApexVoidConfig validation
```

Profile selection is itself resolved from the highest-precedence supplied
`runtime.profile` value before profile assignments are overlaid. Profile and
enum-like strings retain legacy trim/lower normalization.

| Field/scenario | Schema | Profile | Dotenv | Process ENV | Init | Effective source | Result |
|---|---|---|---|---|---|---|---|
| direct demo mapped zone | false | true | — | — | — | PROFILE | true |
| Compose demo mapped zone | false | true | — | false | — | PROCESS_ENV | false |
| process alias over dotenv canonical | default | — | canonical | alias | — | PROCESS_ENV alias | parsed process value |
| init over process canonical | default | — | — | canonical | canonical path | INIT_VALUE | init value |
| implicit market-map guard | true | profile value | — | — | — | DERIVED_COMPATIBILITY_RULE | mapped-zone value |
| explicit market-map guard | true | profile value | explicit | — | — | DOTENV | explicit value |
| conservative live structural guard | balanced | — | require-demo=false | — | — | DERIVED_COMPATIBILITY_RULE | strict |
| schema-only constant | constant | — | not accepted | not accepted | not accepted | SCHEMA_DEFAULT | declared constant |

## 3. Immutable profile documents

`app.configuration.profiles` declares frozen `ConfigProfile` documents whose
assignments are frozen, sorted tuples of canonical path/value pairs. There are
no flat legacy attributes, ENV names, secrets, runtime dictionaries, or
`setattr()` mutations.

| Profile | Assignments | Behavior |
|---|---:|---|
| `conservative` | 0 | grouped schema defaults plus compatibility rules |
| `demo_eval` | 48 | exact assignments characterized from the legacy profile validator |

Tests prove every assignment path exists in typed traversal, each value
validates against its target field, paths are unique and deterministic, and no
assignment targets a secret.

## 4. Alias-resolution semantics

Canonical names and declared deprecated aliases are resolved independently in
each source layer. Declared `AliasChoices` order is preserved. One supplied
name is accepted. Multiple names with equal parsed values are accepted with a
duplicate warning; the canonical name wins when present. Every supplied
deprecated alias emits a warning identifying canonical ENV, alias, canonical
path, and layer.

Different parsed values in one layer produce a deterministic conflict and no
candidate. The same alias in a higher layer can validly override a canonical
name in a lower layer. The deprecated `AUTO_TRADE_BE_BUFFER_PIPS` remains
tick-valued; a differing TICKS/PIPS pair produces the legacy-compatible
conflict without printing either value.

Typed parsing covers booleans, integers, floats, decimals, optional strings,
optional integers, native integer/string lists, signed values and whitespace.
Native lists accept CSV input. Legacy CSV-string fields remain strings.
Parsing errors identify path, layer, supplied name and expected type, never the
raw value.

## 5. Source provenance model

Every typed leaf receives one final immutable `ResolvedFieldSource` record:

- canonical path and item id;
- effective source kind and supplied name;
- canonical ENV and supplied alias;
- explicit versus implicit status;
- overridden lower-precedence sources;
- selected profile;
- compatibility-rule name;
- secret flag.

Trace records carry no raw or effective values. Secret candidates are confined
to the internal validation payload and excluded from representations. Warning,
conflict, parity, CLI, and JSON structures contain metadata only or explicit
`<redacted>` placeholders.

## 6. Source-aware compatibility rules

Phase 2C implements only rules deferred by Phase 2B:

1. `demo_eval` rejects an explicitly supplied false demo-account requirement;
   a profile assignment is not explicit.
2. Conservative non-demo mode derives structural guard `strict` when the guard
   was not explicitly supplied.
3. An implicit market-map guard inherits the final mapped-zone switch after
   external overlays.
4. Disabled zone reconciliation derives mode `off`.
5. Canonical/deprecated BE tick aliases conflict when their parsed values
   differ in one source layer.
6. Profile and enum policy strings retain legacy trim/lower normalization.

Derived fields are marked `DERIVED_COMPATIBILITY_RULE`, including the exact
rule name and overridden source history.

## 7. Complete, incomplete, and invalid shadow loading

`load_shadow_configuration(ConfigurationSourceBundle)` returns one of:

- `COMPLETE`: `ApexVoidConfig` validated successfully;
- `INCOMPLETE_REQUIRED_INPUT`: owner-specific required values are absent;
- `INVALID`: resolution conflicts or grouped validation errors exist.

Missing cTrader credentials are reported as canonical paths. No fake
credential or hidden fallback is injected, and incomplete input never creates
a misleading config object. Every `ShadowLoadResult` has
`authoritative = false`; construction rejects any attempt to mark it true.

## 8. Old/new fixture parity

The parity harness traverses typed metadata directly and compares all 316
direct legacy fields by value and exact Python type. Computed properties remain
separate.

| Fixture | Direct fields | Result |
|---|---:|---|
| direct conservative | 316 | 316 equal |
| direct `demo_eval` | 316 | 316 equal |
| root Compose `demo_eval` | 316 | 316 equal |
| current test/conftest environment | 316 | 316 equal |
| **Total** | **1,264** | **1,264 equal** |

All four computed properties match in all four fixtures: 16/16 comparisons.
The intentional cross-fixture divergence remains: mapped-zone and market-map
guard are true in direct demo and false in root Compose demo, while old and new
agree inside each fixture.

## 9. Invalid-input parity

Legacy and shadow loaders both reject the characterized cases: unsupported
profile, explicit demo-account disable in demo mode, invalid zone-width order,
invalid reaction fractions, invalid range scale-out relation, invalid BE
buffer, conflicting BE aliases, conflicting general aliases through the
canonical environment contract, and invalid contract mode. Message wording
may reflect canonical nested paths, but success/failure semantics match.

## 10. Secret-redaction proof

Profile generation rejects secret assignments. Source traces do not have a
value member. Parse errors omit raw values. Pydantic validation errors are
rendered with `include_input=False`. Parity rows replace both values with
`<redacted>`. CLI stdout and JSON tests inject a unique secret sentinel and
assert it is absent from both outputs and object representations.

## 11. Diagnostic CLI

The offline adapter is invoked explicitly:

```bash
cd algo-bot
python -m app.configuration.shadow_cli \
  --env-file ../.env \
  --report-summary
```

Optional flags are `--profile`, `--report-sources`, `--report-warnings`, and
`--json-output`. Output always starts with `NON-AUTHORITATIVE SHADOW LOAD`.
Default output contains counts and fingerprints, not effective values. The CLI
does not import application startup, connect to Redis/Postgres/Telegram/
cTrader, start tasks, remain running, or write back to `.env`.

## 12. Tests and unchanged baseline failures

All Phase 2C profile, source, resolver, compatibility, shadow, parity,
generation, CLI, and architecture tests pass. The complete configuration and
config-health selection reports 187 passed. Generator validation reports 437
catalog items and seven current artifacts. Four-fixture parity covers
1,264/1,264 direct comparisons, 16/16 derived comparisons, and complete
provenance for 1,264/1,264 legacy-field observations.

The full Python suite reports 1,521 passed and the same 13 failures reported by
PR #196; 74 targeted Phase 2C tests pass separately. The identical
scanner/worker/TradePlan subset remains 267 passed and 11 failed. Phase 2C does
not xfail, skip, weaken, or modify those trading tests. The unchanged failures
are six `test_auto_scalp_worker` expectations, one confluence-card expectation,
one strategy-match target-room expectation, one TradePlan stop-reason
expectation, and four worker-veto replay expectations.

`dotnet test` is attempted separately. Phase 2C changes no file under
`ctrader-engine`; an unavailable SDK is reported as unavailable, never as a
passing C# run.

## 13. Remaining unresolved behavior conflicts

Phase 2C deliberately preserves all documented cross-service default and
deployment conflicts, including direct-demo versus Compose-demo mapped-zone
behavior. It does not resolve warning-class catalog mismatches, service-scoped
required-input projections, the pre-existing Python behavior failures, or C#
SDK availability.

## 14. Phase 2D entry gate

Phase 2D is: **activate immutable root behind a generated read-only legacy
facade**.

It may begin only after all four 316/316 fixtures, all derived properties,
configuration tests, generated artifacts, redaction checks, provenance, and
compatibility coverage remain green; the Python failure set has not increased;
and C# runtime files remain unchanged.

Authoritative activation must not merge until the existing Python behavioral
suite is green or formally resolved, C# tests pass on a supported SDK, and a
tested rollback path to legacy `Settings` exists.
