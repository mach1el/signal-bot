# PR-X cleanup findings

Audited against `master` at `f46b5f5` (after PR-T and the uniform R:R PR
merged), not the prompt's older base `0d09684`. This change is
behavior-preserving: it removes only unread constants whose values equal their
active default and code with no compatibility callers.

## Method

For every candidate configuration leaf, the audit checked:

1. dotted instrument overrides in `config/trading-bot.yml` and
   `ansible-library/inventory/group_vars/all/`;
2. canonical environment-variable use in compose/deployment templates;
3. the Python-to-cTrader option classification and C# source;
4. direct and quoted `getattr` readers; and
5. dynamic detector registration where applicable.

`Y` below means the check found a live path. `-` means it is not applicable;
the reader column is the deciding evidence for retained Python-only fields.

## Configuration leaves from the original scan

| Leaf | Override | Canonical env / C# | Reader | Dynamic | Recommendation |
| --- | --- | --- | --- | --- | --- |
| `analysis.fibonacci.confluence_weight` | - | - | Y: detector settings and fib score | - | Keep |
| `actionability.entry_location.momentum.momentum_sell_maximum_position` | - | - | Y: `entry_location` | - | Keep |
| `bootstrap.logging.ctrader_file_name` | - | Y: compose and `DailyFileLog` | - | - | Keep |
| `strategies.reaction.key_level.require_htf_alignment` | Y: GBPJPY Ansible override | - | N (declared deprecated no-op) | - | Investigate; do not delete a deployed gate-shaped setting |
| `analysis.fibonacci.deep_discount` | - | - | Y: detector settings | - | Keep |
| `analysis.fibonacci.deep_premium` | - | - | Y: detector settings | - | Keep |
| `analysis.measurements.scanner_conflict_overlap` | N | N | N | N | Keep: historical settings-contract test |
| `delivery.telegram.single_root_card` | N | N | N | N | Keep: fixture override compatibility |
| `delivery.telegram.delete_root_on_terminal` | N | N | N | N | Keep: fixture override compatibility |
| `analysis.momentum.va_gate_enabled` | - | - | Y: detector velocity gate | - | Keep |
| `analysis.fibonacci.epsilon_atr` | - | - | Y: fib touch detector | - | Keep |
| `analysis.momentum.velocity_bear_threshold` | - | - | Y: detector velocity gate | - | Keep |
| `execution.activation.m5_confirmation_maximum_age_bars` | - | - | Y: entry activation | - | Keep |
| `analysis.momentum.velocity_bull_threshold` | - | - | Y: detector velocity gate | - | Keep |
| `actionability.entry_location.momentum.momentum_buy_minimum_position` | - | - | Y: `entry_location` | - | Keep |
| `analysis.momentum.velocity_lookback` | - | - | Y: detector settings | - | Keep |

The two Telegram leaves have no production reader: `should_delete_root_on_terminal`
is deliberately hard-coded to `False`, and `single_root_card` has no consumer.
They nevertheless remain part of the historical settings/fixture compatibility
surface; removing either breaks existing tests, so this PR leaves them intact.
`scanner_conflict_overlap` is likewise a deprecated no-op retained by the
historical configuration parity suite. All three need an explicit compatibility
retirement PR that updates the fixture contract, rather than a cleanup delete.

## Constant layers

### Trendlines

No change. PR-T has merged and owns `analysis.trendlines` and the `TL_*`
constants, as required by the prompt.

### Range scalp

The nested `strategies.range_reversion.range_edge` model is live through
`DetectorSettings`; the legacy `AnalysisSettings` compatibility DTO is also
live and must not be collapsed in this PR. `RANGE_SCALP_CLUSTER_PIP_MULT`
remains a live fallback because there is no nested field for it.

Deleted unread constants that matched the nested defaults:
`RANGE_SCALP_CLUSTER_MIN_ABS`, `RANGE_SCALP_MAX_WIDTH_ATR`,
`RANGE_SCALP_MAX_EDGE_WIDTH_ATR`, `RANGE_SCALP_BREAK_CLOSES`,
`RANGE_SCALP_MIN_INSIDE_CLOSES`, and
`RANGE_SCALP_FALLBACK_MIN_CONFIRMATIONS`.

The following unread constants remain intentionally because their values differ
from the live nested defaults; changing or deleting them belongs to the
configuration architecture work:

| Constant | Dead value | Live default |
| --- | ---: | ---: |
| `RANGE_SCALP_LOOKBACK` | 36 | 48 |
| `RANGE_SCALP_CLUSTER_ATR` | 0.20 | 0.25 |
| `RANGE_SCALP_MIN_TOUCHES` | 3 | 2 |
| `RANGE_SCALP_MIN_WICK_FRAC` | 0.35 | 0.25 |
| `RANGE_SCALP_ENTRY_TOL_ATR` | 0.15 | 0.25 |
| `RANGE_SCALP_MIN_WIDTH_ATR` | 1.2 | 1.0 |
| `RANGE_SCALP_MIN_ROOM_ATR` | 1.0 | 0.75 |

### Breakout and displacement

`accepted_box_break` reads `analysis.breakout`; the matching unread
`BREAKOUT_BUFFER_ATR`, `BREAKOUT_ACCEPT_BARS`, and `BREAKOUT_MAX_AGE_BARS`
constants were deleted. `DISPLACEMENT_BODY_FRAC` and
`DISPLACEMENT_RANGE_ATR` remain the runtime source for `displacement_grade`;
their catalog counterparts are not removed here because that is a separate
algorithm-constant ownership question.

### Market map

`analysis.market_map` is live for map construction. The matching unread module
constants were deleted. `MAP_MAX_TOUCHES = 2` remains as a reported divergence
from the live config default `4`; it is not removed in this PR. `SESSION_BAND_ATR`,
`MAP_TAG_LIMIT`, and `RAIL_TAG_LIMIT` are runtime constants and remain live.

`analysis.market_map.change_min` and `delivery.market_map.tag_limit` have no
discoverable reader in this audit. They remain untouched because neither was in
the supplied candidate list and both need a dedicated delivery-contract review.

## Other findings left untouched

- `_publish_strategy_match(..., consume_redis_match=False)` is a no-op
  parameter, but multiple regression callers pass `False`. Per the prompt,
  it remains until its intended contract is recovered or callers are migrated.
- `require_htf_alignment` is a configured GBPJPY override but its model labels
  it a deprecated no-op. It needs a follow-up decision, not a cleanup delete.
- `deprecated_option_warnings` was an unused import and is removed.
- The dead BUY-side `if False` impulse assignment is removed; no mirrored SELL
  assignment exists.

## Regression coverage

`test_checked_in_example_snapshots_all_effective_instruments` compares the
resolved manifest for every live instrument in `config/trading-bot.yml` against
the checked-in generated snapshot. The Ansible inventory was independently
checked for the retained compatibility leaves and has no assignment; this diff
does not alter any config leaf, so applying its instrument overrides cannot
change due to this cleanup.

Vulture now runs as a non-blocking CI report at confidence 90 with explicit
whitelist entries for dynamic runtime entry points.
