# Configuration refactor — Phase 1 audit

Status: analysis and design only
Repository: `st-mich43l/apexvoid-trading-bot`
Audited commit: `76187deeb95f03189e790ffa5dd735b023839cdf`
Latest merged PR at that commit: `#193`, `feat/scalp-bypass-opposing-gates`
Baseline working tree: clean; local `master` matched `origin/master` after fetch
Audit date: 2026-08-01

## 1. Executive summary

The repository has one large Python `Settings` schema, but it does not have one
configuration source of truth. Metadata and values are independently repeated
in Pydantic defaults, the mutating `demo_eval` validator, Compose interpolation,
the production Jinja template, `.env.example`, Python mirror dataclasses/module
constants, `environment_options.py`, `config_health.py`, C# option fallbacks,
and C# hardcoded streams/constants.

The complete application/runtime inventory contains **437 unique items**:

- 316 Python `Settings` fields;
- 47 C# option rows without a Python `Settings` field;
- 7 environment/deployment-only items;
- 67 config-like hardcoded constants, including protocol versions and shared
  Redis keys/streams that must remain constants or be explicitly promoted;
- 95 Python/C# shared items: 86 environment/option bindings and 9 shared
  constants;
- 21 deprecated or deployment aliases;
- 30 items with a verified value, alias, or default conflict;
- 121 items that demonstrate source fragmentation even when their current
  values happen to agree.

The highest-risk verified behavior is profile/Compose interaction. The Python
`demo_eval` document declares 48 field values. Root Compose injects 31 of those
as real process-environment values, so `model_fields_set` treats them as
explicit and the validator will not apply its profile value. Most injected
values currently duplicate the profile, masking the architectural problem.
Two do not: direct `demo_eval` resolves `auto_trade_mapped_zone_enabled=true`
and `auto_trade_market_map_guard_enabled=true`, while root Compose resolves both
to `false`.

Production values are not inferable from this repository alone. The deploy
workflow delegates to `ansible-library`, and
`deployment-template/docker-compose.yml.j2` renders a vaulted environment.
This audit did not read external vaults or secrets. Accordingly, every catalog
entry records the tracked defaults and reports the production value as runtime
or Ansible-vault dependent instead of presenting an unverified value.

No production code, scanner, detector, worker, TradePlan, C# execution logic,
ENV name, or strategy parameter was changed in Phase 1.

### Findings by priority

**P0 — environment-dependent profile semantics.** `demo_eval` is not an
immutable input layer. It runs after source loading and mutates only fields not
present in `model_fields_set`. Compose therefore changes the meaning of the
same profile name. The current visible divergence is mapped-zone and
market-map-guard enablement.

**P0 — cross-service behavior can depend on deployment masking.** Direct
conservative defaults disagree for `AUTO_TRADE_ALLOW_COUNTER_BIAS` (Python
`true`, C# `false`). Root Compose currently injects `true` into both services,
but direct execution and alternative deployment paths do not have parity.

**P1 — duplicated analysis defaults drift.** Seven range-scalp fields disagree
between root `Settings`, `AnalysisSettings`, `DetectorSettings`, and module
fallbacks. Runtime call sites often pass the root values, but tests and direct
construction can observe the mirrors, so these are not harmless comments.

**P1 — shared bindings are not always shared configuration.** Python hardcodes
`bars:new` while C# reads `BARS_CHANNEL`; C# hardcodes
`manual_trade:commands` while Python exposes `MANUAL_TRADE_COMMAND_STREAM`.
Protocol versions and health Redis keys are repeated constants in both
languages without one generated parity contract.

**P1 — the production template is another profile engine.** Its Jinja
variables and dictionaries recalculate profile-dependent defaults separately
from both Python and C#. Root Compose, production Compose, direct Python, and
direct C# therefore have four independently maintained default paths.

**P2 — registry and documentation drift.** `environment_options.py` covers
only part of `AliasChoices`; `config_health.py` maintains separate canonical,
profile, and legacy sets; `.env.example` contains orphan
`SCANNER_CONFLICT_OVERLAP`; `.env.example`/production default
`CTRADER_TIMEFRAMES=M1,M5,M15,M30` while `FeedOptions` directly defaults to
`M1,M5,M15,H1`.

## 2. Current configuration loading flow

### Python algo-bot

1. Importing `app.core.config` imports `environment_options.py`.
2. `raw_environment()` reads `.env` with `dotenv_values`, then overlays
   `os.environ`.
3. `RESOLVED_ENVIRONMENT_OPTIONS` resolves a partial alias registry at module
   import. Conflicting values among covered canonical names and aliases fail
   here.
4. `Settings()` uses Pydantic settings sources. The effective source order is
   init keyword arguments, process environment, `.env`, secrets source, then
   field defaults. `AliasChoices` selects its first present name.
5. The `after` model validator normalizes values, validates selected
   cross-field constraints, and calls `setattr()` for profile defaults only
   when a field is absent from `model_fields_set`.
6. A module singleton, `settings = Settings()`, is imported throughout the
   process. There is no reload path.
7. A few consumers bypass the root loader: `config_health.py` reads broker,
   build metadata and aliases from `os.getenv`; `strategy_match_ready.py`
   reads `HOSTNAME`; `logging_setup.py` reads `LOG_DIR`; the BE compatibility
   check reads `os.environ` directly.

`environment_options.py` is not a second settings source for values consumed by
the app; it is a preflight/conflict detector. Its registry nevertheless must
stay manually synchronized with `AliasChoices` and config health.

### C# cTrader engine

1. `FeedOptions.FromEnvironment()` and
   `AutoTradeOptions.FromEnvironment()` independently read process ENV.
2. C# has no dotenv loader. `.env` works only when Compose injects it.
3. `AutoTradeOptions.FromEnvironment()` selects `conservative` by default,
   computes profile-sensitive fallback expressions, and then applies explicit
   process ENV through `EnvironmentResolver`.
4. `AutoTradeOptions.Validate()` applies execution-safety checks. The bare
   record constructor has additional defaults used heavily by tests; notably
   `ContractMode="legacy_v6"`, while `FromEnvironment()` defaults to
   `v7_only`.
5. Python and C# publish separate Redis manifests. `compare_manifests()` has a
   manually maintained fatal/warning field list; unlisted shared values are
   invisible to the handshake.

### Docker Compose and production deployment

Root Compose has both `env_file: .env` and an `environment:` block. Compose
interpolation resolves `${VAR:-default}` from the invoking shell/project
`.env`; the resulting `environment:` values override same-name values coming
from `env_file`. Inside either process, all values appear as ordinary explicit
environment variables.

The production template does not use the root anchor. Ansible constructs
`resolved_bot_env` from fixed execution defaults, demo defaults, logging
defaults, and the vaulted dictionary, then emits that dictionary for the bot.
It emits an independently enumerated environment for the C# service. Values in
the vault override the Jinja dictionaries. This makes the production template
a separate loader/profile implementation.

## 3. Source precedence by runtime mode

### A. Source-of-truth table

| Source | Scope | Precedence | Runtime mode | Problems |
|---|---|---:|---|---|
| Pydantic field default | Python | Lowest input layer | Direct Python/tests/Compose | Repeated by mirrors, Compose, docs and C# |
| C# record constructor default | C# tests/direct construction | Lowest constructor layer | Mainly tests | Can deliberately differ from `FromEnvironment` (`ContractMode`) |
| Python/C# profile fallback | Auto trade | Above schema only when field is absent | Direct processes | Python mutates after load; C# computes during load |
| `.env` via Pydantic | Python | Above profile intent, below process ENV | Direct Python | Values become explicit and block Python profile mutation |
| Process environment | Both | Highest external runtime input | All | Direct reads bypass validation/catalog |
| Root Compose `${VAR:-default}` | Both | Produces process ENV; `environment` beats `env_file` | Local Compose/CI config | 93 bot variables become explicit; root defaults to `demo_eval` |
| Production Jinja dictionaries | Both | Below vaulted dict, then emitted as process ENV | VPS deployment | Reimplements profile/default logic outside both services |
| `.env.example` | Operator documentation | No runtime authority | Setup/docs | Can drift; one orphan field exists |
| `AliasChoices` | Python aliases | First present alias | Python | Separate conflict registry required to detect shadowed disagreement |
| `ENVIRONMENT_OPTION_CONTRACTS` | Python preflight | Import-time conflict check | Python | Partial duplicate alias catalog |
| `config_health.py` sets | Shared handshake | Post-load comparison | Both services | Separate canonical/profile/fatal/warning registries |
| C# resolver calls | C# | Process ENV over profile/application fallback | Direct/Compose | Independently typed/defaulted/validated |
| Module/dataclass constants | Python/C# subdomains | Fallback or direct behavior | Direct calls/tests/runtime | Some duplicate and disagree with root settings |

### Effective precedence by mode

- **Direct Python:** init kwargs → process ENV → `.env` → secrets source →
  schema default; then the validator fills only profile fields not represented
  in `model_fields_set`, followed by complete model-validator checks.
- **Python tests:** `conftest.py` seeds process ENV before application imports.
  Tests using `Settings(_env_file=None)` remove dotenv but retain process ENV;
  tests constructing analysis/detector dataclasses may use their independent
  defaults.
- **Direct C#:** process ENV → profile-sensitive fallback → application
  fallback. Direct `new AutoTradeOptions(...)` uses record defaults and can
  bypass `FromEnvironment` semantics until `Validate()` is invoked.
- **Root Compose:** invoking-shell value → project `.env` value → `${...:-...}`
  default; resolved `environment:` → container process ENV, overriding
  `env_file`. Pydantic/C# then see an explicit value.
- **Production Compose:** Ansible-vault value → Jinja demo/execution/logging
  default → emitted process ENV. The exact vault-selected value is external to
  this repository.
- **Conservative:** Python has no immutable conservative document; schema
  defaults are the profile, with a conditional structural-guard normalization.
  C# has its own conservative fallbacks.
- **Demo evaluation:** Python declares 48 overrides, C# implements a smaller
  overlapping set as ternary fallbacks, and both Compose definitions pre-resolve
  a subset into explicit ENV.

### Proof of the Compose/profile concern

The audit instantiated the real Python loader three times with
`_env_file=None`: minimal `conservative`, minimal `demo_eval`, and the exact bot
environment returned by `docker compose config --format json`. It did not
simulate the validator.

- `Settings` fields: 316.
- Root Compose bot ENV entries: 93.
- `demo_eval` document entries: 48.
- Profile entries also injected by root Compose: 31.
- Declared demo values already equal to schema defaults: 23.
- Effective direct conservative/demo differences: 26, including the selected
  profile field itself.
- Direct-demo versus Compose-demo differences: exactly 2:
  `auto_trade_mapped_zone_enabled` and
  `auto_trade_market_map_guard_enabled`, both `true` direct and `false` through
  root Compose.

This proves that profile semantics are environment-dependent. Matching values
for the other 29 injected profile fields are accidental parity, not a reliable
precedence model.

## 4. Complete field inventory

The machine-readable source is
[`config-catalog-phase-1.json`](config-catalog-phase-1.json). It contains type,
unit, secrecy, aliases, all tracked defaults, effective direct/profile/Compose
values, consumers, validation, ownership, proposed domain/path/reload policy,
risk, duplicate definitions, issues, and shared-contract parity metadata for
every item.

Inventory rules used:

- each `Settings` field is one item; mirror dataclass/module defaults are
  attached as duplicate definitions rather than counted again;
- each C# resolver/feed/log option absent from Python is one item;
- each direct/orphan environment item is one item;
- config-like numeric thresholds, timeframes, streams, Redis contract keys and
  version constants not represented by a field are one item;
- state-machine enum labels, DTO payload members and serialization literals are
  protocol implementation, not operator configuration, and were excluded;
- current runtime reload policy is restart/code redeploy for every item;
  `reload_policy` is the proposed future policy only.

There are zero duplicate canonical ENV assignments, zero deprecated-alias
collisions, and zero proposed-path collisions in the catalog. Every numeric
item has non-empty unit metadata.

### B. Field inventory table

| Legacy attribute | Canonical ENV | Python default | Compose default | C# default | Proposed path | Owner | Unit | Risk |
|---|---|---|---|---|---|---|---|---|
| — | AUTO_TRADE_ADD_COOLDOWN_BARS | — | — | 3 | lifecycle.add_cooldown_bars | ctrader | bars | lifecycle |
| — | AUTO_TRADE_ADD_LEVEL_BUFFER_ATR | — | — | 1 | execution.add_level_buffer_atr | ctrader | atr | execution safety |
| — | AUTO_TRADE_ADD_MAX_AGE_BARS | — | — | 3 | lifecycle.add_max_age_bars | ctrader | bars | lifecycle |
| — | AUTO_TRADE_ADD_MAX_GROUP_RISK_PCT | — | — | 3.0 | risk.add_max_group_risk_pct | ctrader | percent | broker/account safety |
| — | AUTO_TRADE_ADD_PULLBACK_ENABLED | — | — | False | execution.add_pullback_enabled | ctrader | boolean | execution safety |
| — | AUTO_TRADE_ADD_PULLBACK_MAX_RETRACE | — | — | 0.7 | execution.add_pullback_max_retrace | ctrader | count | execution safety |
| — | AUTO_TRADE_ADD_PULLBACK_MIN_RETRACE | — | — | 0.2 | execution.add_pullback_min_retrace | ctrader | count | execution safety |
| — | AUTO_TRADE_ADD_REQUIRE_RISK_FREE | — | — | False | risk.add_require_risk_free | ctrader | boolean | broker/account safety |
| — | AUTO_TRADE_BOX_MIN_RR | — | — | 1.25 | execution.box_min_rr | ctrader | count | execution safety |
| — | AUTO_TRADE_BROKER_ABSENCE_CONFIRMATIONS | — | — | 2 | execution.broker_absence_confirmations | ctrader | count | execution safety |
| — | AUTO_TRADE_BROKER_ABSENCE_RECHECK_SECONDS | — | — | 3 | lifecycle.broker_absence_recheck_seconds | ctrader | seconds | lifecycle |
| — | AUTO_TRADE_BROKER_RECOVERY_TIMEOUT_SECONDS | — | — | 30 | lifecycle.broker_recovery_timeout_seconds | ctrader | seconds | lifecycle |
| — | AUTO_TRADE_EXPECTED_BROKER | — | — | fpmarkets | contract.account.expected_broker | shared | string | cross-service contract |
| — | AUTO_TRADE_FLIP_CONFIRM_TIMEOUT_SECONDS | — | — | 30 | lifecycle.flip_confirm_timeout_seconds | ctrader | seconds | lifecycle |
| — | AUTO_TRADE_FLIP_EXIT_BUFFER_PIPS | — | — | 10 | execution.flip_exit_buffer_pips | ctrader | pips | execution safety |
| — | AUTO_TRADE_LABEL | — | — | apexvoid-auto | execution.label | ctrader | string | execution safety |
| — | AUTO_TRADE_MAX_SPREAD_PIPS | — | — | 5 | execution.max_spread_pips | ctrader | pips | execution safety |
| — | AUTO_TRADE_PIP_VALUE_PER_LOT | — | — | 10 | execution.pip_value_per_lot | ctrader | pips | execution safety |
| — | AUTO_TRADE_POLL_MS | — | — | 1000 | execution.poll_ms | ctrader | milliseconds | execution safety |
| — | AUTO_TRADE_POSITION_MISSING_CONFIRMATIONS | — | — | 2 | lifecycle.position_missing_confirmations | ctrader | count | lifecycle |
| — | AUTO_TRADE_POSITION_MISSING_RECHECK_SECONDS | — | — | 3 | lifecycle.position_missing_recheck_seconds | ctrader | seconds | lifecycle |
| — | AUTO_TRADE_REQUIRE_DEMO_ONLY_TOKEN | — | — | False | execution.require_demo_only_token | ctrader | boolean | execution safety |
| — | AUTO_TRADE_RISK_PCT | — | — | 2 | risk.risk_pct | ctrader | percent | broker/account safety |
| — | AUTO_TRADE_TP_WEIGHTS | — | — | 20,20,20,20,20 | execution.tp_weights | ctrader | count | execution safety |
| — | AUTO_TRADE_ZONE_COOLDOWN_MINUTES | — | — | 60 | lifecycle.zone_cooldown_minutes | ctrader | minutes | lifecycle |
| — | AUTO_TRADE_ZONE_FILL_MIN_LOTS | — | — | 0.09 | execution.zone_fill_min_lots | ctrader | lots | execution safety |
| — | AUTO_TRADE_ZONE_FILL_TTL_BARS | — | — | 3 | lifecycle.zone_fill_ttl_bars | ctrader | bars | lifecycle |
| — | BARS_CHANNEL | bars:new | — | bars:new | analysis.bars_channel | shared | string | infrastructure |
| — | BARS_WINDOW_MAX | — | — | 1500 | analysis.bars_window_max | ctrader | bars | infrastructure |
| — | BAR_QUALITY_LOOKBACK | — | — | 6 | analysis.bar_quality_lookback | ctrader | bars | infrastructure |
| — | CTRADER_ACCESS_TOKEN | — | — | <required> | analysis.ctrader_access_token | ctrader | string | broker/account safety |
| — | CTRADER_ACCOUNT_ID | — | — | <required> | analysis.ctrader_account_id | ctrader | count | infrastructure |
| — | CTRADER_BACKFILL_BARS | — | — | 1500 | analysis.ctrader_backfill_bars | ctrader | bars | infrastructure |
| — | CTRADER_CLIENT_ID | — | — | <required> | analysis.ctrader_client_id | ctrader | string | infrastructure |
| — | CTRADER_CLIENT_SECRET | — | — | <required> | analysis.ctrader_client_secret | ctrader | string | broker/account safety |
| — | CTRADER_HOST | — | — | demo.ctraderapi.com | analysis.ctrader_host | ctrader | string | infrastructure |
| — | CTRADER_PORT | — | — | 5035 | analysis.ctrader_port | ctrader | count | infrastructure |
| — | CTRADER_REFRESH_TOKEN | — | — | <required> | analysis.ctrader_refresh_token | ctrader | string | broker/account safety |
| — | CTRADER_REFRESH_TOKEN_FILE | — | — | /var/lib/apexvoid/ctrader-token.json | analysis.ctrader_refresh_token_file | ctrader | string | infrastructure |
| — | CTRADER_REFRESH_TOKEN_KEY | — | — | ctrader:refresh_token | analysis.ctrader_refresh_token_key | ctrader | string | infrastructure |
| — | CTRADER_REQUEST_TIMEOUT | — | — | 30 | analysis.ctrader_request_timeout | ctrader | count | infrastructure |
| — | CTRADER_SYMBOL | — | — | XAUUSD | analysis.ctrader_symbol | ctrader | string | infrastructure |
| — | CTRADER_TIMEFRAMES | — | — | M1,M5,M15,H1 | analysis.ctrader_timeframes | ctrader | string | infrastructure |
| — | CTRADER_TOKEN_CHECK_INTERVAL_HOURS | — | — | 6 | analysis.ctrader_token_check_interval_hours | ctrader | hours | infrastructure |
| — | CTRADER_TOKEN_REFRESH_LEAD_DAYS | — | — | 5 | analysis.ctrader_token_refresh_lead_days | ctrader | count | infrastructure |
| — | HEALTH_FILE | — | — | /tmp/ctrader-feed.heartbeat | analysis.health_file | ctrader | string | infrastructure |
| — | LOG_FILE_NAME | — | ctrader-engine.log | ctrader-engine.log | analysis.log_file_name | ctrader | string | infrastructure |
| — | GIT_SHA | unknown | — | unknown | bootstrap.git_sha | shared | string | infrastructure |
| — | HOSTNAME | algo-worker | — | — | bootstrap.hostname | python | string | infrastructure |
| — | POSTGRES_DB | signals | signals | — | bootstrap.postgres_db | python | string | infrastructure |
| — | POSTGRES_PASSWORD | apexvoid | apexvoid | — | bootstrap.postgres_password | python | string | infrastructure |
| — | POSTGRES_USER | apexvoid | apexvoid | — | bootstrap.postgres_user | python | string | infrastructure |
| — | SCANNER_CONFLICT_OVERLAP | 0.5 | — | — | analysis.scanner_conflict_overlap | python | fraction | analysis behavior |
| — | SERVICE_VERSION | dev | — | — | bootstrap.service_version | python | string | infrastructure |
| — | — | 1.5 | — | — | analysis.detectors.scoring.coil | python | score | analysis behavior |
| — | — | 1.0 | — | — | analysis.detectors.reaction.maximum_distance_atr | python | atr | analysis behavior |
| — | — | 12.0 | — | — | analysis.detectors.star_thresholds.three | python | score | analysis behavior |
| — | — | 8.0 | — | — | analysis.detectors.star_thresholds.two | python | score | analysis behavior |
| — | — | 0.6 | — | — | analysis.displacement.body_fraction | python | fraction | analysis behavior |
| — | — | 1.0 | — | — | analysis.displacement.minimum_range_atr | python | atr | analysis behavior |
| — | — | 0.1 | — | — | analysis.market_map.session_band_atr | python | atr | analysis behavior |
| — | — | 0.2 | — | — | analysis.trendlines.dedup_slope_percent | python | percent | analysis behavior |
| — | — | 0.5 | — | — | analysis.trendlines.dedup_value_atr | python | atr | analysis behavior |
| — | — | 3.0 | — | — | analysis.zones.scoring.fresh | python | score | analysis behavior |
| — | — | 2.0 | — | — | analysis.zones.scoring.grade_a_grab | python | score | analysis behavior |
| — | — | 3.0 | — | — | analysis.zones.scoring.higher_timeframe | python | score | analysis behavior |
| — | — | 2.0 | — | — | analysis.zones.scoring.key_level | python | score | analysis behavior |
| — | — | 2.0 | — | — | analysis.zones.scoring.liquidity | python | score | analysis behavior |
| — | — | 2.0 | — | — | analysis.zones.reconciliation.minimum_remainder_price | python | price | analysis behavior |
| — | — | 2.0 | — | — | analysis.zones.scoring.premium_discount | python | score | analysis behavior |
| — | — | 0.2 | — | — | analysis.zones.reconciliation.maximum_affected_fraction | python | fraction | analysis behavior |
| — | — | 5 | — | — | analysis.zones.reconciliation.minimum_sample | python | count | analysis behavior |
| — | — | 0.5 | — | — | analysis.zones.reconciliation.minimum_overlap | python | fraction | analysis behavior |
| — | — | 1.0 | — | — | analysis.zones.scoring.round_number | python | score | analysis behavior |
| — | — | 2.0 | — | — | analysis.zones.scoring.session_level | python | score | analysis behavior |
| — | — | 1.0 | — | — | analysis.zones.scoring.single_touch | python | score | analysis behavior |
| — | — | 5.0 | — | — | analysis.zones.scoring.source_cap | python | score | analysis behavior |
| — | — | 1.5 | — | — | analysis.zones.scoring.trendline | python | score | analysis behavior |
| — | — | auto_trade:config_health | — | auto_trade:config_health | contract.keys.config_health | shared | string | cross-service contract |
| — | — | 2 | — | 2 | contract.versions.config_manifest | shared | count | cross-service contract |
| — | — | auto_trade:config_manifest:ctrader | — | auto_trade:config_manifest:ctrader | contract.keys.ctrader_manifest | shared | string | cross-service contract |
| — | — | 1 | — | 1 | contract.versions.entry_plan | shared | count | cross-service contract |
| — | — | auto_trade:executor_readiness | — | auto_trade:executor_readiness | contract.keys.executor_readiness | shared | string | cross-service contract |
| — | — | auto_trade:config_manifest:python | — | auto_trade:config_manifest:python | contract.keys.python_manifest | shared | string | cross-service contract |
| — | — | 3 | — | 3 | contract.versions.stop_plan | shared | count | cross-service contract |
| — | — | auto_trade:strategy_match_ready | — | — | contract.streams.strategy_match_ready | python | string | cross-service contract |
| — | — | auto_trade:positions | — | auto_trade:positions | contract.keys.tracked_positions | shared | string | cross-service contract |
| — | — | 7 | — | 7 | contract.versions.trade_plan | shared | count | cross-service contract |
| — | — | 3000 | — | — | delivery.chart_analysis.maximum_tokens | python | count | delivery |
| — | — | claude-opus-4-7 | — | — | delivery.chart_analysis.model | python | string | delivery |
| — | — | 4 | — | — | delivery.market_map.tag_limit | python | count | delivery |
| — | — | 2.0 | — | — | delivery.telegram.photo_debounce_seconds | python | seconds | delivery |
| — | — | — | — | 30 | lifecycle.executor.candidate_heartbeat_seconds | ctrader | seconds | lifecycle |
| — | — | — | — | 120 | lifecycle.executor.candidate_lease_seconds | ctrader | seconds | lifecycle |
| — | — | — | — | 604800 | lifecycle.delivery.notification_dedup_seconds | ctrader | seconds | lifecycle |
| — | — | 150 | — | — | lifecycle.range_context.private_source_max_age_seconds | python | seconds | lifecycle |
| — | — | 300 | — | — | lifecycle.strategy_match.ready_consumer_health_ttl_seconds | python | seconds | lifecycle |
| — | — | 660 | — | — | lifecycle.range_context.scanner_source_max_age_seconds | python | seconds | lifecycle |
| — | — | 86400 | — | — | lifecycle.setup.audit_retention_seconds | python | seconds | lifecycle |
| — | — | 86400 | — | — | lifecycle.setup.terminal_retention_seconds | python | seconds | lifecycle |
| — | — | 604800 | — | — | lifecycle.zone_watch.retention_seconds | python | seconds | lifecycle |
| — | — | — | — | 0.05 | manual_algo.scaling.first_leg_lots | ctrader | lots | execution safety |
| — | — | — | — | 0.13 | manual_algo.scaling.first_leg_threshold_lots | ctrader | lots | execution safety |
| — | — | 0.12 | — | — | strategies.auto_scalp.box.break_buffer_atr | python | atr | strategy behavior |
| — | — | 2 | — | — | strategies.auto_scalp.box.break_confirmation_closes | python | bars | strategy behavior |
| — | — | 60 | — | — | strategies.auto_scalp.box.lookback_bars | python | bars | strategy behavior |
| — | — | 0.45 | — | — | strategies.auto_scalp.box.maximum_close_efficiency | python | fraction | strategy behavior |
| — | — | 6 | — | — | strategies.auto_scalp.box.maximum_touch_band_pips | python | pips | strategy behavior |
| — | — | 120 | — | — | strategies.auto_scalp.box.maximum_width_pips | python | pips | strategy behavior |
| — | — | 0.15 | — | — | strategies.auto_scalp.box.minimum_body_fraction | python | fraction | strategy behavior |
| — | — | 0.82 | — | — | strategies.auto_scalp.box.minimum_inside_ratio | python | fraction | strategy behavior |
| — | — | 2.5 | — | — | strategies.auto_scalp.box.minimum_touch_band_pips | python | pips | strategy behavior |
| — | — | 2 | — | — | strategies.auto_scalp.box.minimum_touch_episodes | python | count | strategy behavior |
| — | — | 0.15 | — | — | strategies.auto_scalp.box.minimum_wick_fraction | python | fraction | strategy behavior |
| — | — | 55 | — | — | strategies.auto_scalp.box.minimum_width_pips | python | pips | strategy behavior |
| — | — | 0.15 | — | — | strategies.auto_scalp.box.recovery_atr | python | atr | strategy behavior |
| — | — | 0.18 | — | — | strategies.auto_scalp.box.touch_band_atr | python | atr | strategy behavior |
| — | — | M1 | — | — | strategies.auto_scalp.execution_timeframe | python | string | strategy behavior |
| — | — | 64 | — | — | strategies.auto_scalp.lookbacks.m15_bars | python | bars | strategy behavior |
| — | — | 120 | — | — | strategies.auto_scalp.lookbacks.m1_bars | python | bars | strategy behavior |
| — | — | 96 | — | — | strategies.auto_scalp.lookbacks.m5_bars | python | bars | strategy behavior |
| alert_overlap_suppress | ALERT_OVERLAP_SUPPRESS | 0.5 | — | — | analysis.alert_overlap_suppress | python | fraction | analysis behavior |
| allow_counter_trend | ALLOW_COUNTER_TREND | True | — | — | strategies.allow_counter_trend | python | boolean | strategy behavior |
| anthropic_api_key | ANTHROPIC_API_KEY | — | — | — | delivery.anthropic_api_key | python | string | infrastructure |
| atr_length | ATR_LENGTH | 14 | — | — | analysis.atr.length | python | count | analysis behavior |
| auto_book_bare_pips | AUTO_BOOK_BARE_PIPS | False | — | — | delivery.auto_book_bare_pips | python | boolean | delivery |
| auto_trade_add_min_stop_pips | AUTO_TRADE_ADD_MIN_STOP_PIPS | 30 | 30 | 30 | execution.add_min_stop_pips | shared | pips | execution safety |
| auto_trade_add_risk_fraction | AUTO_TRADE_ADD_RISK_FRACTION | 0.5 | — | 0.5 | risk.add_risk_fraction | shared | fraction | broker/account safety |
| auto_trade_add_size_ratio | AUTO_TRADE_ADD_SIZE_RATIO | 0.5 | — | 0.5 | execution.add_size_ratio | shared | fraction | execution safety |
| auto_trade_add_stop_buffer_atr | AUTO_TRADE_ADD_STOP_BUFFER_ATR | 0.3 | 0.3 | 0.3 | execution.add_stop_buffer_atr | shared | atr | execution safety |
| auto_trade_allow_concurrent_strategies | AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES | False | true | False | risk.allow_concurrent_strategies | shared | boolean | broker/account safety |
| auto_trade_allow_counter_bias | AUTO_TRADE_ALLOW_COUNTER_BIAS | True | true | False | execution.allow_counter_bias | shared | boolean | execution safety |
| auto_trade_allow_hedged_xau | AUTO_TRADE_ALLOW_HEDGED_XAU | False | — | False | risk.allow_hedged_xau | shared | boolean | broker/account safety |
| auto_trade_be_buffer_ticks | AUTO_TRADE_BE_BUFFER_TICKS | 6 | — | 6 | execution.be_buffer_ticks | shared | ticks | execution safety |
| auto_trade_box_breakout_enabled | AUTO_TRADE_BOX_BREAKOUT_ENABLED | False | — | — | strategies.box_breakout_enabled | python | boolean | strategy behavior |
| auto_trade_box_retire_seconds | AUTO_TRADE_BOX_RETIRE_SECONDS | 14400 | — | — | lifecycle.range_box.retirement_seconds | python | seconds | lifecycle |
| auto_trade_break_retest_enabled | AUTO_TRADE_BREAK_RETEST_ENABLED | False | — | — | strategies.break_retest_enabled | python | boolean | strategy behavior |
| auto_trade_breakout_enabled | AUTO_TRADE_BREAKOUT_ENABLED | True | — | True | strategies.breakout_enabled | shared | boolean | strategy behavior |
| auto_trade_candidate_contract_version | AUTO_TRADE_CANDIDATE_CONTRACT_VERSION | 6 | 6 | 6 | contract.candidate_version | shared | count | cross-service contract |
| auto_trade_candidate_max_age_seconds | AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS | 90 | 420 | 90 | lifecycle.candidate_max_age_seconds | shared | seconds | lifecycle |
| auto_trade_candidate_ttl | AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS | 86400 | 604800 | 86400 | lifecycle.candidate.storage_ttl_seconds | shared | seconds | lifecycle |
| auto_trade_canonical_symbol | AUTO_TRADE_CANONICAL_SYMBOL | XAU | XAU | XAU | contract.canonical_symbol | shared | string | cross-service contract |
| auto_trade_contract_mode | AUTO_TRADE_CONTRACT_MODE | v7_only | — | v7_only | contract.mode | shared | string | cross-service contract |
| auto_trade_contract_size | AUTO_TRADE_XAU_CONTRACT_SIZE | 100.0 | 100 | 100 | contract.instrument.contract_size | shared | count | cross-service contract |
| auto_trade_demand_reaction_enabled | AUTO_TRADE_DEMAND_REACTION_ENABLED | True | true | — | strategies.demand_reaction_enabled | python | boolean | strategy behavior |
| auto_trade_direct_publish_enabled | AUTO_TRADE_DIRECT_PUBLISH_ENABLED | True | — | — | execution.direct_publish_enabled | python | boolean | execution safety |
| auto_trade_displacement_override_lookback_bars | AUTO_TRADE_DISPLACEMENT_OVERRIDE_LOOKBACK_BARS | 3 | — | — | execution.displacement_override_lookback_bars | python | bars | execution safety |
| auto_trade_dry_run | AUTO_TRADE_DRY_RUN | True | false | True | contract.dry_run | shared | boolean | cross-service contract |
| auto_trade_edge_proximity_atr | AUTO_TRADE_EDGE_PROXIMITY_ATR | 0.5 | — | — | actionability.edge_proximity_atr | python | atr | execution safety |
| auto_trade_enabled | AUTO_TRADE_ENABLED | False | true | False | contract.enabled | shared | boolean | cross-service contract |
| auto_trade_entry_contract_tolerance_pips | AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS | 3.0 | — | 3 | execution.entry.contract_tolerance_pips | shared | pips | execution safety |
| auto_trade_eq_exclusion_fraction | AUTO_TRADE_EQ_EXCLUSION_FRACTION | 0.15 | — | — | actionability.eq_exclusion_fraction | python | fraction | execution safety |
| auto_trade_equity_table_version | AUTO_TRADE_EQUITY_TABLE_VERSION | owner_equity_v1 | owner_equity_v1 | owner_equity_v1 | risk.equity_table_version | shared | string | broker/account safety |
| auto_trade_event_stream | AUTO_TRADE_EVENT_STREAM | auto_trade:events | auto_trade:events | auto_trade:events | contract.streams.events | shared | string | cross-service contract |
| auto_trade_execution_cost_pips | AUTO_TRADE_EXECUTION_COST_PIPS | 1.0 | — | — | execution.execution_cost_pips | python | pips | execution safety |
| auto_trade_execution_zone_max_width_atr | AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR | 2.0 | 2.0 | 2.0 | execution.execution_zone_max_width_atr | shared | atr | execution safety |
| auto_trade_execution_zone_max_width_pips | AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS | 100.0 | 100 | 100 | execution.execution_zone_max_width_pips | shared | pips | execution safety |
| auto_trade_fade_scalp_enabled | AUTO_TRADE_FADE_SCALP_ENABLED | True | — | — | strategies.fade_scalp_enabled | python | boolean | strategy behavior |
| auto_trade_group_close_allocation | AUTO_TRADE_GROUP_CLOSE_ALLOCATION | pro_rata | pro_rata | pro_rata | execution.group_close_allocation | shared | string | execution safety |
| auto_trade_htf_veto_enabled | AUTO_TRADE_HTF_VETO_ENABLED | True | — | — | actionability.htf_veto_enabled | python | boolean | execution safety |
| auto_trade_inside_zone_market_entry_enabled | AUTO_TRADE_INSIDE_ZONE_MARKET_ENTRY_ENABLED | True | — | True | execution.inside_zone_market_entry_enabled | shared | boolean | execution safety |
| auto_trade_key_level_reaction_enabled | AUTO_TRADE_KEY_LEVEL_REACTION_ENABLED | True | true | — | strategies.key_level_reaction_enabled | python | boolean | strategy behavior |
| auto_trade_liquidity_reversal_enabled | AUTO_TRADE_LIQUIDITY_REVERSAL_ENABLED | True | — | True | strategies.liquidity_reversal_enabled | shared | boolean | strategy behavior |
| auto_trade_map_counter_bias_enabled | AUTO_TRADE_MAP_COUNTER_BIAS_ENABLED | True | — | — | strategies.map_counter_bias_enabled | python | boolean | strategy behavior |
| auto_trade_map_counter_bias_min_confluence | AUTO_TRADE_MAP_COUNTER_BIAS_MIN_CONFLUENCE | 2 | — | — | actionability.map_counter_bias_min_confluence | python | count | execution safety |
| auto_trade_map_counter_bias_min_score | AUTO_TRADE_MAP_COUNTER_BIAS_MIN_SCORE | 6.0 | — | — | execution.map_counter_bias_min_score | python | count | execution safety |
| auto_trade_map_execute_distance_atr | AUTO_TRADE_MAP_EXECUTE_DISTANCE_ATR | 1.5 | — | — | execution.map_execute_distance_atr | python | atr | execution safety |
| auto_trade_map_execute_tolerance_atr | AUTO_TRADE_MAP_EXECUTE_TOLERANCE_ATR | 0.15 | — | — | execution.map_execute_tolerance_atr | python | atr | execution safety |
| auto_trade_map_execute_tolerance_pips | AUTO_TRADE_MAP_EXECUTE_TOLERANCE_PIPS | 3.0 | — | — | execution.map_execute_tolerance_pips | python | pips | execution safety |
| auto_trade_map_hard_entry_drift_pips | AUTO_TRADE_MAP_HARD_ENTRY_DRIFT_PIPS | 20.0 | 20 | — | execution.map_hard_entry_drift_pips | python | pips | execution safety |
| auto_trade_map_max_entry_drift_atr | AUTO_TRADE_MAP_MAX_ENTRY_DRIFT_ATR | 0.4 | 1.0 | — | execution.map_max_entry_drift_atr | python | atr | execution safety |
| auto_trade_map_min_entry_drift_pips | AUTO_TRADE_MAP_MIN_ENTRY_DRIFT_PIPS | 10.0 | 10 | — | execution.map_min_entry_drift_pips | python | pips | execution safety |
| auto_trade_map_reaction_lookback_bars | AUTO_TRADE_MAP_REACTION_LOOKBACK_BARS | 5 | 5 | — | execution.map_reaction_lookback_bars | python | bars | execution safety |
| auto_trade_map_reaction_rearm_atr | AUTO_TRADE_MAP_REACTION_REARM_ATR | 0.5 | 0.50 | — | lifecycle.map_reaction_rearm_atr | python | atr | lifecycle |
| auto_trade_map_reaction_rearm_bars | AUTO_TRADE_MAP_REACTION_REARM_BARS | 3 | 3 | — | lifecycle.map_reaction_rearm_bars | python | bars | lifecycle |
| auto_trade_map_thesis_lock_enabled | AUTO_TRADE_MAP_THESIS_LOCK_ENABLED | True | true | True | execution.map_thesis_lock_enabled | shared | boolean | execution safety |
| auto_trade_map_track_distance_atr | AUTO_TRADE_MAP_TRACK_DISTANCE_ATR | 8.0 | — | — | execution.map_track_distance_atr | python | atr | execution safety |
| auto_trade_map_zone_min_width_abs | AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ABS | 1.0 | — | — | execution.map_zone_min_width_abs | python | count | execution safety |
| auto_trade_map_zone_min_width_atr | AUTO_TRADE_MAP_ZONE_MIN_WIDTH_ATR | 0.15 | — | — | execution.map_zone_min_width_atr | python | atr | execution safety |
| auto_trade_mapped_zone_enabled | AUTO_TRADE_MAPPED_ZONE_ENABLED | True | false | True | strategies.mapped_zone_enabled | shared | boolean | strategy behavior |
| auto_trade_market_map_guard_enabled | AUTO_TRADE_MARKET_MAP_GUARD_ENABLED | True | false | True | actionability.market_map_guard_enabled | shared | boolean | execution safety |
| auto_trade_max_active_positions_per_symbol | AUTO_TRADE_MAX_ACTIVE_POSITIONS_PER_SYMBOL | 1 | — | — | risk.position_limits.maximum_per_symbol | python | count | broker/account safety |
| auto_trade_max_entry_distance_pips | AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS | 40.0 | 40 | 40 | execution.entry.maximum_chase_distance_pips | shared | pips | execution safety |
| auto_trade_max_tracked_candidates | AUTO_TRADE_MAX_TRACKED_CANDIDATES | 5 | — | — | risk.max_tracked_candidates | python | count | broker/account safety |
| auto_trade_max_tranches | AUTO_TRADE_MAX_TRANCHES | 2 | — | 2 | execution.max_tranches | shared | count | execution safety |
| auto_trade_min_capped_target_pips | AUTO_TRADE_MIN_CAPPED_TARGET_PIPS | 15.0 | — | — | actionability.target_room.minimum_capped_target_pips | python | pips | execution safety |
| auto_trade_min_confluence | AUTO_TRADE_MIN_CONFLUENCE | 2 | 2 | 2 | actionability.min_confluence | shared | count | execution safety |
| auto_trade_momentum_ride_enabled | AUTO_TRADE_MOMENTUM_RIDE_ENABLED | True | true | — | strategies.momentum_ride_enabled | python | boolean | strategy behavior |
| auto_trade_multi_match_enabled | AUTO_TRADE_MULTI_MATCH_ENABLED | False | — | False | execution.multi_match_enabled | shared | boolean | execution safety |
| auto_trade_news_guard_minutes | AUTO_TRADE_NEWS_GUARD_MINUTES | 30 | — | — | actionability.news_guard_minutes | python | minutes | execution safety |
| auto_trade_non_hedged_opposite_policy | AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY | reject | broker_netting | reject | risk.non_hedged_opposite_policy | shared | string | broker/account safety |
| auto_trade_one_sided_range_risk_multiplier | AUTO_TRADE_ONE_SIDED_RANGE_RISK_MULTIPLIER | 0.5 | — | — | risk.one_sided_range_risk_multiplier | python | fraction | broker/account safety |
| auto_trade_opposing_active_min_price | AUTO_TRADE_OPPOSING_ACTIVE_MIN_PRICE | 15.0 | 15 | — | risk.exposure.opposing_minimum_separation_price | python | price | broker/account safety |
| auto_trade_opposing_barrier_atr | AUTO_TRADE_OPPOSING_BARRIER_ATR | 0.5 | — | — | actionability.target_room.barrier_buffer_atr | python | atr | execution safety |
| auto_trade_opposing_barrier_veto_enabled | AUTO_TRADE_OPPOSING_BARRIER_VETO_ENABLED | True | — | — | actionability.opposing_barrier_veto_enabled | python | boolean | execution safety |
| auto_trade_overlap_veto_enabled | AUTO_TRADE_OVERLAP_VETO_ENABLED | True | — | — | actionability.overlapping_zones.veto_enabled | python | boolean | execution safety |
| auto_trade_post_fill_target_fallback | AUTO_TRADE_POST_FILL_TARGET_FALLBACK | fill_relative | — | fill_relative | execution.post_fill_target_fallback | shared | string | execution safety |
| auto_trade_post_impulse_risk_multiplier | AUTO_TRADE_POST_IMPULSE_RISK_MULTIPLIER | 0.5 | — | — | risk.post_impulse_risk_multiplier | python | fraction | broker/account safety |
| auto_trade_profile | AUTO_TRADE_PROFILE | conservative | demo_eval | conservative | contract.profile | shared | string | cross-service contract |
| auto_trade_range_box_move_sl_to_be_after_scale_out | AUTO_TRADE_RANGE_BOX_MOVE_SL_TO_BE_AFTER_SCALE_OUT | False | false | False | execution.range_box_move_sl_to_be_after_scale_out | shared | boolean | execution safety |
| auto_trade_range_box_scale_out_enabled | AUTO_TRADE_RANGE_BOX_SCALE_OUT_ENABLED | True | true | True | strategies.range_box_scale_out_enabled | shared | boolean | strategy behavior |
| auto_trade_range_box_scale_out_fraction | AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION | 0.5 | 0.50 | 0.5 | execution.range_box_scale_out_fraction | shared | fraction | execution safety |
| auto_trade_range_box_scale_out_threshold_pips | AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS | 70 | 70 | 70 | execution.range_box_scale_out_threshold_pips | shared | pips | execution safety |
| auto_trade_range_box_scale_out_trigger_pips | AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS | 30 | 30 | 30 | execution.range_box_scale_out_trigger_pips | shared | pips | execution safety |
| auto_trade_range_enabled | AUTO_TRADE_RANGE_ENABLED | True | — | True | strategies.range_enabled | shared | boolean | strategy behavior |
| auto_trade_range_flip_enabled | AUTO_TRADE_RANGE_FLIP_ENABLED | False | true | False | strategies.range_flip_enabled | shared | boolean | strategy behavior |
| auto_trade_range_hard_entry_drift_pips | AUTO_TRADE_RANGE_HARD_ENTRY_DRIFT_PIPS | 20.0 | 20 | — | execution.range_hard_entry_drift_pips | python | pips | execution safety |
| auto_trade_range_max_entry_drift_atr | AUTO_TRADE_RANGE_MAX_ENTRY_DRIFT_ATR | 0.35 | 1.0 | — | execution.range_max_entry_drift_atr | python | atr | execution safety |
| auto_trade_range_max_risk_multiplier | AUTO_TRADE_RANGE_MAX_RISK_MULTIPLIER | 2.0 | 2.0 | — | risk.range_max_risk_multiplier | python | fraction | broker/account safety |
| auto_trade_range_min_entry_drift_pips | AUTO_TRADE_RANGE_MIN_ENTRY_DRIFT_PIPS | 10.0 | 10 | — | execution.range_min_entry_drift_pips | python | pips | execution safety |
| auto_trade_range_min_rr | AUTO_TRADE_RANGE_MIN_RR | 1.0 | 1.0 | — | execution.range_min_rr | python | count | execution safety |
| auto_trade_range_min_target_pips | AUTO_TRADE_RANGE_MIN_TARGET_PIPS | 15.0 | 15 | — | execution.range_min_target_pips | python | pips | execution safety |
| auto_trade_range_room_stop_floor_pips | AUTO_TRADE_RANGE_ROOM_STOP_FLOOR_PIPS | 15 | 15 | — | execution.range_room_stop_floor_pips | python | pips | execution safety |
| auto_trade_range_targets_pips | AUTO_TRADE_RANGE_TARGETS_PIPS | 15,20,30,40,50,70 | 15,20,30,40,50,70 | 15,20,30,40,50,70 | execution.targeting.range_ladder_pips | shared | pips | execution safety |
| auto_trade_range_tp_buffer_pips | AUTO_TRADE_RANGE_TP_BUFFER_PIPS | 3.0 | 3 | 3 | execution.range_tp_buffer_pips | shared | pips | execution safety |
| auto_trade_range_two_sided_enabled | AUTO_TRADE_RANGE_TWO_SIDED_ENABLED | False | true | False | strategies.range_two_sided_enabled | shared | boolean | strategy behavior |
| auto_trade_reaction_enabled | AUTO_TRADE_REACTION_ENABLED | True | — | True | strategies.reaction_enabled | shared | boolean | strategy behavior |
| auto_trade_reaction_market_fraction | AUTO_TRADE_REACTION_MARKET_FRACTION | 0.7 | 0.70 | 0.7 | execution.reaction_market_fraction | shared | fraction | execution safety |
| auto_trade_reaction_room_stop_floor_pips | AUTO_TRADE_REACTION_ROOM_STOP_FLOOR_PIPS | 20 | 20 | — | execution.stops.reaction.room_floor_pips | python | pips | execution safety |
| auto_trade_reaction_room_stop_min_rr | AUTO_TRADE_REACTION_ROOM_STOP_MIN_RR | 1.0 | 1.0 | — | execution.reaction_room_stop_min_rr | python | count | execution safety |
| auto_trade_reaction_scale_enabled | AUTO_TRADE_REACTION_SCALE_ENABLED | False | false | False | strategies.reaction_scale_enabled | shared | boolean | strategy behavior |
| auto_trade_reaction_scale_fraction | AUTO_TRADE_REACTION_SCALE_FRACTION | 0.3 | 0.30 | 0.3 | execution.reaction_scale_fraction | shared | fraction | execution safety |
| auto_trade_reaction_scale_invalid_policy | AUTO_TRADE_REACTION_SCALE_INVALID_POLICY | single_market | single_market | single_market | execution.reaction_scale_invalid_policy | shared | string | execution safety |
| auto_trade_reaction_scale_step_atr | AUTO_TRADE_REACTION_SCALE_STEP_ATR | 0.5 | 0.50 | 0.5 | execution.reaction_scale_step_atr | shared | atr | execution safety |
| auto_trade_reaction_stop_max_pips | AUTO_TRADE_REACTION_STOP_MAX_PIPS | 60 | — | — | execution.reaction_stop_max_pips | python | pips | execution safety |
| auto_trade_reaction_stop_min_pips | AUTO_TRADE_REACTION_STOP_MIN_PIPS | 40 | — | — | execution.reaction_stop_min_pips | python | pips | execution safety |
| auto_trade_regime_direction_enabled | AUTO_TRADE_REGIME_DIRECTION_ENABLED | False | — | — | execution.regime_direction_enabled | python | boolean | execution safety |
| auto_trade_regime_direction_lookback | AUTO_TRADE_REGIME_DIRECTION_LOOKBACK | 120 | — | — | execution.regime_direction_lookback | python | bars | execution safety |
| auto_trade_regime_min_directional_swings | AUTO_TRADE_REGIME_MIN_DIRECTIONAL_SWINGS | 3 | — | — | execution.regime_min_directional_swings | python | count | execution safety |
| auto_trade_regime_min_displacement_atr | AUTO_TRADE_REGIME_MIN_DISPLACEMENT_ATR | 4.0 | — | — | execution.regime_min_displacement_atr | python | atr | execution safety |
| auto_trade_require_demo_account | AUTO_TRADE_REQUIRE_DEMO_ACCOUNT | True | true | True | contract.account.require_demo | shared | boolean | cross-service contract |
| auto_trade_require_flat_for_range | AUTO_TRADE_REQUIRE_FLAT_FOR_RANGE | True | — | True | risk.require_flat_for_range | shared | boolean | broker/account safety |
| auto_trade_retest_enabled | AUTO_TRADE_RETEST_ENABLED | True | — | True | strategies.retest_enabled | shared | boolean | strategy behavior |
| auto_trade_retest_trigger_validity_bars | AUTO_TRADE_RETEST_TRIGGER_VALIDITY_BARS | 2 | 2 | — | lifecycle.retest.trigger_validity_bars | python | bars | lifecycle |
| auto_trade_same_direction_stack_size_fraction | AUTO_TRADE_SAME_DIRECTION_STACK_SIZE_FRACTION | 0.6 | 0.60 | — | risk.same_direction_stack_size_fraction | python | fraction | broker/account safety |
| auto_trade_session_level_reaction_enabled | AUTO_TRADE_SESSION_LEVEL_REACTION_ENABLED | True | true | — | strategies.session_level_reaction_enabled | python | boolean | strategy behavior |
| auto_trade_sizing_mode | AUTO_TRADE_SIZING_MODE | equity_table | equity_table | equity_table | risk.sizing.mode | shared | string | broker/account safety |
| auto_trade_sl_distance | AUTO_TRADE_SL_DISTANCE | 6.5 | 6.5 | 6.5 | execution.sl_distance | shared | price | execution safety |
| auto_trade_snap_back_enabled | AUTO_TRADE_SNAP_BACK_ENABLED | True | — | — | strategies.snap_back_enabled | python | boolean | strategy behavior |
| auto_trade_spot_max_age | AUTO_TRADE_SPOT_MAX_AGE_SECONDS | 5 | 5 | 5 | market_data.spot.maximum_age_seconds | shared | count | lifecycle |
| auto_trade_stop_push_beyond_zone | AUTO_TRADE_STOP_PUSH_BEYOND_ZONE | True | — | True | execution.stop_push_beyond_zone | shared | boolean | execution safety |
| auto_trade_strategy_match_enabled | AUTO_TRADE_STRATEGY_MATCH_ENABLED | True | — | True | execution.strategy_match_enabled | shared | boolean | execution safety |
| auto_trade_strategy_match_max_age_seconds | AUTO_TRADE_STRATEGY_MATCH_MAX_AGE_SECONDS | 420 | — | — | lifecycle.strategy_match.maximum_age_seconds | python | seconds | lifecycle |
| auto_trade_stream | AUTO_TRADE_CANDIDATE_STREAM | auto_trade:candidates | auto_trade:candidates | auto_trade:candidates | contract.streams.candidates | shared | string | cross-service contract |
| auto_trade_stream_maxlen | AUTO_TRADE_STREAM_MAXLEN | 1000 | — | — | contract.stream_maxlen | python | count | cross-service contract |
| auto_trade_structural_guard_mode | AUTO_TRADE_STRUCTURAL_GUARD_MODE | balanced | observe | balanced | actionability.structural_guard_mode | shared | string | execution safety |
| auto_trade_structural_reaction_lookback_bars | AUTO_TRADE_STRUCTURAL_REACTION_LOOKBACK_BARS | 3 | 3 | — | execution.structural_reaction_lookback_bars | python | bars | execution safety |
| auto_trade_supply_reaction_enabled | AUTO_TRADE_SUPPLY_REACTION_ENABLED | True | true | — | strategies.supply_reaction_enabled | python | boolean | strategy behavior |
| auto_trade_symbols | AUTO_TRADE_SYMBOLS | XAU | XAU | XAU | contract.symbols | shared | string | cross-service contract |
| auto_trade_telegram_delete_root_on_terminal | AUTO_TRADE_TELEGRAM_DELETE_ROOT_ON_TERMINAL | False | false | — | delivery.telegram.delete_root_on_terminal | python | boolean | delivery |
| auto_trade_telegram_single_root_card | AUTO_TRADE_TELEGRAM_SINGLE_ROOT_CARD | True | true | — | delivery.telegram.single_root_card | python | boolean | delivery |
| auto_trade_tier_a_risk_multiplier | AUTO_TRADE_TIER_A_RISK_MULTIPLIER | 1.0 | — | — | risk.tiers.a_multiplier | python | fraction | broker/account safety |
| auto_trade_tier_b_risk_multiplier | AUTO_TRADE_TIER_B_RISK_MULTIPLIER | 0.5 | — | — | risk.tiers.b_multiplier | python | fraction | broker/account safety |
| auto_trade_tp_pips | AUTO_TRADE_TARGET_PLANS_PIPS | 30,60,90,120,200 | 30,60,90,120,200 | 30,60,90,120,200 | execution.targeting.default_ladder_pips | shared | pips | execution safety |
| auto_trade_track_all_structural_matches | AUTO_TRADE_TRACK_ALL_STRUCTURAL_MATCHES | False | — | False | execution.track_all_structural_matches | shared | boolean | execution safety |
| auto_trade_trade_plan_stream | AUTO_TRADE_TRADE_PLAN_STREAM | execution:trade_plans | — | execution:trade_plans | contract.streams.trade_plans | shared | string | cross-service contract |
| auto_trade_trend_enabled | AUTO_TRADE_TREND_ENABLED | False | — | False | strategies.trend_enabled | shared | boolean | strategy behavior |
| auto_trade_trend_hard_entry_drift_pips | AUTO_TRADE_TREND_HARD_ENTRY_DRIFT_PIPS | 30.0 | 30 | — | execution.trend_hard_entry_drift_pips | python | pips | execution safety |
| auto_trade_trend_max_entry_drift_atr | AUTO_TRADE_TREND_MAX_ENTRY_DRIFT_ATR | 0.85 | 1.5 | — | execution.trend_max_entry_drift_atr | python | atr | execution safety |
| auto_trade_trend_min_entry_drift_pips | AUTO_TRADE_TREND_MIN_ENTRY_DRIFT_PIPS | 15.0 | 15 | — | execution.trend_min_entry_drift_pips | python | pips | execution safety |
| auto_trade_trend_pullback_enabled | AUTO_TRADE_TREND_PULLBACK_ENABLED | True | — | — | strategies.trend_pullback_enabled | python | boolean | strategy behavior |
| auto_trade_trend_stop_max_pips | AUTO_TRADE_TREND_STOP_MAX_PIPS | 60 | 60 | 60 | execution.trend_stop_max_pips | shared | pips | execution safety |
| auto_trade_trend_stop_min_pips | AUTO_TRADE_TREND_STOP_MIN_PIPS | 40 | 40 | 40 | execution.stops.trend.minimum_pips | shared | pips | execution safety |
| auto_trade_trendline_reaction_enabled | AUTO_TRADE_TRENDLINE_REACTION_ENABLED | True | true | — | strategies.trendline_reaction_enabled | python | boolean | strategy behavior |
| auto_trade_unfilled_leg_after_tp_policy | AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY | cancel | cancel | cancel | execution.unfilled_leg_after_tp_policy | shared | string | execution safety |
| auto_trade_wick_stop_buffer_atr | AUTO_TRADE_WICK_STOP_BUFFER_ATR | 0.15 | 0.15 | 0.15 | execution.wick_stop_buffer_atr | shared | atr | execution safety |
| auto_trade_xau_pip_size | AUTO_TRADE_XAU_PIP_SIZE | 0.1 | 0.1 | 0.1 | contract.instrument.pip_size | shared | pips | cross-service contract |
| auto_trade_xau_price_digits | AUTO_TRADE_XAU_PRICE_DIGITS | 2 | 2 | — | contract.instrument.price_digits | python | count | cross-service contract |
| auto_trade_zone_cooldown_atr | AUTO_TRADE_ZONE_COOLDOWN_ATR | 1.0 | — | — | lifecycle.zone_cooldown_atr | python | atr | lifecycle |
| auto_trade_zone_cooldown_enabled | AUTO_TRADE_ZONE_COOLDOWN_ENABLED | True | false | True | lifecycle.zone_cooldown_enabled | shared | boolean | lifecycle |
| auto_trade_zone_fill_enabled | AUTO_TRADE_ZONE_FILL_ENABLED | False | true | False | execution.zone_fill_enabled | shared | boolean | execution safety |
| auto_trade_zone_fill_fallback_enabled | AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED | True | — | True | execution.zone_fill_fallback_enabled | shared | boolean | execution safety |
| auto_trade_zone_fill_min_atr | AUTO_TRADE_ZONE_FILL_MIN_ATR | 0.5 | — | 0.5 | execution.zone_fill_min_atr | shared | atr | execution safety |
| auto_trade_zone_reconcile_enabled | AUTO_TRADE_ZONE_RECONCILE_ENABLED | True | — | — | actionability.zone_reconcile_enabled | python | boolean | execution safety |
| auto_trade_zone_reconcile_mode | AUTO_TRADE_ZONE_RECONCILE_MODE | enforce | shadow | enforce | actionability.zone_reconciliation.mode | shared | string | execution safety |
| auto_trade_zone_scale_first_leg_fraction | AUTO_TRADE_ZONE_SCALE_FIRST_LEG_FRACTION | 0.7 | — | — | execution.zone_scaling.first_leg_fraction | python | fraction | execution safety |
| auto_trade_zone_scale_step_atr | AUTO_TRADE_ZONE_SCALE_STEP_ATR | 0.5 | — | — | execution.zone_scale_step_atr | python | atr | execution safety |
| auto_trade_zone_scale_undersized_policy | AUTO_TRADE_ZONE_SCALE_UNDERSIZED_POLICY | single_entry | single_entry | single_entry | execution.zone_scale_undersized_policy | shared | string | execution safety |
| breakout_accept_bars | BREAKOUT_ACCEPT_BARS | 2 | — | — | analysis.breakout_accept_bars | python | bars | analysis behavior |
| breakout_buffer_atr | BREAKOUT_BUFFER_ATR | 0.1 | — | — | analysis.breakout_buffer_atr | python | atr | analysis behavior |
| breakout_max_age_bars | BREAKOUT_MAX_AGE_BARS | 6 | — | — | analysis.breakout_max_age_bars | python | bars | analysis behavior |
| calendar_currencies | CALENDAR_CURRENCIES | USD | — | — | market_data.calendar_currencies | python | string | infrastructure |
| calendar_enabled | CALENDAR_ENABLED | True | — | — | market_data.calendar_enabled | python | boolean | infrastructure |
| calendar_feed_nextweek | CALENDAR_FEED_NEXTWEEK | https://nfs.faireconomy.media/ff_calendar_nextweek.json | — | — | market_data.calendar_feed_nextweek | python | string | infrastructure |
| calendar_feed_thisweek | CALENDAR_FEED_THISWEEK | https://nfs.faireconomy.media/ff_calendar_thisweek.json | — | — | market_data.calendar_feed_thisweek | python | string | infrastructure |
| calendar_user_agent | CALENDAR_USER_AGENT | apexvoid-trading-bot/1.0 (+contact) | — | — | market_data.calendar_user_agent | python | string | infrastructure |
| chop_edge_frac | CHOP_EDGE_FRAC | 0.25 | — | — | analysis.chop_edge_frac | python | fraction | analysis behavior |
| chop_filter_enabled | CHOP_FILTER_ENABLED | True | — | — | analysis.chop_filter_enabled | python | boolean | analysis behavior |
| chop_lookback | CHOP_LOOKBACK | 24 | — | — | analysis.chop_lookback | python | bars | analysis behavior |
| chop_range_atr | CHOP_RANGE_ATR | 4.0 | — | — | analysis.chop_range_atr | python | atr | analysis behavior |
| coil_contract | COIL_CONTRACT | 0.8 | — | — | analysis.coil_contract | python | count | analysis behavior |
| contested_corridor_gap_atr | CONTESTED_CORRIDOR_GAP_ATR | 0.5 | — | — | actionability.contested_corridor.gap_atr | python | atr | execution safety |
| counter_extreme_pd | COUNTER_EXTREME_PD | 0.25 | — | — | strategies.counter_extreme_pd | python | count | strategy behavior |
| counter_level_min_touches | COUNTER_LEVEL_MIN_TOUCHES | 3 | — | — | strategies.counter_level_min_touches | python | count | strategy behavior |
| counter_min_zone_score | COUNTER_MIN_ZONE_SCORE | 10.0 | — | — | strategies.counter_min_zone_score | python | count | strategy behavior |
| daily_rollover_utc_hour | DAILY_ROLLOVER_UTC_HOUR | 21 | — | — | market_data.daily_rollover_utc_hour | python | utc_hour | infrastructure |
| database_url | DATABASE_URL | postgresql://apexvoid:apexvoid@localhost:5432/signals | — | — | bootstrap.postgres.url | python | string | infrastructure |
| delivery_delete_on_terminal | DELIVERY_DELETE_ON_TERMINAL | True | — | — | delivery.delivery_delete_on_terminal | python | boolean | delivery |
| delivery_thread_lifecycle | DELIVERY_THREAD_LIFECYCLE | True | — | — | delivery.delivery_thread_lifecycle | python | boolean | delivery |
| displacement_atr_mult | DISPLACEMENT_ATR_MULT | 1.5 | — | — | analysis.displacement_atr_mult | python | atr | analysis behavior |
| eq_band | EQ_BAND | 0.1 | — | — | analysis.eq_band | python | count | analysis behavior |
| equal_tol_atr | EQUAL_TOL_ATR | 0.15 | — | — | analysis.equal_tol_atr | python | atr | analysis behavior |
| event_guard_hours | EVENT_GUARD_HOURS | 4.0 | — | — | market_data.event_guard_hours | python | hours | infrastructure |
| inducement_band_atr | INDUCEMENT_BAND_ATR | 0.3 | — | — | analysis.inducement_band_atr | python | atr | analysis behavior |
| key_level_min_touches | KEY_LEVEL_MIN_TOUCHES | 2 | — | — | analysis.levels.minimum_key_touches | python | count | analysis behavior |
| key_level_role_ambiguity_gate_enabled | KEY_LEVEL_ROLE_AMBIGUITY_GATE_ENABLED | False | false | — | actionability.key_level_role.enabled | python | boolean | execution safety |
| level_cluster_atr | LEVEL_CLUSTER_ATR | 0.5 | — | — | analysis.level_cluster_atr | python | atr | analysis behavior |
| log_dir | LOG_DIR | /var/log/apexvoid | /var/log/apexvoid | /var/log/apexvoid | bootstrap.logging.directory | shared | string | infrastructure |
| log_file_enabled | LOG_FILE_ENABLED | True | true | True | bootstrap.log_file_enabled | shared | boolean | infrastructure |
| log_level | LOG_LEVEL | INFO | — | — | bootstrap.logging.level | python | string | infrastructure |
| log_retention_days | LOG_RETENTION_DAYS | 14 | 14 | 14 | bootstrap.log_retention_days | shared | count | infrastructure |
| m1_trigger_patterns | M1_TRIGGER_PATTERNS | wick_rejection,body_close,strong_close,pin_bar,engulfing,… | — | — | analysis.m1_trigger_patterns | python | string | analysis behavior |
| m1_trigger_strong_close_pct | M1_TRIGGER_STRONG_CLOSE_PCT | 0.2 | — | — | analysis.m1_trigger_strong_close_pct | python | percent | analysis behavior |
| m1_trigger_wick_fraction | M1_TRIGGER_WICK_FRACTION | 0.5 | — | — | analysis.m1_trigger_wick_fraction | python | fraction | analysis behavior |
| manual_algo_dry_run | MANUAL_ALGO_DRY_RUN | True | — | — | manual_algo.dry_run | python | boolean | execution safety |
| manual_algo_enabled | MANUAL_ALGO_ENABLED | False | — | False | manual_algo.enabled | shared | boolean | execution safety |
| manual_algo_owner_execution_dm_enabled | MANUAL_ALGO_OWNER_EXECUTION_DM_ENABLED | False | — | — | manual_algo.owner_execution_dm_enabled | python | boolean | execution safety |
| manual_algo_risk_pct | MANUAL_ALGO_RISK_PCT | 2.0 | — | — | manual_algo.risk_percent | python | percent | execution safety |
| manual_trade_command_stream | MANUAL_TRADE_COMMAND_STREAM | manual_trade:commands | — | manual_trade:commands | manual_algo.manual_trade_command_stream | shared | string | execution safety |
| manual_trade_command_stream_maxlen | MANUAL_TRADE_COMMAND_STREAM_MAXLEN | 1000 | — | — | manual_algo.manual_trade_command_stream_maxlen | python | count | execution safety |
| manual_trade_intent_stream | MANUAL_TRADE_INTENT_STREAM | manual_trade:intents | — | — | manual_algo.streams.intents | python | string | execution safety |
| manual_trade_intent_stream_maxlen | MANUAL_TRADE_INTENT_STREAM_MAXLEN | 1000 | — | — | manual_algo.manual_trade_intent_stream_maxlen | python | count | execution safety |
| map_band_max_atr | MAP_BAND_MAX_ATR | 2.0 | — | — | analysis.map_band_max_atr | python | atr | analysis behavior |
| map_change_min | MAP_CHANGE_MIN | 1.0 | — | — | analysis.map_change_min | python | count | analysis behavior |
| map_fallback_radius | MAP_FALLBACK_RADIUS | 30.0 | — | — | analysis.map_fallback_radius | python | price | analysis behavior |
| map_major_score | MAP_MAJOR_SCORE | 12.0 | — | — | analysis.map_major_score | python | count | analysis behavior |
| map_max_distance_atr | MAP_MAX_DISTANCE_ATR | 15.0 | — | — | analysis.map_max_distance_atr | python | atr | analysis behavior |
| map_max_per_side | MAP_MAX_PER_SIDE | 4 | — | — | analysis.map_max_per_side | python | count | analysis behavior |
| map_max_touches | MAP_MAX_TOUCHES | 2 | — | — | analysis.map_max_touches | python | count | analysis behavior |
| map_min_level_touches | MAP_MIN_LEVEL_TOUCHES | 4 | — | — | analysis.map_min_level_touches | python | count | analysis behavior |
| map_min_per_side | MAP_MIN_PER_SIDE | 2 | — | — | analysis.map_min_per_side | python | count | analysis behavior |
| map_min_zone_score | MAP_MIN_ZONE_SCORE | 6.0 | — | — | analysis.map_min_zone_score | python | count | analysis behavior |
| map_scalp_radius | MAP_SCALP_RADIUS | 15.0 | — | — | analysis.map_scalp_radius | python | price | analysis behavior |
| map_scan_interval_minutes | MAP_SCAN_INTERVAL_MINUTES | 60 | — | — | analysis.map_scan_interval_minutes | python | minutes | analysis behavior |
| map_session_send | MAP_SESSION_SEND | True | — | — | delivery.map_session_send | python | boolean | delivery |
| max_entry_atr | MAX_ENTRY_ATR | 2.0 | — | — | actionability.max_entry_atr | python | atr | execution safety |
| max_merged_zone_atr | MAX_MERGED_ZONE_ATR | 3.0 | — | — | analysis.max_merged_zone_atr | python | atr | analysis behavior |
| max_zone_width_atr | MAX_ZONE_WIDTH_ATR | 1.5 | — | — | analysis.zones.discovery.maximum_width_atr | python | atr | execution safety |
| momentum_body_frac | MOMENTUM_BODY_FRAC | 0.6 | — | — | analysis.momentum_body_frac | python | fraction | analysis behavior |
| momentum_lookback | MOMENTUM_LOOKBACK | 8 | — | — | analysis.momentum_lookback | python | bars | analysis behavior |
| news_brief_hour | NEWS_BRIEF_HOUR | 7 | — | — | market_data.news_brief_hour | python | utc_hour | infrastructure |
| news_guard_block | NEWS_GUARD_BLOCK | False | — | — | market_data.news_guard_block | python | boolean | infrastructure |
| oil_keywords | OIL_KEYWORDS | crude oil inventories,opec,cushing,api weekly crude | — | — | market_data.oil_keywords | python | string | infrastructure |
| proximal_band_atr | PROXIMAL_BAND_ATR | 0.5 | — | — | actionability.proximal_band_atr | python | atr | execution safety |
| public_show_pips | SIGNAL_PUBLIC_SHOW_PIPS | True | — | — | delivery.public_show_pips | python | boolean | delivery |
| range_context_disagreement_gate_enabled | RANGE_CONTEXT_DISAGREEMENT_GATE_ENABLED | False | false | — | actionability.range_context_disagreement_gate_enabled | python | boolean | execution safety |
| range_lookback | RANGE_LOOKBACK | 50 | — | — | analysis.range_lookback | python | bars | analysis behavior |
| range_scalp_allow_rejection_only | RANGE_SCALP_ALLOW_REJECTION_ONLY | True | — | — | strategies.range_scalp_allow_rejection_only | python | boolean | strategy behavior |
| range_scalp_break_closes | RANGE_SCALP_BREAK_CLOSES | 2 | — | — | strategies.range_scalp_break_closes | python | count | strategy behavior |
| range_scalp_cluster_atr | RANGE_SCALP_CLUSTER_ATR | 0.25 | — | — | strategies.range_scalp_cluster_atr | python | atr | strategy behavior |
| range_scalp_cluster_min_abs | RANGE_SCALP_CLUSTER_MIN_ABS | 0.0 | — | — | strategies.range_scalp_cluster_min_abs | python | count | strategy behavior |
| range_scalp_enabled | RANGE_SCALP_ENABLED | True | — | — | strategies.range_scalp_enabled | python | boolean | strategy behavior |
| range_scalp_entry_tol_atr | RANGE_SCALP_ENTRY_TOL_ATR | 0.25 | — | — | strategies.range_scalp_entry_tol_atr | python | atr | strategy behavior |
| range_scalp_lookback | RANGE_SCALP_LOOKBACK | 48 | — | — | strategies.range_scalp_lookback | python | bars | strategy behavior |
| range_scalp_max_edge_width_atr | RANGE_SCALP_MAX_EDGE_WIDTH_ATR | 0.75 | — | — | strategies.range_scalp_max_edge_width_atr | python | atr | strategy behavior |
| range_scalp_max_width_atr | RANGE_SCALP_MAX_WIDTH_ATR | 6.0 | — | — | strategies.range_scalp_max_width_atr | python | atr | strategy behavior |
| range_scalp_min_inside_closes | RANGE_SCALP_MIN_INSIDE_CLOSES | 3 | — | — | strategies.range_scalp_min_inside_closes | python | count | strategy behavior |
| range_scalp_min_room_atr | RANGE_SCALP_MIN_ROOM_ATR | 0.75 | — | — | strategies.range_scalp_min_room_atr | python | atr | strategy behavior |
| range_scalp_min_touches | RANGE_SCALP_MIN_TOUCHES | 2 | — | — | strategies.range_scalp_min_touches | python | count | strategy behavior |
| range_scalp_min_wick_frac | RANGE_SCALP_MIN_WICK_FRAC | 0.25 | — | — | strategies.range_scalp_min_wick_frac | python | fraction | strategy behavior |
| range_scalp_min_wick_rejections | RANGE_SCALP_MIN_WICK_REJECTIONS | 1 | — | — | strategies.range_scalp_min_wick_rejections | python | count | strategy behavior |
| range_scalp_min_width_atr | RANGE_SCALP_MIN_WIDTH_ATR | 1.0 | — | — | strategies.range_scalp_min_width_atr | python | atr | strategy behavior |
| reaction_max_atr | REACTION_MAX_ATR | 0.5 | — | — | analysis.reaction_max_atr | python | atr | analysis behavior |
| redis_url | REDIS_URL | redis://redis:6379/0 | — | redis://redis:6379/0 | bootstrap.redis.url | shared | string | infrastructure |
| regime_chop_alert_share | REGIME_CHOP_ALERT_SHARE | 0.75 | — | — | analysis.regime_chop_alert_share | python | fraction | analysis behavior |
| round_step | ROUND_STEP | 5.0 | — | — | analysis.round_step | python | price | analysis behavior |
| scalp_barrier_fallback_enabled | SCALP_BARRIER_FALLBACK_ENABLED | True | — | — | strategies.scalp_barrier_fallback_enabled | python | boolean | strategy behavior |
| scalp_barrier_fallback_min_confirmations | SCALP_BARRIER_FALLBACK_MIN_CONFIRMATIONS | 1 | — | — | strategies.scalp_barrier_fallback_min_confirmations | python | count | strategy behavior |
| scalp_post_impulse_range_enabled | SCALP_POST_IMPULSE_RANGE_ENABLED | True | — | — | strategies.scalp_post_impulse_range_enabled | python | boolean | strategy behavior |
| scalp_range_provisional_enabled | SCALP_RANGE_PROVISIONAL_ENABLED | True | — | — | strategies.scalp_range_provisional_enabled | python | boolean | strategy behavior |
| scanner_actionability_gate_enabled | SCANNER_ACTIONABILITY_GATE_ENABLED | False | false | — | actionability.scanner_actionability_gate_enabled | python | boolean | execution safety |
| scanner_alert_ttl | SCANNER_ALERT_TTL | 7200 | — | — | market_data.scanner_alert_ttl | python | seconds | infrastructure |
| scanner_card_top_n | SCANNER_CARD_TOP_N | 2 | — | — | delivery.scanner_cards.maximum_cards | python | count | delivery |
| scanner_conflict_margin | SCANNER_CONFLICT_MARGIN | 1.0 | — | — | actionability.scanner_conflict_margin | python | count | execution safety |
| scanner_confluence_floor | SCANNER_CONFLUENCE_FLOOR | 2 | — | — | market_data.scanner_confluence_floor | python | count | infrastructure |
| scanner_enabled | SCANNER_ENABLED | False | — | — | market_data.scanner_enabled | python | boolean | infrastructure |
| scanner_exec_tf | SCANNER_EXEC_TF | M5 | — | — | market_data.scanner.execution_timeframe | python | string | infrastructure |
| scanner_gate_counter_bias_min_confluence | SCANNER_GATE_COUNTER_BIAS_MIN_CONFLUENCE | 3 | — | — | actionability.scanner_gate_counter_bias_min_confluence | python | count | execution safety |
| scanner_gate_max_source_touches | SCANNER_GATE_MAX_SOURCE_TOUCHES | 0 | — | — | actionability.scanner_gate_max_source_touches | python | count | execution safety |
| scanner_gate_require_structural_anchor | SCANNER_GATE_REQUIRE_STRUCTURAL_ANCHOR | False | — | — | actionability.scanner_gate_require_structural_anchor | python | boolean | execution safety |
| scanner_gate_suppress_counter_bias_in_range | SCANNER_GATE_SUPPRESS_COUNTER_BIAS_IN_RANGE | False | — | — | actionability.scanner_gate_suppress_counter_bias_in_range | python | boolean | execution safety |
| scanner_htf | SCANNER_HTF | H1,M15 | — | — | market_data.scanner_htf | python | string | infrastructure |
| scanner_level_bucket | SCANNER_LEVEL_BUCKET | 20 | — | — | market_data.scanner_level_bucket | python | count | infrastructure |
| scanner_symbols | SCANNER_SYMBOLS | XAU | — | — | market_data.scanner.symbols | python | string | infrastructure |
| scanner_telegram_bot_token | SCANNER_TELEGRAM_BOT_TOKEN | — | — | — | delivery.scanner_telegram_bot_token | python | string | infrastructure |
| scanner_top_n | SCANNER_TOP_N | 3 | — | — | delivery.scanner_top_n | python | count | delivery |
| scanner_window | SCANNER_WINDOW | 500 | — | — | market_data.scanner_window | python | bars | infrastructure |
| scanner_zone_width_gate_enabled | SCANNER_ZONE_WIDTH_GATE_ENABLED | False | — | — | actionability.scanner_zone_width_gate_enabled | python | boolean | execution safety |
| seq_reset_tz | SEQ_RESET_TZ | Asia/Ho_Chi_Minh | — | — | delivery.seq_reset_tz | python | string | delivery |
| session_asia_start | SESSION_ASIA_START | 22 | — | — | market_data.session_asia_start | python | utc_hour | infrastructure |
| session_london_start | SESSION_LONDON_START | 7 | — | — | market_data.session_london_start | python | utc_hour | infrastructure |
| session_ny_start | SESSION_NY_START | 13 | — | — | market_data.session_ny_start | python | utc_hour | infrastructure |
| signal_public_channel_id | SIGNAL_PUBLIC_CHANNEL_ID | — | — | — | delivery.signal_public_channel_id | python | count | delivery |
| spot_fresh_secs | SPOT_FRESH_SECS | 30 | — | — | market_data.spot_fresh_secs | python | seconds | infrastructure |
| spot_max_deviation_pct | SPOT_MAX_DEVIATION_PCT | 2.0 | — | — | market_data.spot_max_deviation_pct | python | percent | infrastructure |
| strict_pd_gate | STRICT_PD_GATE | False | — | — | analysis.strict_pd_gate | python | boolean | analysis behavior |
| sweep_body_frac | SWEEP_BODY_FRAC | 0.5 | — | — | analysis.sweep_body_frac | python | fraction | analysis behavior |
| sweep_react_bars | SWEEP_REACT_BARS | 3 | — | — | analysis.sweep_react_bars | python | bars | analysis behavior |
| swing_fractal_n | SWING_FRACTAL_N | 2 | — | — | analysis.swings.fractal_size | python | fraction | analysis behavior |
| telegram_bot_token | TELEGRAM_BOT_TOKEN | <required> | — | — | bootstrap.telegram.bot_token | python | string | infrastructure |
| telegram_channel_id | SIGNAL_VIP_CHANNEL_ID | <required> | — | — | delivery.telegram_channel_id | python | count | delivery |
| telegram_owner_id | TELEGRAM_OWNER_ID | — | — | — | delivery.telegram_owner_id | python | count | delivery |
| tiingo_api_key | TIINGO_API_KEY | — | — | — | market_data.tiingo_api_key | python | string | broker/account safety |
| tl_max_slope_atr | TL_MAX_SLOPE_ATR | 0.15 | — | — | analysis.tl_max_slope_atr | python | atr | analysis behavior |
| tl_min_touches | TL_MIN_TOUCHES | 3 | — | — | analysis.tl_min_touches | python | count | analysis behavior |
| tl_tol_atr | TL_TOL_ATR | 0.3 | — | — | analysis.tl_tol_atr | python | atr | analysis behavior |
| tp_min_spacing_atr | TP_MIN_SPACING_ATR | 0.5 | — | — | analysis.tp_min_spacing_atr | python | atr | analysis behavior |
| track_interval | TRACK_INTERVAL | 30 | — | — | market_data.track_interval | python | count | infrastructure |
| trend_allow_chase | TREND_ALLOW_CHASE | False | — | — | strategies.trend_allow_chase | python | boolean | strategy behavior |
| trend_atr_baseline_bars | TREND_ATR_BASELINE_BARS | 48 | — | — | strategies.trend_atr_baseline_bars | python | atr | strategy behavior |
| trend_atr_expansion | TREND_ATR_EXPANSION | 1.15 | — | — | strategies.trend_atr_expansion | python | atr | strategy behavior |
| trend_breakout_accept_bars | TREND_BREAKOUT_ACCEPT_BARS | 2 | — | — | strategies.trend_breakout_accept_bars | python | bars | strategy behavior |
| trend_breakout_max_age_bars | TREND_BREAKOUT_MAX_AGE_BARS | 5 | — | — | strategies.trend_breakout_max_age_bars | python | bars | strategy behavior |
| trend_breakout_min_room_pips | TREND_BREAKOUT_MIN_ROOM_PIPS | 35 | — | — | strategies.trend_breakout_min_room_pips | python | pips | strategy behavior |
| trend_level_buffer_atr | TREND_LEVEL_BUFFER_ATR | 1.0 | — | — | strategies.trend_level_buffer_atr | python | atr | strategy behavior |
| trend_min_bos | TREND_MIN_BOS | 2 | — | — | strategies.trend_min_bos | python | count | strategy behavior |
| trend_min_height_atr | TREND_MIN_HEIGHT_ATR | 3.0 | — | — | strategies.trend_min_height_atr | python | atr | strategy behavior |
| watcher_ctrader_stale_seconds | WATCHER_CTRADER_STALE_SECONDS | 180 | — | — | market_data.watcher_ctrader_stale_seconds | python | seconds | infrastructure |
| weekly_report_dow | WEEKLY_REPORT_DOW | 6 | — | — | delivery.weekly_report_dow | python | day_of_week | delivery |
| weekly_report_enabled | WEEKLY_REPORT_ENABLED | True | — | — | delivery.weekly_report_enabled | python | boolean | delivery |
| weekly_report_hour | WEEKLY_REPORT_HOUR | 8 | — | — | delivery.weekly_report_hour | python | utc_hour | delivery |
| weekly_report_skip_empty | WEEKLY_REPORT_SKIP_EMPTY | False | — | — | delivery.weekly_report_skip_empty | python | boolean | delivery |
| xau_lookback_h1_bars | XAU_LOOKBACK_H1_BARS | 400 | — | — | market_data.xau_lookback_h1_bars | python | bars | infrastructure |
| xau_lookback_m15_bars | XAU_LOOKBACK_M15_BARS | 650 | — | — | market_data.xau_lookback_m15_bars | python | bars | infrastructure |
| xau_lookback_m1_bars | XAU_LOOKBACK_M1_BARS | 150 | — | — | market_data.xau_lookback_m1_bars | python | bars | infrastructure |
| xau_lookback_m5_bars | XAU_LOOKBACK_M5_BARS | 1000 | — | — | market_data.lookbacks.m5_bars | python | bars | infrastructure |
| xau_major_zone_max_width_price | XAU_MAJOR_ZONE_MAX_WIDTH_PRICE | 10.0 | — | — | analysis.xau_major_zone_max_width_price | python | price | analysis behavior |
| xau_zone_min_width_price | XAU_ZONE_MIN_WIDTH_PRICE | 3.0 | — | — | analysis.zones.symbol_contract.minimum_width_price | python | price | analysis behavior |
| xau_zone_preferred_max_width_price | XAU_ZONE_PREFERRED_MAX_WIDTH_PRICE | 6.0 | — | — | analysis.xau_zone_preferred_max_width_price | python | price | analysis behavior |
| xau_zone_preferred_min_width_price | XAU_ZONE_PREFERRED_MIN_WIDTH_PRICE | 3.0 | — | — | analysis.xau_zone_preferred_min_width_price | python | price | analysis behavior |
| zigzag_atr_mult | ZIGZAG_ATR_MULT | 1.0 | — | — | analysis.zigzag_atr_mult | python | atr | analysis behavior |
| zigzag_pct | ZIGZAG_PCT | 0.0 | — | — | analysis.zigzag_pct | python | percent | analysis behavior |
| zone_alert_ttl | ZONE_ALERT_TTL | 14400 | — | — | analysis.zone_alert_ttl | python | seconds | analysis behavior |
| zone_merge_gap | ZONE_MERGE_GAP | 1.0 | 1 | — | analysis.zones.confluence.merge_gap_price | python | price | analysis behavior |
| zone_merge_max_width | ZONE_MERGE_MAX_WIDTH | 6.0 | 6 | — | analysis.zone_merge_max_width | python | price | analysis behavior |
| zone_merge_overlap | ZONE_MERGE_OVERLAP | 0.5 | — | — | analysis.zone_merge_overlap | python | fraction | analysis behavior |
| zone_width | ZONE_WIDTH | body | — | — | analysis.zone_width | python | string | analysis behavior |

## 5. Duplicate/default conflict report

There are 30 unique verified conflict items:

- 18 Python-schema versus root-Compose default differences;
- 1 direct Python/C# conservative default mismatch;
- 3 Python/C# alias-contract mismatches for legacy logging aliases;
- 7 duplicated Python range-scalp defaults with different values;
- 1 C# versus `.env.example` default mismatch for `CTRADER_TIMEFRAMES`.

Formatting-only spellings such as `0.5` versus `0.50` and `100` versus `100.0`
were normalized before counting. Profile-intent differences are still listed
because Compose turns them into independent definitions. The effective
production column is intentionally not guessed from tracked defaults.

### C. Duplicate/conflict table

| Field | Definition locations | Conflicting values | Effective production value | Recommended source of truth |
|---|---|---|---|---|
| CTRADER_TIMEFRAMES | ctrader-engine/src/FeedOptions.cs:41 | Python=—; Compose=—; C#=M1,M5,M15,H1 | Ansible vault/process ENV; not derivable here | `analysis.ctrader_timeframes` catalog metadata |
| auto_trade_allow_concurrent_strategies | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=False; Compose=true; C#=False | Ansible vault/process ENV; not derivable here | `risk.allow_concurrent_strategies` catalog metadata |
| auto_trade_allow_counter_bias | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=True; Compose=true; C#=False | Ansible vault/process ENV; not derivable here | `execution.allow_counter_bias` catalog metadata |
| auto_trade_candidate_max_age_seconds | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=90; Compose=420; C#=90 | Ansible vault/process ENV; not derivable here | `lifecycle.candidate_max_age_seconds` catalog metadata |
| auto_trade_candidate_ttl | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=86400; Compose=604800; C#=86400 | Ansible vault/process ENV; not derivable here | `lifecycle.candidate.storage_ttl_seconds` catalog metadata |
| auto_trade_dry_run | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=True; Compose=false; C#=True | Ansible vault/process ENV; not derivable here | `contract.dry_run` catalog metadata |
| auto_trade_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=False; Compose=true; C#=False | Ansible vault/process ENV; not derivable here | `contract.enabled` catalog metadata |
| auto_trade_map_max_entry_drift_atr | algo-bot/app/core/config.py | Python=0.4; Compose=1.0; C#=— | Ansible vault/process ENV; not derivable here | `execution.map_max_entry_drift_atr` catalog metadata |
| auto_trade_mapped_zone_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=True; Compose=false; C#=True | Ansible vault/process ENV; not derivable here | `strategies.mapped_zone_enabled` catalog metadata |
| auto_trade_market_map_guard_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=True; Compose=false; C#=True | Ansible vault/process ENV; not derivable here | `actionability.market_map_guard_enabled` catalog metadata |
| auto_trade_non_hedged_opposite_policy | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=reject; Compose=broker_netting; C#=reject | Ansible vault/process ENV; not derivable here | `risk.non_hedged_opposite_policy` catalog metadata |
| auto_trade_profile | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=conservative; Compose=demo_eval; C#=conservative | Ansible vault/process ENV; not derivable here | `contract.profile` catalog metadata |
| auto_trade_range_flip_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=False; Compose=true; C#=False | Ansible vault/process ENV; not derivable here | `strategies.range_flip_enabled` catalog metadata |
| auto_trade_range_max_entry_drift_atr | algo-bot/app/core/config.py | Python=0.35; Compose=1.0; C#=— | Ansible vault/process ENV; not derivable here | `execution.range_max_entry_drift_atr` catalog metadata |
| auto_trade_range_two_sided_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=False; Compose=true; C#=False | Ansible vault/process ENV; not derivable here | `strategies.range_two_sided_enabled` catalog metadata |
| auto_trade_structural_guard_mode | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=balanced; Compose=observe; C#=balanced | Ansible vault/process ENV; not derivable here | `actionability.structural_guard_mode` catalog metadata |
| auto_trade_trend_max_entry_drift_atr | algo-bot/app/core/config.py | Python=0.85; Compose=1.5; C#=— | Ansible vault/process ENV; not derivable here | `execution.trend_max_entry_drift_atr` catalog metadata |
| auto_trade_zone_cooldown_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=True; Compose=false; C#=True | Ansible vault/process ENV; not derivable here | `lifecycle.zone_cooldown_enabled` catalog metadata |
| auto_trade_zone_fill_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=False; Compose=true; C#=False | Ansible vault/process ENV; not derivable here | `execution.zone_fill_enabled` catalog metadata |
| auto_trade_zone_reconcile_mode | algo-bot/app/core/config.py<br>ctrader-engine/src/AutoTra… | Python=enforce; Compose=shadow; C#=enforce | Ansible vault/process ENV; not derivable here | `actionability.zone_reconciliation.mode` catalog metadata |
| log_dir | algo-bot/app/core/config.py<br>ctrader-engine/src/DailyFi… | Python=/var/log/apexvoid; Compose=/var/log/apexvoid; C#=/… | Ansible vault/process ENV; not derivable here | `bootstrap.logging.directory` catalog metadata |
| log_file_enabled | algo-bot/app/core/config.py<br>ctrader-engine/src/DailyFi… | Python=True; Compose=true; C#=True | Ansible vault/process ENV; not derivable here | `bootstrap.log_file_enabled` catalog metadata |
| log_retention_days | algo-bot/app/core/config.py<br>ctrader-engine/src/DailyFi… | Python=14; Compose=14; C#=14 | Ansible vault/process ENV; not derivable here | `bootstrap.log_retention_days` catalog metadata |
| range_scalp_cluster_atr | algo-bot/app/core/config.py<br>algo-bot/app/analysis/engi… | Python=0.25; Compose=—; C#=—; mirrors=AnalysisSettings de… | Ansible vault/process ENV; not derivable here | `strategies.range_scalp_cluster_atr` catalog metadata |
| range_scalp_entry_tol_atr | algo-bot/app/core/config.py<br>algo-bot/app/analysis/engi… | Python=0.25; Compose=—; C#=—; mirrors=AnalysisSettings de… | Ansible vault/process ENV; not derivable here | `strategies.range_scalp_entry_tol_atr` catalog metadata |
| range_scalp_lookback | algo-bot/app/core/config.py<br>algo-bot/app/analysis/engi… | Python=48; Compose=—; C#=—; mirrors=AnalysisSettings defa… | Ansible vault/process ENV; not derivable here | `strategies.range_scalp_lookback` catalog metadata |
| range_scalp_min_room_atr | algo-bot/app/core/config.py<br>algo-bot/app/analysis/engi… | Python=0.75; Compose=—; C#=—; mirrors=AnalysisSettings de… | Ansible vault/process ENV; not derivable here | `strategies.range_scalp_min_room_atr` catalog metadata |
| range_scalp_min_touches | algo-bot/app/core/config.py<br>algo-bot/app/analysis/engi… | Python=2; Compose=—; C#=—; mirrors=AnalysisSettings defau… | Ansible vault/process ENV; not derivable here | `strategies.range_scalp_min_touches` catalog metadata |
| range_scalp_min_wick_frac | algo-bot/app/core/config.py<br>algo-bot/app/analysis/engi… | Python=0.25; Compose=—; C#=—; mirrors=AnalysisSettings de… | Ansible vault/process ENV; not derivable here | `strategies.range_scalp_min_wick_frac` catalog metadata |
| range_scalp_min_width_atr | algo-bot/app/core/config.py<br>algo-bot/app/analysis/engi… | Python=1.0; Compose=—; C#=—; mirrors=AnalysisSettings def… | Ansible vault/process ENV; not derivable here | `strategies.range_scalp_min_width_atr` catalog metadata |

Additional structural duplication without a current value disagreement is
recorded in each catalog row. Examples include 72 fields mirrored into
`AnalysisSettings`/`DetectorSettings`/module fallbacks, 47 C# options with no
Python `Settings` field, partial alias registries, and generated-health lists.

## 6. Profile behavior report

Python `conservative` is not a profile document. With default
`AUTO_TRADE_REQUIRE_DEMO_ACCOUNT=true`, it is the schema baseline. If demo
requirement is explicitly disabled and structural guard is not explicit, the
validator changes the guard to `strict`. C# expresses conservative through
fallback arguments and differs on counter-bias.

Python `demo_eval` declares 48 values. It also rejects
`AUTO_TRADE_REQUIRE_DEMO_ACCOUNT=false`. Twenty-three declared values currently
equal schema defaults; retaining them in a future document is still useful
because a profile should be complete and immutable, not depend on unrelated
future schema edits.

### D. Profile parity table

`Direct runtime` and `Compose runtime` below both select `demo_eval` and use the
real loader. A dash means root Compose does not define an explicit default for
that field.

| Field | Conservative | Demo profile | Compose default | Direct runtime | Compose runtime |
|---|---|---|---|---|---|
| auto_trade_profile | conservative | demo_eval | demo_eval | demo_eval | demo_eval |
| auto_trade_allow_concurrent_strategies | False | True | true | True | True |
| auto_trade_allow_counter_bias | True | True | true | True | True |
| auto_trade_allow_hedged_xau | False | True | — | True | True |
| auto_trade_breakout_enabled | True | True | — | True | True |
| auto_trade_candidate_max_age_seconds | 90 | 420 | 420 | 420 | 420 |
| auto_trade_candidate_ttl | 86400 | 604800 | 604800 | 604800 | 604800 |
| auto_trade_demand_reaction_enabled | True | True | true | True | True |
| auto_trade_dry_run | True | False | false | False | False |
| auto_trade_enabled | False | True | true | True | True |
| auto_trade_key_level_reaction_enabled | True | True | true | True | True |
| auto_trade_liquidity_reversal_enabled | True | True | — | True | True |
| auto_trade_map_counter_bias_enabled | True | True | — | True | True |
| auto_trade_map_hard_entry_drift_pips | 20.0 | 20.0 | 20 | 20.0 | 20.0 |
| auto_trade_map_max_entry_drift_atr | 0.4 | 1.0 | 1.0 | 1.0 | 1.0 |
| auto_trade_map_min_entry_drift_pips | 10.0 | 10.0 | 10 | 10.0 | 10.0 |
| auto_trade_mapped_zone_enabled | True | True | false | True | False |
| auto_trade_market_map_guard_enabled | True | True | false | True | False |
| auto_trade_max_active_positions_per_symbol | 1 | 0 | — | 0 | 0 |
| auto_trade_max_tracked_candidates | 5 | 0 | — | 0 | 0 |
| auto_trade_multi_match_enabled | False | True | — | True | True |
| auto_trade_non_hedged_opposite_policy | reject | broker_netting | broker_netting | broker_netting | broker_netting |
| auto_trade_opposing_barrier_veto_enabled | True | False | — | False | False |
| auto_trade_overlap_veto_enabled | True | False | — | False | False |
| auto_trade_range_enabled | True | True | — | True | True |
| auto_trade_range_flip_enabled | False | True | true | True | True |
| auto_trade_range_hard_entry_drift_pips | 20.0 | 20.0 | 20 | 20.0 | 20.0 |
| auto_trade_range_max_entry_drift_atr | 0.35 | 1.0 | 1.0 | 1.0 | 1.0 |
| auto_trade_range_min_entry_drift_pips | 10.0 | 10.0 | 10 | 10.0 | 10.0 |
| auto_trade_range_two_sided_enabled | False | True | true | True | True |
| auto_trade_reaction_enabled | True | True | — | True | True |
| auto_trade_require_demo_account | True | True | true | True | True |
| auto_trade_require_flat_for_range | True | False | — | False | False |
| auto_trade_retest_enabled | True | True | — | True | True |
| auto_trade_session_level_reaction_enabled | True | True | true | True | True |
| auto_trade_strategy_match_enabled | True | True | — | True | True |
| auto_trade_structural_guard_mode | balanced | observe | observe | observe | observe |
| auto_trade_structural_reaction_lookback_bars | 3 | 3 | 3 | 3 | 3 |
| auto_trade_supply_reaction_enabled | True | True | true | True | True |
| auto_trade_track_all_structural_matches | False | True | — | True | True |
| auto_trade_trend_enabled | False | True | — | True | True |
| auto_trade_trend_hard_entry_drift_pips | 30.0 | 30.0 | 30 | 30.0 | 30.0 |
| auto_trade_trend_max_entry_drift_atr | 0.85 | 1.5 | 1.5 | 1.5 | 1.5 |
| auto_trade_trend_min_entry_drift_pips | 15.0 | 15.0 | 15 | 15.0 | 15.0 |
| auto_trade_trendline_reaction_enabled | True | True | true | True | True |
| auto_trade_zone_cooldown_enabled | True | False | false | False | False |
| auto_trade_zone_fill_enabled | False | True | true | True | True |
| auto_trade_zone_reconcile_mode | enforce | shadow | shadow | shadow | shadow |
| scanner_top_n | 3 | 0 | — | 0 | 0 |

### Replacement profile architecture

Profiles should be immutable nested documents keyed by canonical paths, for
example `profiles/demo_eval.py` exporting a frozen `DemoEvalProfile` or
`MappingProxyType`. The loader should deep-merge raw mappings in this fixed
order:

```text
schema defaults
  → selected immutable profile document
  → .env values
  → process environment values
  → one complete ApexVoidConfig validation
```

The selected profile name must be resolved before the profile document is
loaded. No `setattr`, no mutation after validation, and no `model_fields_set`
dependency should remain. Compose and Jinja should select the profile and pass
true operator overrides only; they must not restate profile values. Phase 2
must first capture golden parity, because simply removing current Compose
defaults would change the two mapped-zone values.

## 7. Python/C# shared-contract report

The audit identifies 95 shared items: 83 Python `Settings` fields, two C# option
rows consumed/hardcoded by Python, one direct shared operational ENV, and nine
shared constants. Of these, 86 are environment/option bindings and nine are
protocol/key constants.

The config-health manifest does not cover every shared item. The catalog's
`cross_service.config_health_manifest` is `fatal`, `warning`, or
`not_reported`, derived from the current comparison code. Boolean parsing is
also not identical: Pydantic and the C# custom parser do not advertise the same
accepted spelling set. `AUTO_TRADE_CONTRACT_MODE` validation is deliberately
different today: Python permits only `v7_only`; C# `Validate()` accepts four
historical values, while the real C# ENV fallback is `v7_only` and the bare
record default is `legacy_v6` for old tests.

### E. Cross-service contract table

| Field | Python | C# | Match state | Required action |
|---|---|---|---|---|
| AUTO_TRADE_EXPECTED_BROKER | — | fpmarkets | default drift | Generate binding; retain warning health policy |
| BARS_CHANNEL | bars:new | bars:new | no shared ENV | Centralize shared stream binding |
| GIT_SHA | unknown | unknown | match | Generate binding; retain warning health policy |
| contract.keys.config_health | auto_trade:config_health | auto_trade:config_health | no shared ENV | Keep constant; generate parity assertion |
| contract.versions.config_manifest | 2 | 2 | no shared ENV | Keep constant; generate parity assertion |
| contract.keys.ctrader_manifest | auto_trade:config_manifest:ctrader | auto_trade:config_manifest:ctrader | no shared ENV | Keep constant; generate parity assertion |
| contract.versions.entry_plan | 1 | 1 | no shared ENV | Keep constant; generate parity assertion |
| contract.keys.executor_readiness | auto_trade:executor_readiness | auto_trade:executor_readiness | no shared ENV | Keep constant; generate parity assertion |
| contract.keys.python_manifest | auto_trade:config_manifest:python | auto_trade:config_manifest:python | no shared ENV | Keep constant; generate parity assertion |
| contract.versions.stop_plan | 3 | 3 | no shared ENV | Keep constant; generate parity assertion |
| contract.keys.tracked_positions | auto_trade:positions | auto_trade:positions | no shared ENV | Keep constant; generate parity assertion |
| contract.versions.trade_plan | 7 | 7 | no shared ENV | Keep constant; generate parity assertion |
| AUTO_TRADE_ADD_MIN_STOP_PIPS | 30 | 30 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_ADD_RISK_FRACTION | 0.5 | 0.5 | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_ADD_SIZE_RATIO | 0.5 | 0.5 | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_ADD_STOP_BUFFER_ATR | 0.3 | 0.3 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_ALLOW_CONCURRENT_STRATEGIES | False | False | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_ALLOW_COUNTER_BIAS | True | False | default drift, parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_ALLOW_HEDGED_XAU | False | False | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_BE_BUFFER_TICKS | 6 | 6 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_BREAKOUT_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_CANDIDATE_CONTRACT_VERSION | 6 | 6 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_CANDIDATE_MAX_AGE_SECONDS | 90 | 90 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_CANDIDATE_STORAGE_TTL_SECONDS | 86400 | 86400 | match | Generate binding; retain warning health policy |
| AUTO_TRADE_CANONICAL_SYMBOL | XAU | XAU | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_CONTRACT_MODE | v7_only | v7_only | validation drift | Align allowed values; keep fatal manifest check |
| AUTO_TRADE_XAU_CONTRACT_SIZE | 100.0 | 100 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_DRY_RUN | True | True | parser drift | Generate binding; retain fatal health policy |
| AUTO_TRADE_ENABLED | False | False | parser drift | Generate binding; retain fatal health policy |
| AUTO_TRADE_ENTRY_CONTRACT_TOLERANCE_PIPS | 3.0 | 3 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_EQUITY_TABLE_VERSION | owner_equity_v1 | owner_equity_v1 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_EVENT_STREAM | auto_trade:events | auto_trade:events | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_ATR | 2.0 | 2.0 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_EXECUTION_ZONE_MAX_WIDTH_PIPS | 100.0 | 100 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_GROUP_CLOSE_ALLOCATION | pro_rata | pro_rata | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_INSIDE_ZONE_MARKET_ENTRY_ENABLED | True | True | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_LIQUIDITY_REVERSAL_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_MAP_THESIS_LOCK_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_MAPPED_ZONE_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_MARKET_MAP_GUARD_ENABLED | True | True | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_MAX_ENTRY_DISTANCE_PIPS | 40.0 | 40 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_MAX_TRANCHES | 2 | 2 | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_MIN_CONFLUENCE | 2 | 2 | match | Generate binding; retain warning health policy |
| AUTO_TRADE_MULTI_MATCH_ENABLED | False | False | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_NON_HEDGED_OPPOSITE_POLICY | reject | reject | match | Generate binding; retain warning health policy |
| AUTO_TRADE_POST_FILL_TARGET_FALLBACK | fill_relative | fill_relative | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_PROFILE | conservative | conservative | match | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_BOX_MOVE_SL_TO_BE_AFTER_SCALE_OUT | False | False | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_BOX_SCALE_OUT_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION | 0.5 | 0.5 | match | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS | 70 | 70 | match | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS | 30 | 30 | match | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_FLIP_ENABLED | False | False | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_RANGE_TARGETS_PIPS | 15,20,30,40,50,70 | 15,20,30,40,50,70 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_RANGE_TP_BUFFER_PIPS | 3.0 | 3 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_RANGE_TWO_SIDED_ENABLED | False | False | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_REACTION_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_REACTION_MARKET_FRACTION | 0.7 | 0.7 | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_REACTION_SCALE_ENABLED | False | False | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_REACTION_SCALE_FRACTION | 0.3 | 0.3 | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_REACTION_SCALE_INVALID_POLICY | single_market | single_market | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_REACTION_SCALE_STEP_ATR | 0.5 | 0.5 | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_REQUIRE_DEMO_ACCOUNT | True | True | parser drift | Generate binding; retain fatal health policy |
| AUTO_TRADE_REQUIRE_FLAT_FOR_RANGE | True | True | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_RETEST_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_SIZING_MODE | equity_table | equity_table | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_SL_DISTANCE | 6.5 | 6.5 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_SPOT_MAX_AGE_SECONDS | 5 | 5 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_STOP_PUSH_BEYOND_ZONE | True | True | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_STRATEGY_MATCH_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_CANDIDATE_STREAM | auto_trade:candidates | auto_trade:candidates | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_STRUCTURAL_GUARD_MODE | balanced | balanced | match | Generate binding; retain warning health policy |
| AUTO_TRADE_SYMBOLS | XAU | XAU | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_TARGET_PLANS_PIPS | 30,60,90,120,200 | 30,60,90,120,200 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_TRACK_ALL_STRUCTURAL_MATCHES | False | False | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_TRADE_PLAN_STREAM | execution:trade_plans | execution:trade_plans | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_TREND_ENABLED | False | False | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_TREND_STOP_MAX_PIPS | 60 | 60 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_TREND_STOP_MIN_PIPS | 40 | 40 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_UNFILLED_LEG_AFTER_TP_POLICY | cancel | cancel | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_WICK_STOP_BUFFER_ATR | 0.15 | 0.15 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_XAU_PIP_SIZE | 0.1 | 0.1 | match | Generate binding; retain fatal health policy |
| AUTO_TRADE_ZONE_COOLDOWN_ENABLED | True | True | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_ZONE_FILL_ENABLED | False | False | parser drift | Generate binding; retain warning health policy |
| AUTO_TRADE_ZONE_FILL_FALLBACK_ENABLED | True | True | parser drift | Assign owner; add generated parity or document constant |
| AUTO_TRADE_ZONE_FILL_MIN_ATR | 0.5 | 0.5 | match | Assign owner; add generated parity or document constant |
| AUTO_TRADE_ZONE_RECONCILE_MODE | enforce | enforce | match | Generate binding; retain warning health policy |
| AUTO_TRADE_ZONE_SCALE_UNDERSIZED_POLICY | single_entry | single_entry | match | Generate binding; retain fatal health policy |
| LOG_DIR | /var/log/apexvoid | /var/log/apexvoid | match | Assign owner; add generated parity or document constant |
| LOG_FILE_ENABLED | True | True | parser drift | Assign owner; add generated parity or document constant |
| LOG_RETENTION_DAYS | 14 | 14 | match | Assign owner; add generated parity or document constant |
| MANUAL_ALGO_ENABLED | False | False | parser drift | Generate binding; retain warning health policy |
| MANUAL_TRADE_COMMAND_STREAM | manual_trade:commands | manual_trade:commands | no shared ENV | Centralize shared stream binding |
| REDIS_URL | redis://redis:6379/0 | redis://redis:6379/0 | match | Generate binding; retain fatal health policy |

### Ownership direction

- **Validate identically in both services:** connection identity, Redis DB,
  selected profile/account mode, candidate/TradePlan versions, stream names,
  instrument pip/price/contract units, and every value required to interpret a
  TradePlan safely.
- **Python-owned and embedded into TradePlan V7:** strategy selection,
  confluence/actionability decisions, planned entry and absolute stop/targets,
  target-room caps, structural evidence, risk tier selection, and strategy
  lifecycle timestamps. C# should validate the plan contract rather than
  recompute strategy policy.
- **C#-owned:** broker credentials/host/account, token rotation, request/feed
  transport, spread and broker-submit mechanics, broker recovery quorum,
  position reconciliation, lot normalization, and execution polling.
- **Shared but generated constants:** manifest/entry/stop/TradePlan versions,
  Redis manifest/readiness keys, and any stream/key read by both languages.
- **Compatibility-only:** deprecated PIPS/ticks, stream, market-map and forming
  aliases. These stay accepted during migration but are emitted from catalog
  metadata, not separate registries.

## 8. Proposed grouped model hierarchy

The proposed roots are broadly sound, but three refinements are necessary:

1. `AUTO_TRADE_PROFILE`, service metadata and logging are bootstrap concerns,
   not strategy or contract values. The chosen profile determines a source
   layer; it is not itself the execution contract.
2. Scanner card limits and Telegram lifecycle presentation belong to delivery;
   scanner data acquisition/lookbacks belong to market data; detector
   thresholds belong to analysis. The current `scanner_*` prefix combines all
   three responsibilities.
3. Protocol versions and Redis contract keys should normally be typed constants
   generated for both languages, not operator-tunable configuration. They are
   catalogued so parity is enforceable, but should have `configurable=false` in
   Phase 2.

Recommended root:

```text
ApexVoidConfig
├── bootstrap
│   ├── postgres, redis, telegram, logging, build, runtime
├── market_data
│   ├── feed, scanner, lookbacks, spot, sessions, calendar
├── analysis
│   ├── atr, swings, structure, levels, zones, liquidity, market_map,
│   │   regime, trendlines, triggers
├── strategies
│   ├── reaction, range_reversion, breakout, trend, mapped_zone,
│   │   liquidity_reversal, auto_scalp
├── actionability
│   ├── contested_corridor, key_level_role, target_room, higher_timeframe,
│   │   overlapping_zones, zone_reconciliation
├── contract
│   ├── versions, streams, keys, instrument, account
├── execution
│   ├── entry, targeting, stops, scaling, fill, flip, confirmation
├── risk
│   ├── sizing, tiers, exposure, position_limits
├── lifecycle
│   ├── candidate, setup, strategy_match, range_box, zone_watch, executor
├── delivery
│   ├── telegram, scanner_cards, market_map, reports, chart_analysis
└── manual_algo
    ├── execution, sizing, delivery, streams, scaling
```

`market_data.calendar` is preferred over a new calendar root because feeds and
event freshness are market inputs. Calendar notification schedules remain in
`delivery.reports`. Observability stays under `bootstrap.logging` and
`bootstrap.build` until it needs enough policy to justify a root.

### F. Domain grouping table

| Domain | Responsibility | Current items | Proposed model | Migration priority |
|---|---|---:|---|---|
| `bootstrap` | Process dependencies, secrets, logging, build identity | 13 | `BootstrapConfig` with postgres/redis/telegram/logging/build/runtime | P0 foundation |
| `market_data` | Feed, bars, scanner acquisition, freshness, sessions, calendar | 49 | `MarketDataConfig` | P1 |
| `analysis` | Pure PA measurements, zones, levels, scoring and regime | 82 | `AnalysisConfig` | P1 |
| `strategies` | Strategy enablement and strategy-specific detector policy | 73 | `StrategiesConfig` | P1 |
| `actionability` | Cross-strategy veto, room, ambiguity and reconciliation | 26 | `ActionabilityConfig` | P0/P1 |
| `contract` | Cross-service versions, streams, keys, instrument/account interpretation | 26 | `ContractConfig` plus generated constants | P0 |
| `execution` | Entry, stops, targets, scale/fill/confirmation mechanics | 87 | `ExecutionConfig` | P0 |
| `risk` | Sizing, exposure and position limits | 19 | `RiskConfig` | P0 |
| `lifecycle` | Age, TTL, cooldown, retirement, leases and rearm | 28 | `LifecycleConfig` | P1 |
| `delivery` | Telegram cards, reports and chart-analysis presentation | 24 | `DeliveryConfig` | P2 |
| `manual_algo` | Owner-triggered execution path | 10 | `ManualAlgoConfig` | P1 |

The counts above include catalogued constants and therefore total 437. Contract
has 26 after adding ten protocol/key constants and the expected-broker binding; the machine catalog is the
authoritative count if this document is regenerated.

### Ambiguities to resolve before implementation

- Parent and child switches such as reaction plus individual reaction sources,
  range plus two-sided/flip/scale-out, mapped-zone plus market-map guard, and
  scanner plus actionability gates need documented boolean precedence.
- `auto_trade_sl_distance` is a price distance despite its auto-trade naming;
  unit must be explicit in the nested name or metadata.
- `scanner_level_bucket`, `map_fallback_radius`, `map_scalp_radius`,
  `zone_merge_gap`, and absolute zone widths need explicit price-unit naming.
- `AUTO_TRADE_BE_BUFFER_PIPS` is a legacy name whose value is ticks. It should
  remain an alias only and be marked unit `ticks`.
- `scanner_top_n=0` means unlimited under demo profile, while other `*_max*`
  zero values also mean unlimited. Constraints must document sentinel meaning.
- The auto-scalp gate has 15+ threshold constants outside root settings; decide
  which are immutable algorithm constants and which are operator policy. Do
  not expose them merely because they were found.
- `AUTO_TRADE_EXPECTED_BROKER` is read directly in Python config health but has
  no Python field. It belongs under `contract.account.expected_broker` and must
  remain secret-safe in health output.

## 9. Proposed canonical catalog schema

### Metadata types

```python
class ConfigOwner(StrEnum):
  PYTHON = "python"
  CTRADER = "ctrader"
  SHARED = "shared"

class ReloadPolicy(StrEnum):
  RESTART = "restart"
  NEXT_SCANNER_CYCLE = "next_scanner_cycle"
  NEXT_WORKER_CYCLE = "next_worker_cycle"
  NEW_SETUP_ONLY = "new_setup_only"
  IMMEDIATE = "immediate"

class ConfigUnit(StrEnum):
  PRICE = "price"
  PIPS = "pips"
  ATR = "atr"
  BARS = "bars"
  SECONDS = "seconds"
  MINUTES = "minutes"
  HOURS = "hours"
  MILLISECONDS = "milliseconds"
  TICKS = "ticks"
  FRACTION = "fraction"
  PERCENT = "percent"
  COUNT = "count"
  LOTS = "lots"
  STRING = "string"
  BOOLEAN = "boolean"
```

The implementation may add `Configurable`/`ProtocolConstant` and a
`MismatchPolicy` enum (`fatal`, `warning`, `not_reported`). Ownership should not
be inferred from prefixes.

### Field API

```python
def config_field(
  default: T,
  *,
  legacy_attr: str,
  env: str | None,
  aliases: tuple[str, ...] = (),
  owner: ConfigOwner,
  reload: ReloadPolicy,
  unit: ConfigUnit,
  secret: bool = False,
  shared_with_ctrader: bool = False,
  deprecated: bool = False,
  configurable: bool = True,
  mismatch_policy: MismatchPolicy = MismatchPolicy.NOT_REPORTED,
  description: str,
  constraints: Mapping[str, object] | None = None,
) -> Any:
  return Field(
    default,
    validation_alias=AliasChoices(env, *aliases) if env else None,
    json_schema_extra={"apexvoid_config": ConfigMetadata(...)},
  )
```

Metadata is declared on the nested typed field exactly once. A catalog builder
walks `ApexVoidConfig.model_fields`, recursively reads
`json_schema_extra["apexvoid_config"]`, and derives:

- canonical/deprecated environment resolution and conflict detection;
- the flat legacy-attribute facade map;
- generated JSON catalog and configuration documentation;
- secret redaction and config-health manifest fields;
- profile path/type validation;
- generated C# shared-option metadata and parity fixtures;
- Compose/Ansible allowed-key and default validation.

`environment_options.py`, `_CANONICAL_ENV_NAMES`, `_PROFILE_DEFAULT_FIELDS`,
`_LEGACY_ENV_ALIASES`, documentation lists, and hand-written alias maps must not
remain independent registries. Generated artifacts are checked in only when
CI can prove they match the typed model.

Catalog invariants:

- every path and legacy attribute is unique;
- every canonical ENV is unique;
- every alias has exactly one owner and cannot be another field's canonical
  name;
- numeric strategy/execution fields require unit metadata;
- secrets cannot participate in unredacted health/doc output;
- shared fields require C# type/unit/default/validation metadata and a mismatch
  policy;
- constants require `configurable=false` and cannot acquire ENV aliases;
- profiles may reference only catalog paths and must type-check before startup.

## 10. Legacy compatibility strategy

The nested model is frozen after startup. Existing code receives a read-only
facade during migration:

```python
class LegacySettingsFacade:
  __slots__ = ("_config",)

  def __init__(self, config: ApexVoidConfig) -> None:
    object.__setattr__(self, "_config", config)

  def __getattr__(self, name: str) -> object:
    path = GENERATED_LEGACY_ATTR_TO_PATH.get(name)
    if path is None:
      raise AttributeError(name)
    return resolve_path(self._config, path)

  def __setattr__(self, name: str, value: object) -> None:
    raise TypeError("configuration is immutable after startup")

config = load_config()
settings = LegacySettingsFacade(config)
```

Examples generated from catalog metadata:

```text
settings.auto_trade_min_confluence
  → config.execution.minimum_confluence
settings.contested_corridor_gap_atr
  → config.actionability.contested_corridor.gap_atr
settings.auto_trade_tp_pips
  → config.execution.targeting.default_ladder_pips
```

No handwritten catch-all fallback is allowed. Unknown legacy names raise
`AttributeError`; duplicate legacy mappings fail catalog construction; no new
flat field may be introduced after the freeze test lands. Consumers migrate by
domain while both APIs return the exact same immutable value.

## 11. Proposed package/file layout

```text
algo-bot/app/configuration/
  __init__.py                 # config singleton + temporary settings facade
  metadata.py                 # enums, ConfigMetadata, config_field
  loader.py                   # deterministic source merge + root validation
  sources.py                  # profile/.env/process-env adapters
  catalog.py                  # recursive metadata traversal and invariants
  facade.py                   # generated legacy attribute access
  profiles/
    conservative.py
    demo_eval.py
  models/
    root.py
    bootstrap.py
    market_data.py
    analysis.py
    strategies.py
    actionability.py
    contract.py
    execution.py
    risk.py
    lifecycle.py
    delivery.py
    manual_algo.py

contracts/configuration/
  config-catalog.generated.json
  shared-config.generated.json

ctrader-engine/src/Configuration/
  GeneratedSharedConfig.cs
  SharedConfigValidator.cs
  EnvironmentBinding.cs

scripts/
  generate_config_artifacts.py
  verify_config_catalog.py
```

`config_health.py` should consume a generated shared manifest descriptor, not
move wholesale into the configuration package: comparison and publication are
operational health responsibilities; field selection and secrecy metadata come
from the catalog.

## 12. Migration phases

1. Add catalog invariants and golden snapshots for the current loader, profiles,
   Compose, production-template defaults, and Python/C# manifest without
   changing imports.
2. Add metadata enums/helper and frozen nested model types beside `Settings`;
   populate them mechanically from this inventory.
3. Generate catalog, docs, legacy map and C# shared descriptor; fail CI on
   drift, duplicates, missing units or secret exposure.
4. Add immutable profile documents and a shadow resolver. Compare every value
   against current direct/Compose fixtures; do not activate it yet.
5. Add the deterministic source loader and full root validation in shadow mode.
6. Activate the root model behind the generated legacy facade only after exact
   parity. Existing consumers continue using `settings.*`.
7. Migrate market-data, analysis, strategy, actionability, lifecycle, delivery
   and manual consumers one domain per commit.
8. Migrate contract/execution/risk last; generate C# binding metadata and retain
   all existing fatal health checks.
9. Replace direct environment reads and duplicate registries with catalog
   traversal; keep every production ENV/alias accepted.
10. Only after explicit operator decisions, simplify Compose/Jinja defaults and
    remove mirror dataclass/constants. Any intentional behavior change belongs
    in a separate PR, not the refactor.

The executable commit breakdown is in
[`config-refactor-phase-2-plan.md`](config-refactor-phase-2-plan.md).

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Silent default change during nesting | Golden snapshots for all 316 fields in direct conservative, direct demo and root Compose demo modes |
| Profile behavior changes when Compose defaults are removed | Preserve current effective Compose fixture first; resolve the two mapped-zone decisions explicitly |
| C# execution interprets a plan with different units | Generated shared descriptor plus fatal parity for instrument/contract/stop/target fields |
| Secrets leak through generated docs/health | Catalog secret metadata, redaction test and generated-output allowlist |
| Legacy consumer typo silently resolves | Exact generated map; unknown names raise `AttributeError` |
| New flat setting reintroduces debt | AST/catalog CI guard forbids fields outside nested models/facade |
| Hot-reload expectations alter live setups | Phase 2 remains startup-only; proposed reload metadata is descriptive until a separately approved phase |
| Constants become unsafe operator knobs | Require an explicit `configurable` decision; protocol versions/keys default to constants |
| Production vault differs from tracked fixtures | Deploy renders a redacted catalog fingerprint and validates it against both service manifests |
| Large all-at-once migration | Small domain commits with existing scanner/worker/TradePlan/executor suites unchanged |

## 14. Open decisions

1. Should root/local Compose preserve mapped-zone `false` under demo evaluation,
   or should it inherit the Python/C# direct-profile `true`? This is a behavior
   decision, not part of the refactor.
2. Is conservative counter-bias canonically `true` (Python) or `false` (C#)?
3. Is the cTrader default timeframe set `M1,M5,M15,H1` or
   `M1,M5,M15,M30`? Production template and example currently choose M30.
4. Which range-scalp default set is authoritative: `Settings`/`DetectorSettings`
   or `AnalysisSettings`/module fallbacks?
5. Should `BARS_CHANNEL` and `MANUAL_TRADE_COMMAND_STREAM` become true shared
   fields, or immutable generated constants? Current one-sided configurability
   is misleading.
6. Which auto-scalp gate thresholds are algorithm constants versus approved
   operator policy? Default recommendation: constants until replay evidence
   justifies configurability.
7. Should config-health fail fatally on every shared execution field, or should
   Python-owned values disappear from C# ENV and arrive only in TradePlan V7?
8. Should generated artifacts be checked in, or generated in CI/build only?
   Recommendation: check in JSON and C# output, with a no-diff regeneration
   check for reviewability.
9. Should future reload policies ever be activated? Phase 2 should implement
   restart-only loading despite metadata describing a potential future policy.

## 15. Phase 2 implementation backlog and exact test design

Phase 2 must add the following tests before switching consumers:

1. `test_every_legacy_settings_field_is_catalogued_exactly_once`: introspect the
   legacy model/facade, compare exact sets, and reject missing/extra/duplicate
   mappings.
2. `test_every_canonical_env_is_unique`: traverse all nested fields and assert
   a one-to-one non-null ENV map.
3. `test_every_deprecated_alias_has_one_owner`: assert no alias is duplicated or
   collides with a canonical ENV.
4. `test_no_direct_os_environment_reads_outside_loader`: AST scan production
   Python, allow only the configuration source adapter.
5. `test_no_direct_environment_reads_outside_csharp_binding`: source scan C#,
   allow only the generated/binding layer and entrypoint healthcheck shim.
6. `test_no_base_settings_outside_configuration_package`: AST scan imports and
   subclasses.
7. `test_grouped_schema_defaults_equal_legacy_defaults`: compare all 316 values
   under minimal conservative input.
8. `test_root_compose_effective_parity`: resolve `docker compose config`, load
   old and new models, and compare every field.
9. `test_production_template_default_keys_are_catalogued`: render the Jinja
   template with a fixture vault and reject unknown/missing shared keys.
10. `test_conservative_profile_parity` and `test_demo_eval_profile_parity`:
    compare old/new direct values and explicit-set behavior, including the two
    known Compose divergences as named fixtures.
11. `test_profile_precedence_env_over_dotenv_over_profile_over_schema`: use four
    distinct values and assert deterministic layer order.
12. `test_profile_documents_are_immutable_and_complete`: mutation fails and
    every intended profile-controlled path is present/type-valid.
13. `test_python_ctrader_shared_option_parity`: compare generated canonical
    ENV/type/default/unit/parser/constraints/allowed-values/mismatch policy.
14. `test_protocol_constants_generated_for_both_languages`: compare config,
    entry, stop and TradePlan versions plus shared Redis keys.
15. `test_generated_catalog_is_current`: regenerate to a temporary buffer and
    assert byte-for-byte equality.
16. `test_secret_catalog_fields_are_redacted_from_health_and_docs`: seed unique
    secret sentinels and assert none appear in output.
17. `test_numeric_trading_fields_have_units`: fail on missing/`string` units for
    numeric strategy, actionability, execution and risk fields.
18. `test_unknown_legacy_attribute_raises_attribute_error` and
    `test_legacy_facade_is_immutable`.
19. `test_no_new_flat_settings_fields`: freeze the legacy attribute set and
    require all additions under a nested model.
20. `test_existing_scanner_worker_tradeplan_tests_unchanged`: run the existing
    Python suite without editing expectations.
21. `test_existing_ctrader_executor_tests_unchanged`: build/run the existing C#
    suite, including ENV resolver, manifest and direct record-constructor tests.
22. `test_compose_config_valid`: run `docker compose config -q` with an empty
    temporary `.env`.

### Phase 1 verification performed

- fetched `origin/master` and recorded exact baseline;
- repository-wide `rg`/AST searches for BaseSettings, `Field`, aliases,
  `os.getenv`, `os.environ`, dotenv, C# `Environment.GetEnvironmentVariable`,
  resolver calls, Compose/Jinja ENV, hardcoded thresholds/timeframes/streams,
  Dockerfiles, workflows, tests and configuration docs;
- introspected the real 316-field Pydantic schema;
- parsed 126 C# AutoTrade/Feed ENV bindings plus C# logging/direct ENV;
- executed `docker compose config --format json` and loaded its exact bot ENV
  through the real `Settings` class;
- instantiated direct conservative and demo profiles with `_env_file=None`;
- normalized numeric spellings before conflict counting;
- validated catalog JSON shape/counts, canonical ENV uniqueness, alias
  uniqueness, proposed path uniqueness, ownership, units and reload metadata;
- no runtime test expectations were changed because Phase 1 changes no runtime
  code.
