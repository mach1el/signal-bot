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
