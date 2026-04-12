# Nasdaq Scoring Simulation — Cross-Method Summary

Generated from 27 simulation runs across 8 scoring methods, N=1..15 sample weeks each.

All returns are **normalized to per-week** using the Nth root of the compound return:
`weekly% = ((1 + compound%/100)^(1/N) - 1) * 100`, then averaged across all runs
of the same scoring method (N=9, N=11, N=15). This makes results comparable despite
different sample sizes.

## Overall Ranking (top 20 combinations)

| Rank | Scoring | Group | Max Pos | Norm Weekly % |
| --- | --- | --- | --- | --- |
| 1 | momentum | 1 | 3 | 13.11 |
| 2 | momentum | 1 | 4 | 12.17 |
| 3 | momentum | 1 | 5 | 11.02 |
| 4 | atr_roc5 | 1 | 3 | 10.48 |
| 5 | roc_acceleration | 1 | 3 | 10.37 |
| 6 | shock_vol_roc | 1 | 3 | 10.20 |
| 7 | roc_acceleration | 1 | 4 | 10.17 |
| 8 | atr_vwap_dev | 1 | 3 | 9.91 |
| 9 | relative_volume | 1 | 3 | 9.83 |
| 10 | roc_acceleration | 1 | 5 | 9.65 |
| 11 | shock_vol_roc | 1 | 4 | 9.51 |
| 12 | momentum | 2 | 3 | 9.36 |
| 13 | shock_vol_roc | 3 | 3 | 9.21 |
| 14 | atr_roc5 | 1 | 4 | 9.18 |
| 15 | roc_acceleration | 2 | 3 | 9.14 |
| 16 | shock_vol_roc | 3 | 4 | 9.09 |
| 17 | shock_vol_roc | 1 | 5 | 9.04 |
| 18 | bb_pctb | 3 | 3 | 8.94 |
| 19 | roc_acceleration | 2 | 4 | 8.94 |
| 20 | momentum | 2 | 4 | 8.89 |

## Best Configuration per Scoring Method

| Scoring Method | Best Group | Best MaxPos | Norm Weekly % |
| --- | --- | --- | --- |
| **momentum** | **1** | **3** | **13.11** |
| atr_roc5 | 1 | 3 | 10.48 |
| roc_acceleration | 1 | 3 | 10.37 |
| shock_vol_roc | 1 | 3 | 10.20 |
| atr_vwap_dev | 1 | 3 | 9.91 |
| relative_volume | 1 | 3 | 9.83 |
| bb_pctb | 3 | 3 | 8.94 |
| range_expansion | 3 | 3 | 4.34 |

## Detailed Normalized Weekly Returns (%)

| Scoring | G1 max3 | G1 max4 | G1 max5 | G2 max3 | G2 max4 | G2 max5 | G3 max3 | G3 max4 | G3 max5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| momentum | **13.11** | **12.17** | **11.02** | 9.36 | 8.89 | 8.51 | 7.94 | 7.23 | 6.69 |
| atr_roc5 | 10.48 | 9.18 | 7.32 | 7.79 | 5.83 | 4.65 | 8.73 | 7.07 | 5.64 |
| roc_acceleration | 10.37 | 10.17 | 9.65 | 9.14 | 8.94 | 7.89 | 8.59 | 7.88 | 7.42 |
| shock_vol_roc | 10.20 | 9.51 | 9.04 | 8.64 | 7.71 | 7.53 | 9.21 | 9.09 | 8.67 |
| atr_vwap_dev | 9.91 | 8.30 | 6.63 | 8.05 | 6.03 | 4.81 | 7.99 | 6.55 | 5.23 |
| relative_volume | 9.83 | 8.72 | 7.82 | 7.69 | 6.98 | 6.71 | 7.76 | 7.76 | 7.01 |
| bb_pctb | 7.93 | 6.85 | 6.85 | 6.73 | 6.28 | 5.91 | 8.94 | 7.96 | 6.92 |
| range_expansion | 3.94 | 2.96 | 2.36 | 3.92 | 2.94 | 2.35 | 4.34 | 3.25 | 2.59 |

## Key Findings

### 1. Momentum is the clear winner
Momentum Group 1 with max_positions=3 produces **13.11%/week** normalized return,
beating every other combination by a wide margin (+2.6% over the runner-up). This
holds consistently across all sample sizes (N=9, 11, 15) — not a single-run fluke.

### 2. Group 1 (top decile) dominates for 6 of 8 methods
Group 1 is the best group for: momentum, atr_roc5, roc_acceleration, shock_vol_roc,
atr_vwap_dev, and relative_volume. Only bb_pctb and range_expansion favor Group 3
(3rd-highest decile), and both are weaker methods overall.

### 3. Fewer positions (max_positions=3) is universally better
Every single scoring method achieves its best result with max_positions=3. The
pattern is monotonic: max3 > max4 > max5 across the board. Concentrating capital
in fewer positions amplifies the alpha from stock selection.

### 4. Scoring method tiers
- **Tier 1 (>10%/week):** momentum (13.1%), atr_roc5 (10.5%), roc_acceleration (10.4%), shock_vol_roc (10.2%)
- **Tier 2 (8-10%/week):** atr_vwap_dev (9.9%), relative_volume (9.8%), bb_pctb (8.9%)
- **Tier 3 (<5%/week):** range_expansion (4.3%) — significantly worse, likely not useful

### 5. Shock_vol_roc and roc_acceleration are the most robust
These two methods show the smallest spread between groups and between max_positions
settings. Even their worst configurations (G3 max5) still return ~7-8%/week. If you
want a scoring method that is less sensitive to group/position choices, these are
safer picks than momentum (whose G3 max5 drops to 6.7%).

### 6. ATR-filtered methods (atr_roc5, atr_vwap_dev) have high variance
These methods show the largest gap between max3 and max5 (~3-5% spread), suggesting
the ATR filter produces a smaller survivor set that benefits more from concentration.

## Recommendation

**Primary:** momentum, Group 1, max_positions=3
**Fallback:** roc_acceleration, Group 1, max_positions=3 (more robust, less sensitive to configuration)
