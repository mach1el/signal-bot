# Phase 2F: authority-neutral trading configuration consumers

## 1. Scope and non-goals

Phase 2F changes only how Python trading consumers read configuration. All
direct, legacy-backed reads rooted at `analysis`, `strategies`,
`actionability`, and `lifecycle` now use the grouped `runtime_config` API.
Defaults, ENV names, source precedence, profiles, validation, decisions,
ranking, reason ordering, lifecycle transitions, execution plans, risk,
Compose, deployment templates, C#, and CI are unchanged.

`APEXVOID_CONFIG_AUTHORITY` still defaults to `legacy`. `canonical` remains an
explicit restart-time choice, and restart rollback to the independent legacy
loader remains available.

## 2. Verified Phase 2E baseline

The branch started from master commit
`2e8c204abc7bceb3b1774659adb409ed78a51938`. The catalog contained 437
canonical leaves, with 387 Python projection leaves, 50 cTrader-only leaves,
316 direct legacy fields, 316 reverse legacy-backed paths, and 427 canonical
traversal prefixes. Phase 2E reported 127/127 operational reads migrated, zero
remaining target reads, and zero unknown blockers.

The pre-migration AST inventory matched the expected Phase 2F total exactly:
15 analysis reads, 37 strategy reads, 47 actionability reads, and 41 lifecycle
reads.

## 3. Phase 2F migration inventory

The deterministic line-level ledger is
`contracts/configuration/consumer-migration-phase-2f.generated.json`.

| Root | Eligible before | Migrated | Remaining | Behavioral boundaries tested |
|---|---:|---:|---:|---|
| `analysis` | 15 | 15 | 0 | detection, zone construction, market map |
| `strategies` | 37 | 37 | 0 | eligibility, match ordering, multi-match selection |
| `actionability` | 47 | 47 | 0 | status/reason order, target room, barriers, counter-bias |
| `lifecycle` | 41 | 41 | 0 | expiry, cooldown, retirement, rearm, reconciliation |
| **Total** | **140** | **140** | **0** | **15 characterized boundaries** |

There are 147 explicitly deferred reads and zero unknown blockers. Every
migrated canonical path exists in the typed catalog and in
`CANONICAL_PATH_TO_LEGACY_ATTR`.

The complete manifest is authoritative. Representative file-level mappings
are summarized below.

| File | Legacy attribute | Canonical path | Migration status | Behavior test |
|---|---|---|---|---|
| `app/analysis/confluence_zone.py` | `xau_zone_min_width_price` | `analysis.zones.symbol_contract.minimum_width_price` | migrated | zone-width contract |
| `app/analysis/market_map_delivery.py` | `map_scan_interval_minutes` | `analysis.market_map.scan_interval_minutes` | migrated | market-map delivery |
| `app/analysis/scanner.py` | `zone_merge_max_width` | `analysis.zones.merge_max_width` | migrated | zone construction |
| `app/autotrade/worker.py` | `atr_length` | `analysis.atr.length` | migrated | detector regression |
| `app/analysis/scanner.py` | `auto_trade_multi_match_enabled` | `strategies.matching.multiple_matches_enabled` | migrated | strategy match |
| `app/autotrade/config_health.py` | `auto_trade_range_flip_enabled` | `strategies.range_reversion.flip_enabled` | migrated | strategy selection |
| `app/autotrade/worker.py` | reaction enable names | `strategies.reaction.<kind>.enabled` | migrated | strategy eligibility |
| `app/analysis/scanner.py` | `auto_trade_structural_guard_mode` | `actionability.structural_guard.guard_mode` | migrated | scanner actionability |
| `app/autotrade/config_health.py` | `auto_trade_market_map_guard_enabled` | `actionability.gates.market_map_guard_enabled` | migrated | actionability contract |
| `app/autotrade/worker.py` | `range_context_disagreement_gate_enabled` | `actionability.gates.range_context_disagreement_gate_enabled` | migrated | target-room/guard replay |
| `app/autotrade/zone_execution_cutover.py` | `scanner_zone_width_gate_enabled` | `actionability.scanner_gates.zone_width_gate_enabled` | migrated | cutover actionability |
| `app/analysis/scanner.py` | `auto_trade_strategy_match_max_age_seconds` | `lifecycle.strategy_match.maximum_age_seconds` | migrated | expiry/handoff |
| `app/autotrade/candidate_publish.py` | `auto_trade_candidate_ttl` | `lifecycle.candidate.storage_ttl_seconds` | migrated | candidate expiry |
| `app/autotrade/lifecycle.py` | `auto_trade_candidate_ttl` | `lifecycle.candidate.storage_ttl_seconds` | migrated | lifecycle persistence |
| `app/autotrade/setup_card.py` | `auto_trade_candidate_ttl` | `lifecycle.candidate.storage_ttl_seconds` | migrated | setup-card lifecycle |
| `app/autotrade/trade_plan_stream.py` | `auto_trade_candidate_ttl` | `lifecycle.candidate.storage_ttl_seconds` | migrated | plan dedup TTL |
| `app/autotrade/worker.py` | `auto_trade_box_retire_seconds` | `lifecycle.range_box.retirement_seconds` | migrated | range retirement |
| `app/autotrade/zone_execution_cutover.py` | `auto_trade_strategy_match_max_age_seconds` | `lifecycle.strategy_match.maximum_age_seconds` | migrated | expiry/cutover |

## 4. Analysis migration

Analysis reads in confluence-zone construction, scanner geometry, market-map
scheduling/change detection, ATR, and worker structure extraction now use
`runtime_config.analysis`. Numeric conversions, width boundaries, formulas,
source ordering, map ordering, fallbacks, and scheduling are unchanged.

## 5. Strategies migration

Strategy enablement, matching, range/flip, breakout/retest, reaction-family,
mapped-zone, trend, momentum, and scalp reads now use
`runtime_config.strategies`. Selection order, setup names, primary/multi-match
handling, eligibility, and reason strings are unchanged.

## 6. Actionability migration

Scanner and worker reads for structural guards, target room, opposing
barriers, counter-bias, overlap, edge/EQ exclusion, news, and scanner gates now
use `runtime_config.actionability`. No terminal result was converted to
telemetry or vice versa, and reason-code evaluation order is unchanged.

## 7. Lifecycle migration

Candidate storage TTL, execution maximum age, strategy-match age, zone
cooldown, range-box retirement, mapped-zone rearm, and retest-trigger validity
now use `runtime_config.lifecycle`. Redis units, timestamp sources, boundary
comparisons, zero semantics, restart handling, and state transitions are
unchanged.

## 8. Dynamic lookup removal

Finite reaction enable lookups and mapped/retest lifecycle lookups no longer
construct or introspect legacy attribute names. They use explicit canonical
leaves for each known reaction kind, rearm ATR/bars, and retest validity.
AST guards reject future Phase 2F `getattr(settings, ...)`, dynamic-name, or
flat attribute reads.

## 9. Compatibility wrapper decisions

No new compatibility adapter or broad configuration protocol was needed.
Modules that still consume deferred runtime, execution, risk, or contract
fields retain the `settings` import beside `runtime_config`. Existing tests
that monkeypatch the authoritative settings singleton continue to work through
the legacy-backed grouped view. Public function signatures and injected test
fixtures were not changed.

## 10. Dual-authority parity

Process-isolated tests load the same safe environment under `legacy` and
`canonical`. All 140 production access points compare canonical path, legacy
owner, value representation, exact Python type, and truthiness. Repeated
call-sites remain repeated comparisons. Legacy resolves to
`LegacyCanonicalConfigView`; canonical resolves to `PythonRuntimeConfig`.

Compact in-memory decision snapshots also compare equal for:

- analysis zone-width eligibility and boundaries;
- enabled strategy-family outcomes;
- actionability EQ and edge reasons;
- lifecycle TTL plus just-before/exact/after expiry decisions.

No secret is serialized and production never reads a migration JSON artifact.

## 11. Decision characterization

Fifteen named characterization guards bind the migration to established real
tests for zone construction, market map, detector order, strategy eligibility
and ordering, actionability status/reasons, target room, opposing barriers,
counter-bias, candidate expiry, cooldown, range retirement, mapped-zone rearm,
and reaction rearm.

The analysis selection passed 119 tests. Phase 2F audit, parity, and
characterization tests passed 34 tests. Strategy, actionability, and lifecycle
selections reproduced only the master failures recorded below; no new
Phase 2F-related failure appeared.

## 12. Existing unchanged failures

The following failures were captured on the untouched master worktree and
reproduced with the same assertion semantics after migration. This phase does
not fix, skip, xfail, or weaken them:

- `test_insufficient_target_room_is_rejected_with_a_reason_not_silently`:
  expected no match but received a `StrategyMatch`;
- `test_entry_inside_opposing_structure_is_preference_telemetry` for both BUY
  and SELL parameters: expected `allow_with_warning`, received `block`;
- `test_counter_bias_barrier_with_no_minimum_room_is_terminal`: expected a
  hard block, received preference telemetry;
- `test_news_wait_preserves_active_match`: expected no publication, received a
  candidate id.

The real-Redis plan-dedup integration case is excluded from unit selections
unless `REAL_REDIS_URL` is supplied; its missing external fixture is not a
product failure.

## 13. Deferred roots

The remaining 147 reads are explicit and unchanged:

| Deferred ownership | Reads |
|---|---:|
| `contract` | 42 |
| `execution` | 41 |
| `manual_algo` | 13 |
| `risk` | 11 |
| `runtime` | 35 |
| derived delivery compatibility properties | 3 |
| optional compatibility names | 2 |
| **Total** | **147** |

Execution, risk, contract, manual-algo, and broad runtime migration remain out
of scope.

## 14. Rollback confirmation

Legacy startup still constructs `Settings` first and exposes a lazy grouped
view over that same authoritative instance. It does not require canonical
source loading. Canonical remains restart-selectable, while removing or
setting `APEXVOID_CONFIG_AUTHORITY=legacy` restores the independent legacy
path. The default remains `legacy`.

## 15. Phase 2G entry criteria

Phase 2G may migrate `runtime`, `contract`, `execution`, `risk`, and
`manual_algo` only after Phase 2F is merged with 140/140 reads migrated, zero
unknown blockers, generated artifacts current, all authority parity and
decision snapshots passing, unchanged analysis/strategy/actionability/
lifecycle behavior, legacy still default, and restart rollback verified. ENV
consolidation and legacy duplication removal remain a later Phase 2H concern.
