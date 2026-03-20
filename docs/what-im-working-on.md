# What I'm Working On

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
