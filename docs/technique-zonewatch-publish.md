# Technique publishers and ZoneWatch publish path

How the five atomic techniques and Confluence Zone move from detection to a
TradePlan V8, and the gates that used to silence them in production.

## Product model

| Setup name | Tag | Role |
|---|---|---|
| Supply Demand | `supply_demand` | T1 band from supply/demand geometry |
| Order Block | `order_block` | T2 OB band |
| FVG | `fvg` | T3 fair-value gap (entry clipped to proximal width) |
| iFVG | `ifvg` | T4 inversion FVG |
| CRT | `crt` | T5 candle-range / reclaim technique |
| Confluence Zone | *(composite)* | **2+ distinct** techniques overlapping on price |

- Single technique → publish under that technique name.
- Overlap of 2+ distinct techniques → publish **only** as Confluence Zone
  (tagged with the contributing technique tags).
- Legacy **Zone Reaction** remains behind
  `AUTO_TRADE_ZONE_REACTION_FALLBACK_ENABLED` (default **false**).
- Do **not** overload `execution.technique` (killzone / sweep / PD pack) with
  these publisher names — that block is a separate gate pack.

Taxonomy: techniques + confluence are **zone family**
(`is_zone_strategy`), not `is_reaction_strategy`. They are also listed in
`STRUCTURAL_SETUPS` for ranking / actionability / multi-match.

Enable flags (schema defaults **true**):

- `AUTO_TRADE_TECHNIQUE_SD_ENABLED`
- `AUTO_TRADE_TECHNIQUE_OB_ENABLED`
- `AUTO_TRADE_TECHNIQUE_FVG_ENABLED`
- `AUTO_TRADE_TECHNIQUE_IFVG_ENABLED`
- `AUTO_TRADE_TECHNIQUE_CRT_ENABLED`
- `AUTO_TRADE_CONFLUENCE_ZONE_ENABLED`

Geometry lives in `algo-bot/app/analysis/technique_geometry.py`; publishers in
`technique_detectors.py`.

## End-to-end path

```text
detector hit
    → ZoneWatch discover (watching_retest) + candidate StrategyMatch
    → record_zone_presence (inside / chase / approach)
    → entry_location + entry_activation
    → _activate_match → try_publish_executable_signal
    → TradePlan V8 → ctrader-engine
```

Cutover module: `algo-bot/app/autotrade/zone_execution_cutover.py`.
Spot re-eval: `zone_watch_execution_loop` (~2s cap).

## Activation chase (techniques / confluence)

Range scalp families already chase past the far edge within a scalp budget.
Techniques previously fell through to `maximum_chase_pips=0` →
`quote_outside_zone` the moment price left the band.

**Current rule:** if strategy is technique or confluence, `_scalp_access`
uses `scalp_zone_access` with chase budget =
`execution.entry.maximum_chase_distance_pips` (prod default **40**).

Legacy Zone Reaction / Demand Zone stay **strict inside** (chase = 0).

## Decisive break = closed bar only

`record_zone_presence(..., decisive_break=True)` invalidates the watch.
Using the **live quote** as the break price caused spot wicks to kill
ZoneWatches permanently (`INVALIDATED` → discovery never resurrects →
`zone_watch_locked_or_terminal`).

**Current rule:** decisive break only when the latest **closed** bar on
`record.source_timeframe` (fallback M5) closes beyond the far edge:

- BUY (demand): close `<` zone `low`
- SELL (supply): close `>` zone `high`

Live quote may mark `inside=False` / approach, but must not set
`decisive_break=True` by itself.

## Activate remain / reject logging

Every `_activate_match` early exit logs at INFO with `zone_id`, `strategy`,
access `mode`, `distance_pips`, `max_chase`, and publish `status` /
`reason_code` where applicable.

On `direct_publish_failed_durable_fallback`, techniques and confluence
**stay watching** (same as reaction strategies) instead of retiring the
setup and thrashing.

## Opposing-room (V8)

Non-scalp publish measures room to opposing Market Map structure.

1. **Shared-boundary filter** — drop opposing entries glued to the
   candidate proximal wall / planned entry (`raw_room≈0` false positives).
2. **Overlap filter** — drop opposing entries that **substantially overlap**
   the candidate band (stacked map vs technique geometry). Clear barriers
   ahead of the band still hard-block
   (`entry_inside_opposing_zone`, `opposing_barrier_room_below_cost`, …).

Implementation: `structural_target_room.py`
(`filter_shared_boundary_opposing_entries`,
`filter_overlapping_opposing_entries`).

## Entry location extremes

Techniques map to the reversal location archetype via zone family. Extreme
blocks (`buy_at_range_extreme` / `sell_at_range_extreme`) often fire because
technique entries sit at dealing-range edges by design.

**Current rule:** for technique / confluence only, allow the extreme band;
keep mid-range `buy_in_premium` / `sell_in_discount` gates.

## Operator checks after deploy

```bash
# Compose health
docker compose ps
docker compose logs --tail=100 bot | rg 'zone activate|zone watch|technique|Confluence|plan_published'

# Redis (example)
redis-cli KEYS 'analysis:zone_watch:*'
redis-cli GET 'auto_trade:last_entry_activation:XAU'
```

Expect:

- Technique watches with non-zero `max_chase` in cutover / activation logs
- Spot wicks through the far edge **without** immediate `INVALIDATED`
- Funnel / metrics showing `plan_published` under FVG / CRT / … / Confluence
- Zone Reaction still strict-inside (no chase)

## Related code

| Area | Path |
|---|---|
| Technique math | `algo-bot/app/analysis/technique_geometry.py` |
| Publishers | `algo-bot/app/analysis/technique_detectors.py` |
| Taxonomy | `algo-bot/app/autotrade/strategy_taxonomy.py` |
| Structural set | `algo-bot/app/analysis/structural_reaction_support.py` |
| Cutover | `algo-bot/app/autotrade/zone_execution_cutover.py` |
| Location | `algo-bot/app/analysis/entry_location.py` |
| Target room | `algo-bot/app/autotrade/structural_target_room.py` |
| ZoneWatch state | `algo-bot/app/autotrade/zone_watch.py` |
