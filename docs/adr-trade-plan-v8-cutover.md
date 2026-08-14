# ADR addendum: TradePlan V8 cutover

## Status

Accepted. Supersedes **new publishes** of TradePlan V7. Historical V7
docs (`adr-trade-plan-v7-boundary.md`, `trade-plan-v7-migration.md`) remain
for architecture rationale and migration history.

## Decision

Bump the live autonomous TradePlan contract from V7 to V8 across
`algo-bot` and `ctrader-engine`. This is a version/identity cutover, not a
redesign of the Python-owns-plan / C#-executes boundary.

## Identity map

| Field | V7 | V8 |
| --- | --- | --- |
| `TRADE_PLAN_VERSION` / `TradePlanContract.Version` | `7` | `8` |
| Plan id | `v7:{match_id}` | `v8:{match_id}` |
| Broker ownership comment | `v7\|…` | `v8\|…` |
| Exposure source | `v7_plan` | `v8_plan` |
| `AUTO_TRADE_CONTRACT_MODE` | `v7_only` | `v8_only` |
| Metrics / reason prefixes | `v7_*` | `v8_*` |

## Compatibility (deploy drain window)

- **Python publishes only** `version: 8` / `v8:` plan ids.
- **C# accepts both** `version` `{7,8}` and plan-id / ownership prefixes
  `v7` and `v8` so in-flight V7 plans can manage to TP/SL.
- Python readers (Telegram, exposure, setup cards) dual-read `v7:` / `v8:`
  until the live book is flat of `v7:` plans.
- Operator logs, Telegram event types, and notify-dedup keys use the `v8`
  prefix. Drain still dual-claims `auto_trade:v7_notify:*` so a mixed
  deploy does not double-fire lifecycle events.
- After drain: drop V7 accept paths and dual-read aliases.

## Unchanged

- Entry / stop / target ownership (Python declares, C# executes).
- V8 opposing-room geometry on the non-scalp publish path:
  shared-boundary filter, **overlap filter** for stacked map vs candidate
  bands, and zone-proximal room reference.
- Scalp opposing bypass via `match_bypasses_opposing_structure`.
- V6 retained only for legacy open-position manage and manual `/algo`.
