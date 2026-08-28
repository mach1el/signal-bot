# Auto-trade logic gaps — measured evidence (PR-B–H)

Evidence captured during the discovery → activation gap closure program.
PR-A (funnel instrumentation, #440) shipped separately; PR-B–H landed as one
combined PR on `feat/auto-trade-logic-gaps-combined`.

## Strict premium/discount asymmetry (PR-B)

Measured over 600 bar-by-bar evaluations per regime (M5 dealing range,
`fractal_n=2`, `zigzag_atr_mult=1.0`, `eq_band=0.10`):

| Regime   | BUY allowed | SELL allowed | eq blocks both |
|----------|-------------|--------------|----------------|
| downtrend | 92.8%      | 3.8%         | 3.3%           |
| flat      | 42.5%      | 46.8%        | 10.7%          |
| uptrend   | 4.0%       | 95.0%        | 1.0%           |

Applying strict PD to continuation archetypes inverts their edge. Default
`strict_premium_discount_archetypes: reversal,range_reversion` limits the
gate to mean-reversion families.

## Causal structure breaks (PR-G)

On 1200 synthetic M5 bars, in-sample `structure_breaks()` labelled 82 breaks;
causal bar-by-bar replay reproduced 42 (~**49%** lookahead inflation).
Live scanner keeps `causal=False`; replay/research use `causal=True`.

## Fractal tie drop (PR-G)

At 0.01 tick granularity, the old uniqueness requirement in
`_fractal_candidates` dropped **16.5%** of fractal extremes — exactly the
equal-high / equal-low pivots liquidity and fade_scalp depend on.

## Cluster span slop (PR-G)

With tolerance 1.04, greedy single-linkage clustering produced max span 2.65;
3/34 clusters exceeded 2× tolerance (~26 pips slop on XAU key levels).

## Confluence inversion (PR-E)

Examples that motivated band bonus scoring and normalised thresholds:

| Case | Before |
|------|--------|
| Key Level, all 7 factors true | published 2★ (factor model → 3★) |
| 5 techniques, best member 3.0 | 1★ (below floor → rejected) |
| 2 techniques, best member 13.0 | 3★ |

Confluence Zone premise requires technique-count bonus in the band score.
