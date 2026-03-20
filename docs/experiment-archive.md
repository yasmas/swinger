# Experiment Archive

## Experiment: HMACD Histogram Delta Filter for KC Midline Hold (v9) — DONE ✅ POSITIVE
**Date:** 2026-03-19

### Problem
kc_midline_hold entries are v8's biggest weakness by volume:
- 550 total kc_midline_hold trades
- 291 → thesis_invalidation = -89.5% sum PnL (avg -0.31%)
- 107 → hard_stop = -47.4% sum PnL (avg -0.44%)
- 152 → supertrend_trailing = +389.1% (the profit engine)
- Net: +252.2%, but 72% of entries end in failure

### Hypothesis
Filter kc_midline_hold entries using HMACD histogram **delta** (rate of change). Require histogram to be expanding in the trade direction:
- LONG: histogram delta > 0 (momentum accelerating)
- SHORT: histogram delta < 0 (momentum decelerating)

### Iteration
1. First tried histogram **sign** filter (histogram > 0 for LONG): Too aggressive, removed 166 entries including 36 good ST trailing winners. Dev return dropped from +26,720% to +25,225%.
2. Switched to histogram **delta** filter (histogram expanding): Much better — only removes entries where momentum is actively decelerating, preserving entries where histogram is negative but improving.

### Results

| Metric | v8 Dev | v9 Dev | v8 Test | v9 Test |
|--------|--------|--------|---------|---------|
| **Return** | +26,720% | **+31,426%** (+17.6%) | +95,523% | **+129,511%** (+35.6%) |
| **Sharpe** | 4.06 | **4.21** | 4.36 | **4.69** |
| **MaxDD** | -15.58% | **-15.57%** | -16.24% | **-14.28%** |
| **WR** | 49.1% | **50.6%** | 50.3% | **52.5%** |
| Trades | 1,144 | 1,139 | 1,155 | 1,163 |
| AvgPnL | +0.49% | +0.50% | +0.57% | +0.59% |

**Verdict:** Positive. Every metric improves on both sets. No overfitting (test >> dev).

### Implementation
- Config: `kc_histogram_filter: true` (boolean, default false)
- Code: `swing_trend.py` — added histogram delta check after kc_midline_hold trigger
- Only affects kc_midline_hold entries (breakout/pullback/MACD entries unchanged)

---

## Experiment: Thesis Invalidation Exit (v8) — DONE ✅ POSITIVE
**Date:** 2026-03-19

### Problem
The 6-12h hold duration bucket was the biggest weakness in v7:
- 162 trades, 17.3% WR, -105.3% sum PnL
- 80 supertrend trailing exits: 11.2% WR, -89.3% sum PnL

### Hypothesis
Exit KC trades at the min_hold boundary if their MFE hasn't reached 1.0%.

### Results

| Metric | v7 Dev | v8 Dev | v7 Test | v8 Test |
|--------|--------|--------|---------|---------|
| **Return** | +13,487% | **+26,720%** (+98%) | +61,320% | **+95,523%** (+56%) |
| **WR** | 43.7% | **49.1%** | 47.4% | **50.3%** |
| **MaxDD** | -19.8% | **-15.6%** | -18.0% | **-16.2%** |
| **Sharpe** | 4.198 | **4.883** | 4.738 | **5.242** |

**Verdict:** Positive. Every metric improves on both sets.

### Implementation
- Config: `thesis_invalidation_pct: 1.0` (percentage, converted to decimal in code)
- Exit reason: `thesis_invalidation`
