# Phase 2G: final runtime and execution configuration consumers

## 1. Scope and non-goals

Phase 2G changes only how production Python modules read configuration under
`runtime`, `contract`, `execution`, `risk`, and `manual_algo`. Every eligible
flat `Settings` attribute read is replaced with
`runtime_config.<canonical.path>`. Derived delivery compatibility properties
and the two optional compatibility names are resolved without adding catalog
leaves. Defaults, ENV names, source precedence, profiles, validation, trading
decisions, Redis formats, Compose, deployment templates, C#, and CI are
unchanged.

`APEXVOID_CONFIG_AUTHORITY` still defaults to `legacy`. `canonical` remains an
explicit restart-time choice. Restart rollback to the independent legacy loader
remains available. ENV consolidation remains Phase 2H.

## 2. Verified Phase 2F baseline

The branch started from master commit
`f68b686395bf938536eac14d3cf1ac3e8cd9afb2` (PR #201). Catalog state matched the
mission baseline: 437 canonical leaves, 387 Python runtime projection leaves,
and 316 direct legacy fields. Phase 2E reported 127 migrated operational reads;
Phase 2F reported 140 migrated trading reads.

Pre-migration deferred ownership matched the expected Phase 2G total of 147
production flat reads:

| Deferred ownership | Reads |
|---|---:|
| `runtime` | 35 |
| `contract` | 42 |
| `execution` | 41 |
| `risk` | 11 |
| `manual_algo` | 13 |
| derived delivery compatibility properties | 3 |
| optional compatibility names | 2 |
| **Total** | **147** |

AST inventory differences after migration are call-site accounting only: five
derived delivery replacements were recorded (repeated `signal_vip_channel_id`
call sites plus `xau_public_channel_id`), while the Phase 2F deferred ledger
counted three derived property names. Optional compatibility remained two
names. Eligible root totals remained 142 (35+42+41+11+13).

## 3. Phase 2G inventory

The deterministic line-level ledger is
`contracts/configuration/consumer-migration-phase-2g.generated.json`.

| Root | Eligible before | Migrated | Remaining | Behavioral tests |
|---|---:|---:|---:|---|
| `runtime` | 35 | 35 | 0 | enablement / scanner / profile |
| `contract` | 42 | 42 | 0 | manifest / streams / instrument |
| `execution` | 41 | 41 | 0 | entry / targets / stops / scaling |
| `risk` | 11 | 11 | 0 | sizing / exposure / limits |
| `manual_algo` | 13 | 13 | 0 | commands / streams / owner DM |
| **Eligible total** | **142** | **142** | **0** | **13 characterized boundaries** |

Additional migrated ledger rows:

| Classification | Count |
|---|---:|
| `DERIVED_PROPERTY_REPLACE` | 5 |
| `OPTIONAL_COMPATIBILITY_RESOLVE` | 2 |
| `UNKNOWN_BLOCKER` | 0 |
| **Ledger migrated_reads** | **149** |

Final production counts:

| Metric | Count |
|---|---:|
| production flat Settings reads | 0 |
| production settings imports | 0 |
| production dynamic Settings lookups | 0 |
| unknown blockers | 0 |

## 4. Runtime migration

Auto-trade enablement, dry-run, direct publish, strategy-match enablement,
scanner enablement, and profile selection now read
`runtime_config.runtime`. Consumers include `app/main.py`,
`app/analysis/scanner.py`, `app/autotrade/worker.py`,
`app/autotrade/config_health.py`, `app/autotrade/delivery.py`,
`app/autotrade/setup_expiry_sweeper.py`, `app/autotrade/lifecycle.py`, and
`app/autotrade/zone_execution_cutover.py`. Startup task decisions and status
wording are unchanged.

## 5. Contract migration

Candidate/TradePlan versions, contract mode, candidate/event/trade-plan
streams, symbols, pip size, price digits, contract size, and demo-account
requirements now read `runtime_config.contract`. Config-health still compares
Python and C# manifests with the same fatal/warning rules. Stream names and
Redis serialization formats are unchanged.

## 6. Execution migration

Entry chase/tolerance, targeting ladders and range buffers, stop envelopes,
BE buffer ticks, range scale-out, mapped-zone thesis lock, zone-fill policy,
and related policy knobs now read `runtime_config.execution`. Exact
Decimal/float/int behavior and reason codes are preserved. Ticks are not
reinterpreted as pips.

## 7. Risk migration

Sizing mode, equity table version, concurrent-strategy and hedged-XAU policy,
non-hedged opposite policy, opposing separation, and same-direction stack
fraction now read `runtime_config.risk`. Exposure rejection ordering and
zero-as-unlimited semantics are unchanged.

## 8. Manual-algo migration

Manual enablement, dry-run, owner execution DM, intent/command streams, and
maxlen settings now read `runtime_config.manual_algo`. Manual streams remain
separate from automatic candidate streams. Pause/resume/close-all semantics
and acknowledgement behavior are unchanged.

## 9. Derived compatibility replacement

Production uses of derived delivery properties were replaced with underlying
canonical leaves:

| Legacy property | Canonical path |
|---|---|
| `signal_vip_channel_id` / `telegram_chat_id` | `delivery.telegram.telegram_channel_id` |
| `xau_public_channel_id` | `delivery.telegram.signal_public_channel_id` |

Derived properties may remain on `CanonicalSettingsFacade` for tests and
rollback compatibility. Production no longer reads them.

## 10. Optional compatibility resolution

| Name | Resolution |
|---|---|
| `auto_trade_broker_lot_size` | local `_DEFAULT_BROKER_LOT_SIZE` constant in delivery |
| `auto_trade_v7_max_volume` | local `_V7_MAX_VOLUME_DEFAULT` constant in worker |

Neither name received a new catalog leaf. Both lacked typed catalog ownership
and behaved as non-configuration compatibility fallbacks.

## 11. Production Settings import elimination

Normal production modules no longer import `settings`, `Settings`,
`CanonicalSettingsFacade`, or `LegacyCanonicalConfigView`. Allowed
Settings-related imports remain `app/core/config.py`, `app/configuration/*`,
tests, and migration/audit tools. `app.core.config` continues exporting
`settings` until Phase 2I. Narrow helper boundaries that historically accepted
flat injected fixtures may obtain a facade via `runtime_config_facade()` from
`app.core.config`; they do not import flat `settings`.

AST guards in the Phase 2G audit suite and `phase2h_gate` enforce zero
production flat reads, dynamic lookups, derived property reads, optional
compatibility reads, and banned Settings imports.

## 12. Dual-authority parity

Process-isolated probes load the same safe fixture under `legacy` and
`canonical`. All Phase 2G-migrated access points compare canonical path, value
representation, exact Python type, and truthiness. Legacy resolves to
`LegacyCanonicalConfigView`; canonical resolves to `PythonRuntimeConfig`.
Value mismatches = 0. Type mismatches = 0.

## 13. Behavioral characterization

Thirteen named characterization guards bind the migration to established
tests for runtime enablement, contract manifest, stream routing, TradePlan
construction, entry policy, targeting, stops, scaling, position sizing,
exposure, position limits, manual commands, and config-health.

## 14. Existing unchanged failures

Captured on untouched master and reproduced with identical assertion semantics
after migration. This phase does not fix, skip, xfail, or weaken them:

- `test_trade_plan_builder.py::test_stop_inside_opposing_zone_surfaces_precise_reason_and_evidence`
  (`stop_exceeds_max_envelope` vs `stop_inside_opposing_zone`);
- `test_confluence_card_rr_pregate.py::test_one_card_and_one_setup_per_merged_zone`
  (title/content expectation mismatch under analysis-only wording).

## 15. Phase 2H readiness result

```text
python -m app.configuration.phase2h_gate --check
```

reports `READY_FOR_PHASE_2H` with:

- production flat Settings reads = 0;
- production settings imports = 0;
- production dynamic Settings lookups = 0;
- unknown blockers = 0;
- generated artifacts current.

Phase 2H may begin ENV/Compose consolidation. It must not reopen consumer
access-path migration.

## 16. Rollback confirmation

Legacy startup still constructs `Settings` first and exposes a lazy grouped
view over that same authoritative instance. It does not require canonical
source loading. Canonical remains restart-selectable when required inputs are
present. Removing or setting `APEXVOID_CONFIG_AUTHORITY=legacy` restores the
independent legacy path. The default remains `legacy`.
