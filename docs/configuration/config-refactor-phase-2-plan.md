# Configuration refactor — Phase 2 implementation plan

Baseline: Phase 1 catalog generated from
`76187deeb95f03189e790ffa5dd735b023839cdf`.

## Objective and guardrails

Phase 2 introduces one frozen, nested `ApexVoidConfig` and derives ENV aliases,
legacy access, documentation and Python/C# shared metadata from its field
metadata. It must preserve the current effective values in every supported
runtime fixture before any default or strategy decision is intentionally
changed.

Guardrails for every commit:

- no ENV rename/removal;
- no scanner, detector, worker, TradePlan or C# execution behavior change;
- no parameter tuning;
- no database configuration or hot reload;
- config loads once at startup;
- existing tests remain unchanged and green unless the commit adds new
  configuration-characterization tests;
- intentional resolution of mapped-zone, counter-bias, timeframe or
  range-scalp conflicts is deferred to separate behavior PRs.

## Commit sequence

### 1. `test(config): freeze legacy inventory and source precedence`

Add read-only characterization tests around the current code:

- introspect all 316 `Settings` fields, aliases, defaults and effective values;
- snapshot direct conservative, direct demo and root-Compose demo values;
- prove the 31 Compose-injected demo fields and two known divergences;
- snapshot the 126 C# AutoTrade/Feed bindings and direct logging bindings;
- assert the 30 known conflict rows from the Phase 1 catalog;
- run `docker compose config -q`.

No application import changes.

### 2. `feat(config): add metadata primitives and frozen domain model shells`

Add `ConfigOwner`, `ReloadPolicy`, `ConfigUnit`, `MismatchPolicy`,
`ConfigMetadata`, and `config_field`. Add empty/frozen nested model modules and
`ApexVoidConfig` composition. Keep legacy `Settings` as the only active loader.

Tests:

- model tree is frozen;
- metadata values serialize deterministically;
- numeric field metadata rejects missing units;
- secret/configurable/constant invariants.

### 3. `feat(config): encode canonical field catalog beside legacy loader`

Populate nested fields mechanically from
`config-catalog-phase-1.json`. Defaults and constraints must reproduce the
legacy model exactly. Do not load the new root in production yet.

Tests:

- every legacy field maps exactly once;
- every proposed path, canonical ENV and alias is unique;
- every alias has one owner;
- every catalog item has owner, unit, reload policy and risk metadata;
- no new flat `Settings` fields.

### 4. `build(config): generate catalog, legacy map and shared descriptor`

Traverse Pydantic metadata to generate:

- `contracts/configuration/config-catalog.generated.json`;
- `contracts/configuration/shared-config.generated.json`;
- the exact legacy-attribute-to-path map;
- generated documentation fragments;
- a C# metadata source/fixture for shared fields and constants.

CI regenerates into a temporary directory and fails on diff. Secret defaults
are always redacted.

### 5. `feat(config): add immutable profile documents in shadow mode`

Represent `conservative` and the exact 48-value `demo_eval` mapping as frozen
nested documents. Add a pure deep-merge function with this order:

```text
schema → profile → dotenv → process ENV → root validation
```

Run it only in tests/shadow startup diagnostics. Compare every value to the
legacy loader for direct and Compose fixtures. Preserve the two known Compose
divergences in the parity fixture; do not silently choose new values.

### 6. `feat(config): add deterministic ENV source and complete validation`

Add one Python source adapter that:

- resolves canonical/alias conflicts from generated metadata;
- gives process ENV precedence over dotenv;
- accepts every existing production alias;
- returns one raw nested mapping;
- validates the complete root once;
- redacts secret values in errors/health.

Remove no old loader code yet. AST tests forbid any new direct `os.getenv` or
`os.environ` call outside the adapter.

### 7. `feat(config): activate immutable root through legacy facade`

Construct `config = load_config()` once and expose
`settings = LegacySettingsFacade(config)`. The generated facade supports all
316 legacy attributes and existing compatibility properties. Unknown names
raise `AttributeError`; mutation raises `TypeError`.

Activation gate:

- old/new values equal in all golden fixtures;
- full Python suite green without changed behavioral assertions;
- Compose configuration parity green;
- secret-redaction tests green.

Keep the old model available only to parity tests for one migration window.

### 8. `refactor(config): migrate non-execution consumers by domain`

Use one commit per domain, in this order:

1. bootstrap/logging/delivery;
2. market data/scanner acquisition;
3. analysis and detector adapters;
4. strategy selection;
5. actionability;
6. lifecycle;
7. manual algo.

Each commit replaces `settings.flat_name` with `config.domain.path` only. Do
not combine the edit with renaming business objects, changing defaults or
removing fallback constants. Run the complete Python suite after every commit.

### 9. `refactor(config): generate and consume Python/C# shared contract metadata`

Add the generated C# descriptor and central `EnvironmentBinding`. Migrate C#
option parsing metadata without changing resolved values. Keep
`AutoTradeOptions` and `FeedOptions` public shape initially so execution call
sites do not move in the same commit.

Required parity assertions per shared field:

- canonical ENV and aliases;
- compatible type and identical unit;
- direct conservative/demo defaults;
- parser accepted values;
- validation/allowed values;
- fatal/warning/not-reported health policy;
- protocol constants and Redis stream/key values.

The existing runtime manifest stays operational throughout.

### 10. `refactor(config): migrate contract, execution and risk consumers`

Migrate the highest-risk Python and C# consumers last, one subdomain per
commit: instrument/versions/streams, entry, stops, targets, scaling/fill,
position management, sizing/exposure. TradePlan V7 payloads and all numeric
units must remain byte/value compatible.

Run after each subdomain:

- all Python tests;
- all C# tests;
- shared-contract generation/parity;
- Redis integration/fencing tests;
- Docker Compose config.

### 11. `refactor(config): remove duplicated registries and direct reads`

After all consumers use the root/facade:

- derive alias conflict checks from catalog metadata;
- derive config-health selection and mismatch policy;
- remove `_CANONICAL_ENV_NAMES`, `_PROFILE_DEFAULT_FIELDS` and duplicate legacy
  alias dictionaries;
- route broker/build/hostname/logging reads through the loader;
- replace one-sided `BARS_CHANNEL` and manual-command stream binding only with
  a generated compatibility path that preserves current values;
- retain deprecated ENV support and warnings.

Do not remove legacy aliases in Phase 2.

### 12. `docs(config): publish operator catalog and migration evidence`

Generate redacted operator documentation, default/profile matrices and a
deployment-key allowlist. Record parity evidence and deprecation warnings.
Keep Phase 1 documents as the audit baseline.

## Deferred behavior decisions

These require separate, explicitly approved PRs after the refactor:

- root Compose mapped-zone/market-map-guard `false` versus direct demo `true`;
- conservative counter-bias Python `true` versus C# `false`;
- cTrader H1 versus M30 feed default;
- authoritative range-scalp defaults;
- promoting auto-scalp constants to operator configuration;
- enabling any reload policy other than restart;
- removing any deprecated ENV alias.

## Completion gate

Phase 2 is complete only when:

- one metadata traversal produces catalog, facade, docs and shared descriptor;
- every legacy attribute and ENV/alias remains accepted exactly as before;
- every runtime fixture has field-by-field parity;
- no direct Python/C# environment reads remain outside binding modules;
- no separate canonical/profile/alias registry remains;
- Python and C# shared options/constants pass generated parity;
- secret values are absent from generated and health output;
- all existing Python/C#/Redis/Compose checks pass;
- no trading behavior assertion or strategy parameter changed.
