# Configuration governance

## Active release checks

```bash
cd algo-bot
python -m app.configuration.catalog_validation --check
python -m app.configuration.generate --check
python -m app.configuration.diagnostic_cli --check
python -m app.configuration.configuration_integrity_gate --check
```

Successful integrity status: `CONFIGURATION_INTEGRITY_OK`.

## Adding a field

1. Add it to the correct grouped model.
2. Declare canonical metadata with `config_field(canonical_env=..., ...)`.
3. Assign owner, unit, risk classification, and reload policy.
4. Assign canonical ENV only when configurable.
5. Add deprecated aliases only when required.
6. Add profile assignment only when profile behavior differs from schema.
7. Regenerate artifacts (`python -m app.configuration.generate --write`).
8. Run catalog validation and the integrity gate.
9. Add targeted behavior tests.

## Guards

- Direct `os.getenv` outside source-policy adapters is forbidden in production.
- Protocol constants cannot bind ENV variables.
- Canonical paths and ENV names must be unique.
- Active descriptions must not use migration terminology.

## Deprecating an option

Set `deprecated=True` with `replacement_path` or
`terminal_deprecation_reason`. Keep deprecated ENV aliases registered until
removed by an explicit ENV contract change.
