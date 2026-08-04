# ADR: Canonical-only Python configuration

Status: Accepted (Phase 2I final closeout)

## Decision

Python configuration has exactly one authority: the canonical catalog resolver
producing a typed `PythonRuntimeConfig` exposed as `runtime_config`.

```
source bundle -> canonical resolver -> PythonRuntimeConfig -> runtime_config
```

There is no Settings singleton, no authority selector, no flat facade, and no
runtime legacy rollback path.

## Context

PR #216 completed the production core cutover but left temporary test/tooling
compatibility and a relaxed completion gate. This ADR records the final
structural closeout.

Authorization was an explicit owner structural decision. No production
observation window was fabricated.

## Consequences

- Invalid configuration fails closed; recover by correcting inputs and restarting
- Deployment rollback means reverting/redeploying the previous known-good image
- Deprecated catalog-backed ENV aliases remain supported
- Historical Phase 2A–2I-A migration artifacts are archived and not regenerated

## Non-goals preserved

Trading behavior, defaults, profiles, C#, and CI workflows are unchanged by this
decision.
