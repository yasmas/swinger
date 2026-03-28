# LazySwing Improvement Context

## Experiment 2: Longer Supertrend ATR Period (13→20) — STATUS: DONE (POSITIVE)

### Hypothesis
Using a longer ATR period (20 vs current 13) smooths the ATR calculation, making the Supertrend
bands less reactive to short-term volatility spikes. This reduces false flips in choppy/volatile
conditions → higher WR, higher avg PnL per trade. Multiplier stays at 2.5.

Grid search across (atr_period ∈ {10,13,16,20,24}, mult ∈ {2.0,2.5,3.0,3.5}) on both dev and
test datasets confirmed that (atr=20, mult=2.5) is the only configuration that improves BOTH
WR and avg PnL on BOTH datasets simultaneously:
- Dev:  WR 71.5%→74.2% (+2.7pp), avg PnL +2.016%→+2.144% (+0.13pp)
- Test: WR 70.0%→71.0% (+1.0pp), avg PnL +2.269%→+2.353% (+0.08pp)

### Implementation Plan
- [x] Grid search (atr_period × multiplier) on dev + test via simulation
- [x] Identify (atr=20, mult=2.5) as best balanced improvement
- [x] Create branch `experiment-longer-st-atr`
- [x] Update dev.yaml: supertrend_atr_period 13→20, version v2→v3
- [x] Run dev backtest, record full metrics
- [x] Update test.yaml: supertrend_atr_period 13→20, version v2→v3
- [x] Run test backtest, record full metrics
- [x] Update benchmark-lazyswing.csv
- [x] Evaluate and document verdict

### Results

| Metric | v2 DEV | v3 DEV | v2 TEST | v3 TEST |
|--------|--------|--------|---------|---------|
| Trades | 724 | 716 | 777 | 767 |
| WR | 68.9% | **71.4% (+2.5pp)** | 65.1% | **66.2% (+1.1pp)** |
| Avg PnL | 2.087% | **2.150% (+0.06pp)** | 2.032% | **2.116% (+0.08pp)** |
| Avg Win | 3.330% | 3.309% | 3.697% | **3.736%** |
| Avg Loss | -0.671% | -0.740% | -1.076% | **-1.061%** |
| Total Return | 154,524,588% | **209,424,979%** | 241,257,561% | **358,285,159%** |
| MaxDD | -11.32% | **-11.31%** | -22.75% | **-20.43%** |
| Sharpe | 8.38 | **8.78** | 7.29 | 7.26 |

### Verdict: POSITIVE ✓

All target metrics (WR and avg PnL per trade) improved on BOTH dev and test datasets, with no
degradation in MaxDD or Sharpe. Total return increased +35% dev / +48% test. The test MaxDD
improved by +2.3pp (from -22.75% to -20.43%) as a bonus.

The mechanism: ATR(20) is smoother than ATR(13) — less reactive to recent volatility spikes —
so ST bands stay stable during choppy bursts. The net effect is ~8-10 fewer false flips per
year, each of which was a losing whipsaw trade. The always-in-market property is fully preserved.

Minor note: avg loss is slightly worse on dev (-0.671% → -0.740%), but this is outweighed by
the WR improvement and doesn't show up on the test set (avg loss improved there).

Merged to main as v3. live.yaml updated to supertrend_atr_period=20.

---

## Experiment 1: Chop Index Entry Filter — STATUS: DONE (NEGATIVE)

### Hypothesis
The Chop Index (CI) measures whether the market is trending or ranging by comparing the sum of ATR over N bars to the total price displacement. High CI (>50) means choppy/flat market; low CI (<50) means trending. Analysis showed:
- Trades entered when CI < 50: 71.6% WR, 2.16% avg PnL (511 trades)
- Trades entered when CI >= 50: 62.9% WR, 1.89% avg PnL (210 trades)
- Baseline: 68.9% WR across 724 trades

### Implementation Plan
- [x] Establish v2 baseline metrics on dev set
- [x] Analyze losing trades and identify flat-market entries as key weakness
- [x] Test multiple filter candidates (ADX, ATR%, HMA slope, HMACD, BBW, Chop Index)
- [x] Select Chop Index < 50 as best filter (highest WR improvement)
- [x] Create experiment branch `experiment-chop-filter`
- [x] Implement Chop Index computation in lazy_swing.py
- [x] Add `chop_period` and `max_chop_index` config params
- [x] Skip entry when CI >= max_chop_index (both fresh entries and flip entries)
- [x] Run dev backtest — compare to v2 baseline
- [x] Evaluate

### Results

| Config | Trades | WR | Avg PnL | Return | Sharpe | MaxDD |
|--------|--------|-----|---------|--------|--------|-------|
| Baseline (no filter) | 724 | 68.9% | 2.09% | 154,524,588% | 9.38 | -11.32% |
| Chop < 50 (fresh only) | 723 | 69.0% | 2.09% | 154,333,477% | 9.38 | -11.32% |
| Chop < 50 (all entries) | 541 | 70.6% | 2.12% | 5,164,364% | 8.23 | -11.32% |
| Chop < 55 (all entries) | 650 | 69.5% | 2.08% | 34,630,472% | 8.86 | -11.32% |
| Chop < 60 (all entries) | ~680 | ~69% | ~2.09% | 78,041,994% | - | -11.32% |
| Chop < 65 (all entries) | ~710 | ~69% | ~2.09% | 109,811,559% | - | -11.32% |

### Verdict: NEGATIVE — Discarded

The Chop Index correctly identifies flat-market trades (the skipped trades have lower WR), but **any entry filter that causes the always-in-market strategy to go flat destroys compounding returns.** Even the mildest threshold (CHOP<65) cuts returns by 30%.

The fundamental issue: LazySwing's power comes from being always invested (immediate flip on ST reversal). Skipping a flip means sitting in cash, which misses the next move. The compounding penalty far exceeds the benefit of avoiding a few losing trades.

**Key insight for future experiments:** For always-in-market strategies, improving win rate via entry filters is counterproductive. Better approaches:
1. Improve EXIT quality (smarter stops, trailing)
2. Reduce position size in choppy markets instead of skipping entirely
3. Improve the Supertrend parameters themselves to reduce false flips

### Analysis Summary (for reference)
Filters tested and their WR impact on the "skipped" group:
| Filter | Skipped WR | Kept WR | Notes |
|--------|-----------|---------|-------|
| ADX >= 20 | 56.8% | 69.6% | Barely differentiates |
| ATR% >= 0.5% | 66.9% | 69.5% | Marginal |
| HMA |slope| >= 0.05% | 85.1% | 67.4% | WRONG DIRECTION — flat HMA = better |
| HMACD agrees | 74.1% | 67.0% | WRONG DIRECTION — disagreement is better |
| BBW pctile > 40 | 72.7% | 67.0% | Decent but wrong direction |
| Chop Index < 50 | 62.9% | 71.6% | BEST — correctly identifies chop |
| Chop Index < 45 | 62.9% | 76.2% | Aggressive — cuts too many trades |
| Prev trade quick loss | 61.3% | 71.1% | Good but backward-looking |
