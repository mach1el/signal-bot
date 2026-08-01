# Configuration refactor Phase 2A — typed foundation

Status: implemented as an inactive, behavior-preserving foundation
Baseline: `4a977a7cebc971c59a67c0500d6f1af22ed1fab9` (merged PR #194)
Catalog version: `1` / `config-catalog-v1`

## 1. Scope and non-goals

Phase 2A freezes the active legacy loader, normalizes the complete Phase 1
catalog, introduces canonical metadata primitives and demonstrates frozen
grouped Pydantic models. The grouped model is deliberately not connected to
application startup.

This phase does not activate a new loader, add a facade, modify Compose or
`.env.example`, change profile semantics, rename an ENV, resolve an existing
default conflict, tune a strategy, migrate a scanner/worker consumer, add
database configuration or add hot reload.

`app.core.config.Settings` and the existing module-level `settings = Settings()`
remain the sole production configuration path.

## 2. Current baseline

Repository-wide searches and the characterization generator confirmed the
Phase 1 baseline still matches master:

| Inventory class | Verified count |
|---|---:|
| Total audited items | 437 |
| Python `Settings` fields | 316 |
| C#-only option rows | 47 |
| Environment/deployment-only items | 7 |
| Hardcoded config-like constants | 67 |
| Python/C# shared items | 95 |
| Verified conflict items | 30 |
| Fragmented-source items | 121 |

The legacy snapshot records every field's name, annotation, default,
validation-alias order, required state and current profile behavior. It also
records all 316 effective values under direct conservative, direct
`demo_eval`, root-Compose `demo_eval` and the test/conftest environment. All
secret values are represented only by `<redacted>`.

The C# snapshot freezes catalogued bindings, `AutoTradeOptions` and
`FeedOptions` ENV lists, normalized method hashes, the intentional
`ContractMode` constructor/`FromEnvironment` split, hardcoded streams and ten
protocol constants. It does not change C# loading.

## 3. Root model decision

The normalized root is:

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

Every shell inherits `FrozenConfigModel`, whose Pydantic configuration is
`ConfigDict(frozen=True, extra="forbid")`. Only representative leaf fields
are encoded in the shells during this phase. The complete 437-item design
remains in `config-catalog-phase-2a-normalized.json`; Phase 2A intentionally
does not duplicate all 316 live fields before loader/parity work begins.

## 4. Why `runtime` was added

Phase 1 incorrectly mixed operational switches with interpretation contracts.
The following stable paths now separate runtime policy from wire/broker
invariants:

| Legacy attribute | Phase 1 path | Normalized path |
|---|---|---|
| `auto_trade_profile` | `contract.profile` | `runtime.profile` |
| `auto_trade_enabled` | `contract.enabled` | `runtime.auto_trade.enabled` |
| `auto_trade_dry_run` | `contract.dry_run` | `runtime.auto_trade.dry_run` |
| `scanner_enabled` | `market_data.scanner_enabled` | `runtime.scanner.enabled` |
| `calendar_enabled` | `market_data.calendar_enabled` | `market_data.calendar.enabled` |
| `weekly_report_enabled` | `delivery.weekly_report_enabled` | `delivery.reports.weekly.enabled` |

`contract` is now reserved for versions, Redis keys/streams, instrument units,
broker identity, account capability requirements and compatibility mode.

## 5. Corrected cTrader ownership

cTrader connection and credential fields no longer appear under pure
analysis. They are grouped as:

```text
bootstrap.ctrader.connection.{host,port,request_timeout_seconds}
bootstrap.ctrader.credentials.{client_id,client_secret,access_token,
  refresh_token,account_id}
bootstrap.ctrader.token_rotation.{refresh_token_key,refresh_token_file,
  refresh_lead_days,check_interval_hours}

market_data.ctrader_feed.{symbol,timeframes,backfill_bars,bars_window_max,
  bars_channel,bar_quality_lookback_bars,health_file}
```

This is catalog ownership only. `FeedOptions.FromEnvironment()` remains
unchanged and active.

## 6. Unit normalization report

The deterministic review corrected 87 Phase 1 unit classifications. The
normalized catalog contains no numeric trading item with a `string` unit and
the validator enforces known suffixes.

| Item | Phase 1 unit | Normalized unit | Evidence |
|---|---|---|---|
| `swing_fractal_n` | fraction | bars | Fractal window counts bars |
| `track_interval` | count | seconds | Watcher sleep/poll interval |
| `trend_atr_baseline_bars` | atr | bars | ATR baseline window length |
| `trend_atr_expansion` | atr | multiplier | Multiplies the ATR baseline |
| `CTRADER_REQUEST_TIMEOUT` | count | seconds | `TimeSpan.FromSeconds` |
| `CTRADER_TOKEN_REFRESH_LEAD_DAYS` | count | days | `TimeSpan.FromDays` |
| `CTRADER_PORT` | count | port | Network endpoint port |
| `AUTO_TRADE_ADD_PULLBACK_MIN_RETRACE` | count | fraction | Fractional retracement bound |
| `AUTO_TRADE_ADD_PULLBACK_MAX_RETRACE` | count | fraction | Fractional retracement bound |
| `AUTO_TRADE_PIP_VALUE_PER_LOT` | pips | money_per_pip_per_lot | Sizing conversion value |
| `scanner_level_bucket` | count | pips | `_level_bucket()` multiplies it by symbol pip size |
| `map_fallback_radius` | price | price | Compared directly with absolute price distance |
| `map_scalp_radius` | price | price | Compared directly with absolute price distance |

Additional semantic units include `version`, `score`, `utc_hour`,
`day_of_week`, `port`, `multiplier`, `identifier`, `path`, `url` and
`contract_units_per_lot`. Every correction is machine-readable in the item's
`normalization_notes`.

## 7. Path normalization report

All 437 paths are unique. Of these, 321 changed from Phase 1 because flat
two-component paths were expanded into domain/subdomain/purpose paths.
Representative corrections include:

| Phase 1 path | Normalized path |
|---|---|
| `strategies.trend_allow_chase` | `strategies.trend.allow_chase` |
| `strategies.trend_min_bos` | `strategies.trend.minimum_bos` |
| `strategies.trend_atr_expansion` | `strategies.trend.atr_expansion_multiplier` |
| `analysis.tl_min_touches` | `analysis.trendlines.minimum_touches` |
| `analysis.tl_tol_atr` | `analysis.trendlines.tolerance_atr` |
| `analysis.xau_major_zone_max_width_price` | `analysis.zones.symbol_contract.major_maximum_width_price` |
| `actionability.scanner_gate_max_source_touches` | `actionability.structural_anchor.maximum_source_touches` |
| `actionability.scanner_gate_counter_bias_min_confluence` | `actionability.counter_bias.minimum_confluence` |
| `lifecycle.candidate_max_age_seconds` | `lifecycle.candidate.execution_maximum_age_seconds` |

Each item carries `catalog_version=1` and
`introduced_in="config-catalog-v1"`. Duplicate paths fail validation. The one
deprecated orphan (`SCANNER_CONFLICT_OVERLAP`) has an explicit terminal
deprecation reason; paths cannot silently be reused.

## 8. Constants and configurability classification

| Kind | Count | Configurable | ENV/aliases | Reload policy |
|---|---:|---|---|---|
| Operator configurable | 370 | yes | existing bindings preserved | existing proposed policy |
| Protocol constant | 10 | no | none | `code_release` |
| Algorithm constant | 57 | no | none | `code_release` |

The protocol set contains the shared TradePlan/entry/stop/config-manifest
versions and Redis contract keys. The algorithm set contains detector scoring,
star thresholds, dedup tolerances, internal lifecycle limits and unapproved
auto-scalp gates. Phase 2A does not promote a discovered hardcode into ENV.

## 9. Secret classification

Nine items are secrets:

- Telegram and scanner Telegram bot tokens;
- database password and DSN;
- cTrader client secret, access token and refresh token;
- Anthropic and Tiingo API keys.

The normalized JSON stores `<redacted>` for every secret default. The
characterization fixture also redacts effective values. Metadata contains only
classification and binding names, never credentials.

## 10. Catalog metadata schema

`ConfigMetadata` is a frozen object stored under
`Field.json_schema_extra["apexvoid_config"]`. It records legacy/canonical names,
aliases, owner, reload policy, unit, risk, kind flags, secret status,
cross-service/mismatch policy, documentation and path-stability metadata.

`config_field()` declares Pydantic field behavior and metadata together.
`iter_config_metadata()` recursively traverses model fields; there is no second
handwritten registry for model-shell metadata.

Enums introduced in Phase 2A are `ConfigOwner`, `ReloadPolicy`, `ConfigUnit`,
`MismatchPolicy`, `RiskClassification` and `ConfigKind`.

## 11. Catalog validator rules

The validator CLI checks:

- unique item IDs, legacy attributes, canonical ENV ownership and paths;
- unique aliases and no alias/canonical collision;
- valid owner, reload, unit, risk, kind and mismatch-policy enums;
- exact configurable/protocol/algorithm flag consistency;
- no ENV or alias on constants and `code_release` constant policy;
- numeric trading units and suffix/unit consistency;
- all known secrets classified and defaults redacted;
- shared fields have an explicit mismatch policy;
- deprecated paths have replacement or terminal disposition;
- catalog version/introduction metadata is present;
- runtime controls are outside `contract`;
- cTrader bootstrap/credential fields are outside `analysis`.

Run from `algo-bot/`:

```bash
python -m app.configuration.catalog_validation \
  docs/configuration/config-catalog-phase-2a-normalized.json
```

The command exits non-zero and reports all detected violations.

## 12. Characterization test results

Phase 2A adds characterization for:

- 316-field legacy schema metadata;
- all 316 effective values in four runtime fixtures;
- all 30 known conflict item IDs;
- the two direct-demo/root-Compose mapped-zone divergences;
- C# catalog rows, ENV bindings, `FromEnvironment` bodies, constructor split,
  hardcoded streams and protocol constants;
- normalized catalog integrity, classification, redaction and units;
- frozen/extra-forbidden grouped shells and recursive metadata traversal.

The fixture is deterministic and secret-safe. Existing behavior tests are not
weakened or removed.

## 13. Remaining open decisions

Phase 2A intentionally leaves these behavioral decisions unresolved:

1. Direct `demo_eval` enables mapped-zone/market-map guard while Compose
   explicitly disables them.
2. Conservative counter-bias defaults differ between Python and C#.
3. cTrader timeframe defaults differ between code and deployment examples.
4. Seven range-scalp mirror defaults still disagree.
5. One-sided configurability of bars/manual-command streams remains.
6. Python-owned strategy policy versus C#-validated plan fields needs a final
   ownership matrix before generated bindings.

Resolving any item above requires a separate behavior decision and is not part
of taxonomy normalization.

## 14. Phase 2B entry criteria

Phase 2B may begin only after:

- Phase 2A catalog/characterization tests are green in CI;
- operator decisions are recorded for any intended parity change;
- every one of the 316 legacy fields has encoded nested metadata or an
  explicit constant/internal disposition;
- a shadow loader can reproduce all four characterization fixtures exactly;
- generated Python/C# shared metadata matches current fatal/warning policy;
- secret redaction is proven for generated docs and health output;
- activation remains separately reviewable from model/catalog construction.

### Normalization summary

| Issue | Phase 1 value/path | Normalized value/path | Evidence | Behavior impact |
|---|---|---|---|---|
| Runtime switches in contract | `contract.{profile,enabled,dry_run}` | `runtime.profile`, `runtime.auto_trade.*` | Operational switches are not wire invariants | none |
| cTrader credentials in analysis | `analysis.ctrader_*` | `bootstrap.ctrader.*` | `FeedOptions` connection/auth consumers | none |
| Flat strategy paths | `strategies.trend_*` | `strategies.trend.*` | Field semantics and consumers | none |
| Flat trendline paths | `analysis.tl_*` | `analysis.trendlines.*` | Trendline detector consumers | none |
| Candidate age ambiguity | `lifecycle.candidate_max_age_seconds` | `lifecycle.candidate.execution_maximum_age_seconds` | Executor freshness vs storage TTL | none |
| Misclassified scanner bucket | count | pips | `_level_bucket`: bucket × pip size | none |
| Hardcoded protocol values | generic hardcode | non-configurable protocol constant | Python/C# version/key parity | none |
| Internal detector gates | generic hardcode | non-configurable algorithm constant | No operator approval to expose | none |
| Secret defaults | tracked placeholders/defaults | `<redacted>` | Secret classification rules | none |

No row in this table changes a loaded value or runtime consumer.
