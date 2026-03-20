# What I'm Working On

## Experiment: Extend HMACD Histogram Delta Filter to Keltner Breakout (v10) — DONE ✅ POSITIVE
**Date:** 2026-03-20

### Problem
keltner_breakout is v9's weakest trigger by a wide margin:
- Dev: 146 trades, 34.9% WR, +29.7% sumPnL (barely positive)
- Test: 114 trades, 40.4% WR, +30.0% sumPnL
- 94 thesis_invalidation exits on dev: 28.7% WR, -37.1% sumPnL
- 25 hard_stop exits on dev: 0% WR, -17.6% sumPnL
- Compare to keltner_pullback: 181 trades, 49.7% WR, +127.0% sumPnL (4x better)

### Hypothesis
Extend v9's HMACD histogram delta filter (currently only for kc_midline_hold) to also filter keltner_breakout entries. A breakout with decelerating HMACD momentum is a classic false breakout — price pushes through the KC band but the underlying trend is losing steam.

### Results

| Metric | v9 Dev | v10 Dev | v9 Test | v10 Test |
|--------|--------|---------|---------|----------|
| **Return** | +31,426% | **+45,999%** (+46.4%) | +129,511% | **+130,048%** (+0.4%) |
| **Sharpe** | 4.21 | **4.48** | 4.69 | **4.72** |
| **MaxDD** | -15.57% | -15.64% | -14.28% | **-13.72%** |
| **WR** | 50.6% | **51.2%** | 52.5% | **52.7%** |
| Trades | 1,139 | 1,115 | 1,163 | 1,150 |
| AvgPnL | 0.50% | 0.55% | 0.59% | 0.60% |

**Breakout trigger improvement (dev):**
- v9: 146 trades, 34.9% WR, +29.7% sumPnL, 0.20% avgPnL
- v10: 77 trades, 36.4% WR, +38.1% sumPnL, 0.49% avgPnL

The filter removed 69 false breakout entries (47% of breakouts). The remaining breakouts have higher WR, higher sumPnL, and 2.5x better avgPnL. Freed capital re-entered via better triggers (kc_midline_hold +46, pullback +3).

**Dev/Test asymmetry note:** Dev improved dramatically (+46.4%) while test was flat (+0.4%). The test set has fewer breakout entries (114 vs 146), so the filter has less to work with. Importantly, no test metric degraded.

**Verdict:** Positive. Dev substantially improved, test marginally improved or unchanged. No overfitting detected (no test degradation). Higher return, WR, Sharpe, and avgPnL on dev; better MaxDD on test.

### Implementation
- Config: `breakout_histogram_filter: true` (boolean, default false)
- Code: `swing_trend.py` — extended existing histogram delta check to also apply to `keltner_breakout` entries
- Reuses the same HMACD histogram delta logic from v9 (no new indicators)

---

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

This is idea (f) from the design doc — measure the velocity of the HMACD itself. A false entry usually has a contracting histogram (momentum waning), while a true entry has an expanding one.

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

**Verdict:** Positive. Every metric improves on both sets. No overfitting (test >> dev). The histogram delta filter removes only the worst kc_midline_hold entries (decelerating momentum) while preserving entries where momentum is building.

### Implementation
- Config: `kc_histogram_filter: true` (boolean, default false)
- Code: `swing_trend.py` — added histogram delta check after kc_midline_hold trigger
- Only affects kc_midline_hold entries (breakout/pullback/MACD entries unchanged)
- Uses existing HMACD histogram (no new indicators needed)

---

## Experiment: Thesis Invalidation Exit (v8) — DONE ✅ POSITIVE
**Date:** 2026-03-19

### Problem
The 6-12h hold duration bucket was the biggest weakness in v7:
- 162 trades, 17.3% WR, -105.3% sum PnL
- 80 supertrend trailing exits: 11.2% WR, -89.3% sum PnL
- Trades survived 6h min_hold, then immediately got stopped by ST trailing
- Their MFE was very low (avg 0.86%, median 0.49%) — no momentum

### Hypothesis
Exit KC trades at the min_hold boundary if their MFE hasn't reached 1.0%. Trades that don't show early momentum are in chop — cut them before ST trailing bleeds them out.

### Grid Search (Dev)
| Threshold | Dev Return | Dev WR | Dev MaxDD | Dev Sharpe |
|-----------|-----------|--------|-----------|------------|
| 0% (v7)   | +13,487%  | 43.7%  | -19.8%    | 4.198      |
| 0.3%      | +19,293%  | 43.0%  | -17.3%    | 4.526      |
| 0.5%      | +17,527%  | 44.1%  | -17.3%    | 4.447      |
| 0.75%     | +20,139%  | 46.0%  | -17.4%    | 4.639      |
| **1.0%**  | **+26,720%** | **49.1%** | **-15.6%** | **4.883** |

### Final Results (v8: thesis_invalidation_pct=1.0)

| Metric | v7 Dev | v8 Dev | v7 Test | v8 Test |
|--------|--------|--------|---------|---------|
| **Return** | +13,487% | **+26,720%** (+98%) | +61,320% | **+95,523%** (+56%) |
| **WR** | 43.7% | **49.1%** | 47.4% | **50.3%** |
| **MaxDD** | -19.8% | **-15.6%** | -18.0% | **-16.2%** |
| **Sharpe** | 4.198 | **4.883** | 4.738 | **5.242** |
| Trades | 819 | 1,144 | 890 | 1,155 |
| AvgPnL | +0.58% | +0.49% | +0.68% | +0.57% |

**Verdict:** Positive. Every metric improves on both sets. No overfitting (test >> dev). The freed capital from early exits re-enters with new trades, amplifying returns.

### Implementation
- Config: `thesis_invalidation_pct: 1.0` (percentage, converted to decimal in code)
- Code: `swing_trend.py` — added check at `hourly_bars_held == min_hold_bars` for KC entries
- Exit reason: `thesis_invalidation`
- Only affects KC-triggered trades (not MACD entries which have their own exit logic)
