# XAU technique structure fixed_rr

XAU **technique / reaction** auto now follows the FX structure book:

| Layer | Behavior |
|-------|----------|
| Stop | Structure swing → fit pack envelope **25–100** pips |
| Targets | **1.0R / 2.0R** of final stop; closes **50 / 50** |
| Runner | After TP1, stop moves to **breakeven** (group weighted fill) |
| Fallback | Single **1R** full close when opposing room cannot hold 2R |
| Pack | `instrument_packs.xau_fixed_2r_v1` / policy `xau_fixed_2r_v1` |

**M1 scalping is unchanged.** Discovery still picks 1:2 or 1:1 room; publish
builds `(1R, 2R)` ladders. `technique_fixed_rr_targeting(symbol, strategy)`
returns fixed_rr targeting only for non-scalp strategies so execution policy
does not expand scalp matches into the technique R ladder.

## Code anchors

- [`app/core/instrument_geometry.py`](../../algo-bot/app/core/instrument_geometry.py) — `technique_fixed_rr_targeting`
- [`app/autotrade/execution_policy.py`](../../algo-bot/app/autotrade/execution_policy.py) — technique-only fixed_rr expansion
- [`app/scalping/context.py`](../../algo-bot/app/scalping/context.py) — XAU remains scalp-eligible with fixed_rr
- Config: `config/trading-bot.yml` → `instrument_packs.xau_fixed_2r_v1`, `instruments.XAU.pack`

## Calibration

Envelope max (100) is a pack leaf. Raise/lower from live Key Level
`stop_exceeds_envelope_*` rates without a code fork.
