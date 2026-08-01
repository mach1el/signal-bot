# Configuration refactor Phase 2B — complete typed catalog

## 1. Scope and non-goals

Phase 2B replaces the representative Phase 2A shells with a complete, static,
metadata-backed `ApexVoidConfig` declaration and deterministic contract
artifacts. The grouped schema is inactive. This phase does not introduce an ENV
loader, legacy facade, database configuration, hot reload, scanner/worker
migration, C# option binding change, profile behavior change, or trading tune.

## 2. Baseline and verified counts

The implementation starts from master `7a1a4473ba6bef7c3a1d2fb2721caca9728fc871`
(merged PR #195). Repository inventory and typed traversal agree:

| Classification | Count |
|---|---:|
| Audited items / typed leaves | 437 |
| Legacy Python Settings fields | 316 |
| Configurable items | 370 |
| Protocol constants | 10 |
| Algorithm constants | 57 |
| Python/C# shared items | 95 |
| Secret items | 9 |
| Preserved warning/conflict rows | 30 |

One Phase 2A migration-oracle error was corrected using the higher-priority
legacy characterization: `telegram_owner_id` has schema default `None`, not the
fixture-like string `"123456789"`. This changes no active runtime value.

## 3. Final grouped hierarchy

```text
ApexVoidConfig
├── bootstrap
├── runtime
├── market_data
├── analysis
├── strategies
├── actionability
├── contract
├── execution
├── risk
├── lifecycle
├── delivery
└── manual_algo
```

Each domain uses focused submodels such as `execution.entry`,
`execution.targeting`, `execution.stops`, `execution.reaction`,
`strategies.reaction`, `actionability.structural_anchor`, and
`actionability.zone_reconciliation`. All models inherit frozen,
extra-forbidden Pydantic configuration.

## 4. Full model encoding approach

All 437 leaves are static Python declarations under
`app/configuration/models`. Each declaration calls `config_field()` beside its
observable type/default and canonical metadata. Neither historical JSON catalog
is read to create models or generate artifacts. Phase 1 and Phase 2A JSON remain
migration evidence used only by tests.

The two owner-native list fields are represented as actual lists. Legacy CSV
fields such as `AUTO_TRADE_TARGET_PLANS_PIPS` remain strings, preserving the
active Python type contract.

## 5. Requiredness strategy

Requiredness follows the active owner. Telegram token/channel fields remain
required. cTrader client ID, client secret, access token, refresh token and
account ID remain required. Optional Python secrets remain optional strings.
Tests construct a full safe fixture with sentinel credentials; no real secret
is present in source snapshots or generated output.

## 6. Owner-specific default strategy

`ConfigMetadata.default_contexts` stores immutable, ordered evidence for:

- `python_schema`;
- `ctrader_from_environment`;
- `ctrader_constructor`.

The model field owns the appropriate schema default. Deployment/Compose and
profile values remain fixture evidence, not invented schema defaults. The
`AUTO_TRADE_CONTRACT_MODE` descriptor therefore retains Python/C# ENV
`v7_only` alongside the C# record-constructor value `legacy_v6`.

## 7. Metadata contract changes

Phase 2B adds stable `item_id`, actual `runtime_reload_policy`, context defaults,
allowed values, validation summaries, evidence notes and forwarding for real
Pydantic constraints (`ge`, `gt`, `le`, `lt`, lengths and patterns). Proposed
semantic reload policy remains distinct from the current actual policy:
configurable values still require restart and constants require a code release.

## 8. Constraint and validator coverage

Pure value validation is placed in the narrowest submodel: XAU zone ordering,
lookback minimums, entry/zone positivity, range scale-out relationships,
reaction fraction sums, BE bounds and the established enum-like policies.
Profile mutation and raw alias-source conflict detection are deferred with an
explicit reason. The complete clause ledger is in
[`config-validation-coverage-phase-2b.md`](config-validation-coverage-phase-2b.md).

## 9. Generated artifacts

`python -m app.configuration.generate --write` deterministically writes:

- `contracts/configuration/config-catalog.generated.json`;
- `contracts/configuration/legacy-map.generated.json`;
- `contracts/configuration/legacy-derived.generated.json`;
- `contracts/configuration/shared-config.generated.json`;
- `contracts/configuration/protocol-constants.generated.json`;
- `docs/configuration/config-catalog.generated.md`.

Artifacts contain catalog version and a SHA-256 fingerprint of typed traversal,
but no timestamp or machine path. `--check` regenerates in memory and returns
non-zero with the drifting paths when checked-in output differs.

## 10. Legacy direct-map strategy

The generated direct map contains exactly the 316 Pydantic Settings fields,
each mapped once to a canonical path. It is derived from leaf metadata and is
not loaded by production. No `LegacySettingsFacade` is introduced in Phase 2B.

## 11. Derived-property strategy

Computed compatibility properties are deliberately separate from the direct
field map. The generated descriptor records four properties:
`telegram_chat_id`, `signal_vip_channel_id`, `xau_vip_channel_id`, and
`xau_public_channel_id`, including source, transformation and return type.

## 12. Shared Python/C# descriptor

The shared descriptor contains exactly 95 items. It includes canonical ENV,
aliases, Python/C# types, unit, all available default contexts, allowed values,
validation summary, mismatch policy and secret/kind classification. Thirty
known warning rows remain explicit. Evidence notes additionally retain:

- counter-bias Python/C# default disagreement;
- mapped-zone and market-map direct-demo versus root-Compose divergence;
- timeframe deployment divergence;
- contract constructor versus `FromEnvironment` behavior;
- one-sided bars-channel and manual-command-stream configurability.

No winner is selected in this phase.

## 13. Secret-redaction proof

Traversal always serializes a secret default as `<redacted>`. Metadata rejects
non-redacted secret context defaults. Tests inspect all six generated outputs,
verify all nine secret rows are redacted, and reject both fixture sentinels and
the local development database DSN. Generated Markdown uses the same redacted
catalog entries.

## 14. Remaining unresolved behavior conflicts

The 30 known mismatch-policy warnings remain unchanged. Compose/profile
divergences remain in characterization and descriptor evidence. C# constructor
and `FromEnvironment` differences remain distinct. No default conflict is
resolved and no profile precedence is altered.

## 15. Phase 2C entry criteria

Phase 2C may begin after this catalog and generator remain green. Its scope is:

1. immutable profile documents;
2. deterministic source resolution with alias/source provenance;
3. shadow configuration loading;
4. full old/new fixture parity.

Phase 2C must still remain non-authoritative until shadow parity is proven.
Production activation is a later, explicit decision.

## Runtime safety statement

`app.core.config.Settings` and `settings = Settings()` remain the sole runtime
loader. Application startup does not import `ApexVoidConfig`; no production
module reads generated artifacts. ENV names, Compose values, C# bindings,
scanner/worker/TradePlan behavior, strategy parameters and risk parameters are
unchanged.
