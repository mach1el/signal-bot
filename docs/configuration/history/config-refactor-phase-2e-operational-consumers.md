# Phase 2E: authority-neutral operational configuration consumers

## 1. Scope and non-goals

Phase 2E changes how Python operational consumers read configuration. It
migrates direct legacy reads whose canonical root is `bootstrap`, `delivery`,
or `market_data` and whose canonical leaf has one direct legacy owner. It does
not change configuration meaning, defaults, ENV names, source precedence,
validation, task order, Telegram routing, market analysis, execution, risk,
Compose, deployment templates, C#, or CI.

`APEXVOID_CONFIG_AUTHORITY` still defaults to `legacy`. Canonical authority is
still an explicit restart-time selection, and restart rollback to legacy is
unchanged.

## 2. Why an authority-neutral nested view is required

Operational code needs one grouped API regardless of which authority was
selected at process start. Loading the canonical resolver while legacy is
authoritative would introduce a second source of truth and could make rollback
depend on canonical-only inputs. The `runtime_config` export avoids that:

- canonical mode exposes the validated `PythonRuntimeConfig` instance;
- legacy mode exposes a lazy `LegacyCanonicalConfigView` backed exclusively by
  the already-created authoritative `Settings` singleton.

The compatibility `settings` export remains unchanged in both modes.

## 3. Canonical mode behavior

`app.core.config` loads the canonical source bundle once, validates the Python
projection, builds `CanonicalSettingsFacade` for legacy callers, and publishes
the same validated `PythonRuntimeConfig` as `runtime_config`. There is no
fallback to legacy when canonical validation fails.

## 4. Legacy-backed view behavior

`LegacyCanonicalConfigView` and `LegacyCanonicalNode` resolve a leaf only when
it is accessed. Traversal uses generated canonical prefixes, and leaf lookup
uses the generated reverse ownership map. The view:

- returns the exact legacy value and exact Python type;
- has no instance `__dict__`;
- rejects assignment and deletion;
- raises `AttributeError` for unknown or non-legacy-backed leaves;
- does not read ENV, source bundles, generated JSON, or invoke the canonical
  resolver;
- has a value-free, secret-safe representation.

## 5. Supported canonical path policy

A production read can migrate in Phase 2E only when its full canonical path is
present in `CANONICAL_PATH_TO_LEGACY_ATTR`. This excludes algorithm/protocol
constants, environment-only fields, cTrader-only fields, and canonical leaves
without direct Python legacy ownership. Derived compatibility properties also
remain deferred because they do not directly own a canonical leaf.

## 6. Generated reverse access contract

The deterministic generator reverses `DIRECT_LEGACY_PATHS` into an immutable
316-entry `CANONICAL_PATH_TO_LEGACY_ATTR`. Duplicate canonical ownership fails
generation. `CANONICAL_LEGACY_PATH_PREFIXES` contains all 427 leaf and
intermediate traversal prefixes. Both contracts are generated Python data;
runtime code never reads the JSON artifacts.

## 7. Migration manifest

`consumer-migration-phase-2e.generated.json` combines the typed catalog, direct
legacy ownership, and AST usage audit. Its final counts are:

| Measure | Count |
|---|---:|
| Eligible production reads before migration | 127 |
| Migrated reads | 127 |
| Eligible reads remaining | 0 |
| Deferred reads | 287 |
| Unknown blockers | 0 |

The generated manifest is the line-level authoritative ledger. The table below
summarizes the primary operational mappings; all repeated call sites and the
complete 127-read inventory remain in the manifest.

| File | Legacy attribute | Canonical path | Authority-neutral support | Migration status | Test coverage |
|---|---|---|---|---|---|
| `app/main.py` | `log_level` | `bootstrap.logging.level` | yes | migrated | logging parity + regression |
| `app/main.py` | `log_dir` | `bootstrap.logging.directory` | yes | migrated | logging parity + regression |
| `app/main.py` | `log_retention_days` | `bootstrap.logging.retention_days` | yes | migrated | logging parity + regression |
| `app/main.py` | `log_file_enabled` | `bootstrap.logging.file_enabled` | yes | migrated | logging parity + regression |
| `app/persistence/store.py` | `database_url` | `bootstrap.postgres.url` | yes | migrated | config + persistence regression |
| `app/persistence/redis_state.py` | `redis_url` | `bootstrap.redis.url` | yes | migrated | config + startup regression |
| `app/bot/client.py` | `telegram_bot_token` | `bootstrap.telegram.bot_token` | yes | migrated | Telegram parity + regression |
| `app/bot/client.py` | `scanner_telegram_bot_token` | `delivery.telegram.scanner_telegram_bot_token` | yes | migrated | fallback parity + regression |
| `app/bot/client.py` | `telegram_channel_id` | `delivery.telegram.telegram_channel_id` | yes | migrated | Telegram parity + regression |
| `app/main.py` | `telegram_owner_id` | `delivery.telegram.telegram_owner_id` | yes | migrated | owner-scope parity + regression |
| `app/core/symbols.py` | `signal_public_channel_id` | `delivery.telegram.signal_public_channel_id` | yes | migrated | broadcast regression |
| `app/signals/trade_ops.py` | `public_show_pips` | `delivery.telegram.public_show_pips` | yes | migrated | broadcast regression |
| `app/persistence/store.py` | `seq_reset_tz` | `delivery.presentation.seq_reset_tz` | yes | migrated | delivery/report parity |
| `app/autotrade/delivery.py` | `delivery_thread_lifecycle` | `delivery.lifecycle.thread_lifecycle` | yes | migrated | delivery regression |
| `app/analysis/market_map_delivery.py` | `map_session_send` | `delivery.market_map.session_send` | yes | migrated | market-map regression |
| `app/analysis/scanner.py` | `scanner_top_n` | `delivery.scanner_cards.top_n` | yes | migrated | AST guard + scanner regression |
| `app/signals/calendar.py` | `calendar_enabled` | `market_data.calendar.enabled` | yes | migrated | calendar parity + regression |
| `app/signals/calendar.py` | `calendar_feed_thisweek` | `market_data.calendar.feed_thisweek` | yes | migrated | calendar parity + regression |
| `app/signals/calendar.py` | `calendar_feed_nextweek` | `market_data.calendar.feed_nextweek` | yes | migrated | calendar parity + regression |
| `app/signals/calendar.py` | `calendar_currencies` | `market_data.calendar.currencies` | yes | migrated | filter regression |
| `app/signals/calendar.py` | `oil_keywords` | `market_data.calendar.oil_keywords` | yes | migrated | filter regression |
| `app/signals/weekly_report.py` | `weekly_report_enabled` | `delivery.reports.weekly.enabled` | yes | migrated | weekly parity + regression |
| `app/signals/weekly_report.py` | `weekly_report_dow` | `delivery.reports.weekly.day_of_week` | yes | migrated | schedule regression |
| `app/signals/weekly_report.py` | `weekly_report_hour` | `delivery.reports.weekly.utc_hour` | yes | migrated | schedule regression |
| `app/signals/weekly_report.py` | `session_ny_start` | `market_data.sessions.ny_start` | yes | migrated | weekly parity + regression |
| `app/signals/watcher.py` | `watcher_ctrader_stale_seconds` | `market_data.watcher.ctrader_stale_seconds` | yes | migrated | freshness regression |
| `app/signals/watcher.py` | `track_interval` | `market_data.watcher.interval_seconds` | yes | migrated | watcher parity + regression |
| `app/signals/price.py` | `tiingo_api_key` | `market_data.tiingo.api_key` | yes | migrated | watcher parity + regression |
| `app/analysis/ohlc_source.py` | `xau_lookback_h1_bars` | `market_data.lookbacks.h1_bars` | yes | migrated | OHLC parity + regression |
| `app/analysis/ohlc_source.py` | `xau_lookback_m15_bars` | `market_data.lookbacks.m15_bars` | yes | migrated | OHLC parity + regression |
| `app/analysis/ohlc_source.py` | `xau_lookback_m5_bars` | `market_data.lookbacks.m5_bars` | yes | migrated | OHLC parity + regression |
| `app/analysis/ohlc_source.py` | `xau_lookback_m1_bars` | `market_data.lookbacks.m1_bars` | yes | migrated | OHLC parity + regression |
| `app/analysis/ohlc_source.py` | `scanner_window` | `market_data.scanner.window` | yes | migrated | unknown-timeframe regression |

## 8. Bootstrap and logging migration

The composition root now passes grouped logging values without changing the
`configure_logging` signature, call order, handlers, formatting, rotation, or
fallback. Direct operational PostgreSQL and Redis URL reads migrate only
because those exact leaves have direct legacy owners. Environment-only
PostgreSQL inputs remain outside the view.

## 9. Delivery and Telegram migration

Bot token selection, scanner-token fallback, channel IDs, owner scope, public
visibility, lifecycle delivery, presentation timezone, scanner-card limits,
and market-map delivery now use grouped paths. Bot construction timing,
messages, routing, retry behavior, replies, and threads are unchanged.

## 10. Calendar migration

Feed selection, User-Agent, currency/oil filtering, timezone, and scheduled
hour now use `market_data.calendar` and `delivery.presentation`. The Thursday
next-week selection, reservation, cache replacement, normalization, delivery,
and backoff logic are unchanged.

## 11. Weekly-report migration

Weekly enable/day/hour/skip-empty, presentation timezone, and session starts
now use grouped paths. The closed-week calculation, idempotency, stats inputs,
VIP-only routing, formatting, and 30-minute loop interval are unchanged.

## 12. Watcher migration

Public pip visibility and owner routing use `delivery.telegram`; Tiingo,
cTrader staleness, and polling interval use `market_data`. Redis-first source
selection and all TP, SL, fill, runner, pip, and alert calculations are
unchanged.

## 13. OHLC and lookback migration

The dynamic legacy attribute-name map was replaced by explicit canonical
lookback resolvers for H1, M15, M5, and M1. The 50-bar floor, case-insensitive
timeframes, explicit unknown-timeframe default priority, scanner-window
fallback, Redis key, price normalization, and DataFrame ordering are unchanged.

## 14. Dual-authority parity results

Process-isolated tests use the same safe environment under `legacy` and
`canonical`. Logging, Telegram selection/routing inputs, calendar, weekly
report, watcher, and all OHLC lookbacks compare equal as both exact values and
exact Python types. Runtime types are also asserted:

- legacy: `settings=Settings`, `runtime_config=LegacyCanonicalConfigView`;
- canonical: `settings=CanonicalSettingsFacade`,
  `runtime_config=PythonRuntimeConfig`.

Operational regression selections cover calendar filtering/scheduling, weekly
scheduling, watcher freshness/source selection, lookback behavior, Telegram
delivery, market-map delivery, and logging.

## 15. Deferred settings reads

The 287 deferred reads are explicit: actionability 47, analysis 15, contract
42, execution 41, lifecycle 41, manual-algo 13, risk 11, runtime 35,
strategies 37, three derived delivery properties, and two optional
compatibility names without typed-catalog leaves. They remain flat legacy reads
until their reviewed phase; none is an unknown blocker.

## 16. Legacy rollback proof

Legacy startup constructs `Settings` and then wraps that same object. It does
not call the canonical resolver or load canonical source bundles. Switching
from canonical back to `APEXVOID_CONFIG_AUTHORITY=legacy` on restart therefore
restores the original authority path without persisted canonical state.

## 17. Phase 2F entry criteria

Phase 2F can begin after this branch is merged and deployed with generated
artifacts current, zero Phase 2E target reads remaining, dual-authority parity
green, the view contract stable, default authority still legacy, and restart
rollback verified. Phase 2F is limited to analysis, strategies, actionability,
and lifecycle. Execution and risk remain a separately reviewed later phase.
