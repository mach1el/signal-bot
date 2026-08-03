# Canonical authority observation runbook

This runbook defines the operator-run observation window that must complete
**before** Phase 2I-B (legacy deletion) can be reviewed. Static analysis
(`phase2i_removal_gate --check-static`) can only certify
`READY_FOR_CANONICAL_OBSERVATION`; it can never certify legacy deletion. The
evidence produced here is validated for **structure and completeness only** by
`phase2i_removal_gate --check-observation <path>` — the gate never inspects live
systems and never fabricates results. All conclusions are the operator's.

## Preconditions

- Managed deployment already defaults to `APEXVOID_CONFIG_AUTHORITY=canonical`.
- The deployment image includes the final structural configuration release that
  contains **Phase 2I-A.1** (typed canonical domain injection; no production
  `project_runtime_config` bridge). See
  `config-refactor-phase-2i-a1-canonical-domain-injection.md`.
- Canonical startup verified once: startup record shows
  `configuration_authority=canonical`, expected profile, catalog fingerprint,
  and configuration health publishes.
- Real secrets stay in the deployment vault; never record them in evidence.

## Observation window requirements

Run the bot under **canonical** authority and record real, first-hand
observations. Do not fabricate, back-fill, or estimate any result.

1. **Duration** — at least **5 trading days** of continuous canonical operation.
2. **Sessions** — cover the Asia, London, and New York sessions across the
   window.
3. **Restarts** — at least one clean restart under canonical authority, with the
   startup record confirming canonical selection.
4. **Config health** — capture the configuration-health output on at least one
   day; confirm it reports healthy.
5. **Incidents** — record any incident (or an explicit empty list if none),
   including any manual rollback to legacy and the reason.
6. **Sign-off** — the operator records who approved the window and the date.

## Evidence file structure

Provide a JSON file with this shape to `--check-observation`:

```json
{
  "phase": "2I-A",
  "authority": "canonical",
  "observation_window": {
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "trading_days": ["YYYY-MM-DD", "... at least 5 entries ..."]
  },
  "sessions_observed": ["asia", "london", "new_york"],
  "restarts": [
    {"timestamp": "YYYY-MM-DDThh:mm:ssZ", "authority": "canonical", "outcome": "clean"}
  ],
  "config_health_checks": [
    {"timestamp": "YYYY-MM-DDThh:mm:ssZ", "status": "pass"}
  ],
  "incidents": [],
  "sign_off": {"approved_by": "<operator>", "date": "YYYY-MM-DD"}
}
```

The gate requires: `phase == "2I-A"`, `authority == "canonical"`, a window with
non-empty `start_date`/`end_date` and at least five `trading_days`, a non-empty
`sessions_observed`, at least one fully-specified restart, at least one
config-health check, an `incidents` list (may be empty), and a complete
`sign_off`. A structurally valid, complete file yields
`READY_FOR_PHASE_2I_B_REVIEW` — a human review gate, still not an automatic
deletion.

## Local validation

```bash
cd algo-bot
python -m app.configuration.phase2i_removal_gate \
  --check-observation ../path/to/observation-evidence.json
```

Expected on a complete file: `status = READY_FOR_PHASE_2I_B_REVIEW` and an empty
`problems` list. Any missing or malformed field is reported in `problems` and
the status stays `NOT_READY`.
