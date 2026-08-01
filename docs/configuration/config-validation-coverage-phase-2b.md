# Phase 2B configuration validation coverage

This document accounts for every clause in the active legacy
`Settings._resolve_auto_trade_profile` validator. The grouped configuration is
inactive; these entries only prove parity or state the Phase 2C source/profile
dependency that prevents honest encoding in a value model.

| Legacy clause | Phase 2B disposition | Typed location | Notes |
|---|---|---|---|
| Profile trim/lower and supported names | Encoded | `RuntimeConfig.profile` | Accepts `conservative` and `demo_eval`; stored value remains `str`. |
| `demo_eval` requires demo account when explicitly overridden | Deferred to Phase 2C | Source/profile resolver | Depends on `model_fields_set`, not only resolved values. |
| Apply `demo_eval` default mutations | Deferred to Phase 2C | Source/profile resolver | Profile mutation is explicitly outside Phase 2B. |
| Conservative live structural-guard default | Deferred to Phase 2C | Source/profile resolver | Depends on explicit-source evidence. |
| Derive market-map guard from mapped-zone switch | Deferred to Phase 2C | Source/profile resolver | Depends on whether the guard was explicitly supplied. |
| Structural guard enum | Encoded | `ActionabilityStructuralGuardConfig` | Normalizes case/whitespace; permits observe/balanced/strict. |
| Structural-reaction lookback >= 1 | Encoded | `ExecutionPolicyConfig` | Pydantic `ge=1`. |
| Retest validity between 1 and 5 | Encoded | `LifecycleRetestConfig` | Pydantic `ge=1`, `le=5`. |
| H1/M15/M5/M1 lookbacks >= 50 | Encoded | `MarketDataLookbacksConfig` | Pydantic `ge=50` on each field. |
| XAU zone-width ordering | Encoded | `AnalysisZonesSymbolContractConfig` | Narrow cross-field model validator. |
| Maximum entry distance positive | Encoded | `ExecutionEntryConfig` | Pydantic `gt=0`. |
| Execution-zone ATR/pip widths positive | Encoded | `ExecutionPolicyConfig` | Pydantic `gt=0`. |
| Range scale-out threshold/trigger/fraction relationship | Encoded | `ExecutionRangeConfig` | Field constraints plus trigger-below-threshold validator. |
| Zone-reconcile normalization, disable-to-off and enum | Encoded | `ActionabilityZoneReconciliationConfig` | Narrow normalization and cross-field validator. |
| Non-hedged opposite-policy normalization and enum | Encoded | `RiskExposureConfig` | Stored type remains `str`. |
| Reaction invalid-policy normalization and enum | Encoded | `ExecutionReactionConfig` | Stored type remains `str`. |
| Reaction fractions positive and sum to 1 | Encoded | `ExecutionReactionConfig` | Field constraints plus sum validator. |
| BE ticks/PIPS deprecated-alias conflict | Deferred to Phase 2C | Source resolver | Requires simultaneous raw ENV source visibility. |
| BE buffer in [0, 1000) | Encoded | `ExecutionStopsConfig` | Pydantic `ge=0`, `lt=1000`. |
| Contract mode is `v7_only` | Encoded | `ContractConfig` | Normalizes case/whitespace and constrains the value. |

No clause is silently omitted. The two deferred categories are precisely the
ones that require source provenance or profile mutation, both explicit Phase
2C responsibilities.
