# Python configuration authority runbook

## Preconditions

- Apply the intended environment through the normal deployment inventory.
- Keep real secrets in the deployment vault or environment, never in this
  runbook or Git.
- Confirm the Python-required values are present: Telegram bot token, VIP
  channel id, and PostgreSQL password, plus any optional settings used by the
  deployment.

## Activate canonical authority

1. Set `APEXVOID_CONFIG_AUTHORITY=canonical` in the bot deployment
   environment.
2. Restart the bot process/container. Do not attempt an in-process switch.
3. Verify one startup record contains:
   `configuration_authority=canonical`, the expected profile, a SHA-256
   catalog fingerprint, `configuration_facade_fields=316`, and
   `configuration_derived_fields=4`.
4. Verify basic bot health: process remains running, Telegram polling starts,
   Redis/PostgreSQL health is normal, and configuration health publishes.

Canonical selection fails closed. It never starts a legacy fallback.

## Legacy settings quarantine (Phase 2H)

The legacy flat `Settings` model now lives in
`app.configuration.legacy_settings` (also exported as `LegacySettings`) and is
re-exported from `app.core.config` for compatibility. `app.core.config` is a
pure composition root: it selects the authority, builds `runtime_config` /
`settings`, and exposes secret-safe diagnostics
(`active_configuration_authority`, `active_configuration_profile`,
`active_configuration_catalog_fingerprint`, `active_configuration_warnings`,
`active_configuration_resolution_trace`). The legacy authority is fully
selectable for rollback; its runtime type is still `Settings`, so its startup
diagnostics are unchanged.

## Authority selection semantics (Phase 2I-A)

Managed deployments now default to **canonical**: `docker-compose.yml`, the
production `docker-compose.yml.j2` template, and `.env.example` all set
`APEXVOID_CONFIG_AUTHORITY=canonical`. The *parser* default is unchanged — an
entirely **missing** `APEXVOID_CONFIG_AUTHORITY` still resolves to legacy so no
existing rollback path breaks — but that implicit selection is now nudged and
logged.

The composition root exposes three additional secret-safe diagnostics:

- `active_configuration_authority_explicit()` — whether the operator set the
  env var explicitly (vs. an implicit default).
- `active_configuration_implicit_authority_warning()` — returns, only when the
  env var is **absent**, the one-line warning
  `configuration_authority_implicit=true selected_authority=legacy recommended_authority=canonical`.
- `active_configuration_deprecation_message()` — returns, only when the env var
  is **explicitly** `legacy`, the one-line diagnostic
  `configuration_authority=legacy configuration_authority_deprecated=true rollback_mode=true planned_removal_phase=2I-B`.

`app.main` logs both at startup. An explicit `legacy` emits the deprecation
diagnostic (not the implicit warning); an absent variable emits the implicit
warning (not the deprecation). The legacy startup message content
(`configuration_authority=legacy`) is unchanged.

## Roll back to legacy

1. Set `APEXVOID_CONFIG_AUTHORITY=legacy`.
2. Restart the bot process/container.
3. Verify one startup record contains `configuration_authority=legacy`.
4. Verify the same basic bot health checks.

Rollback is complete only after restart. No authority or canonical state is
persisted to a file, Redis, PostgreSQL, or module cache across processes.

## Expected startup failure conditions

- invalid or blank `APEXVOID_CONFIG_AUTHORITY`;
- missing required Python secret/input;
- invalid ENV value or conflicting canonical/deprecated aliases;
- invalid or unsupported profile;
- canonical cross-field validation failure;
- projection/catalog mismatch;
- any unexpected loader startup exception.

The safe failure message includes an error category, canonical path where
available, catalog fingerprint, and the recommended rollback action. It must
not include effective values or credentials.

## Local operator checks

From `algo-bot`, using only safe fixture credentials:

```bash
APEXVOID_CONFIG_AUTHORITY=legacy python -c \
  "from app.core.config import settings; print(type(settings).__name__)"

APEXVOID_CONFIG_AUTHORITY=canonical python -c \
  "from app.core.config import settings; print(type(settings).__name__)"
```

Expected types are `Settings` and `CanonicalSettingsFacade`, respectively.
Canonical checks also require safe fixture values for all required Python
inputs. Never paste production credentials into shell history.
