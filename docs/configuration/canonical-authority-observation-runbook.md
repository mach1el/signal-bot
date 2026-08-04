# Canonical authority observation runbook (historical)

> **Superseded by Phase 2I-B / final structural completion.**
>
> This document is retained for history only. Phase 2I was completed as an
> explicit structural architecture decision. **No production observation
> evidence was fabricated**, and this runbook must not be used to invent a
> five-day observation record after the fact.

The production configuration path is now canonical-only. See:

- `adr-canonical-only-python-configuration.md`
- `config-refactor-phase-2i-b-canonical-only.md`
- `config-authority-runbook.md` (startup and recovery)

Recovery is image revert / input correction + restart. There is no
`APEXVOID_CONFIG_AUTHORITY=legacy` rollback selector.
