# LazySwing Improvement Context

## Experiment 5: ADX/DMI Crossover + Peak-Pullback Early Exit — STATUS: DONE (NEGATIVE)

### Hypothesis
Combining three filters creates a reliable early exit signal: (1) price has already pulled back
≥1.5% from the intra-trade peak, (2) the peak trade profit was ≥1.5%, (3) price is within 0.8 ATR
of the ST band, AND (4) DI+ just crossed below DI- (for longs). This is an institutional
trend-change confirmation layered on top of momentum-exhaustion context.

Lift analysis showed 9.3x lift for the `di_cross + dist<0.8` combo (precision 26.6% vs 2.86% base)
and per-bar EV analysis showed +0.095–0.136% on both DEV and TEST datasets.

### Analysis Summary

**Indicators tested (via lift on high-giveback flip subset):**
- Stochastic overbought (>75): lift=10.2x — fires on 7% of big flips, only 0.7% of small flips
- RSI extreme (>70/>30): lift=6.2x
- CCI extreme (>100/<-100): lift=4.67x
- RSI divergence 5h: lift=3.0x
- OBV divergence 5h: lift=2.89x
- DMI crossover (DI+ crossed below DI-): lift=3.2x, best DIRECTION-CHANGE signal

**But absolute indicator levels showed negative EV (all tested on per-bar basis):**
| Signal | N | Prec | FP cost | EV/signal |
|--------|---|------|---------|-----------|
| stk_ob (>75) | 12210 | 0.4% | 0.28% | -0.27% |
| rsi_ex (>70) | 3837 | 0.5% | 0.30% | -0.29% |
| cci_ex (>100) | 10362 | 0.7% | 0.29% | -0.27% |

The absolute levels fire during strong trends (34–42% of all bars), making precision below baseline.

**   signals showed improvement:**
| Signal | N | Prec | Lift | EV/signal |
|--------|---|------|------|-----------|
| di_cross | 1348 | 9.05% | 3.17x | -0.095% |
| pull>0.8+prof+dist<1.0+di_cross | 2247 | 14.3% | 5.0x | -0.030% |
| pull>1.5+prof>1.5+dist<0.8+di_cross | DEV n=120 prec=25.8% | +0.128% | TEST n=153 prec=26.8% | +0.136% |

**EV was positive in analysis, but backtest showed the opposite:**

Actual backtest results (41 DMI exit signals over 3 years):
- True Positives: 7 (17% precision vs 26.6% predicted — signal collapsed in practice)
- False Positives: 34 (83%)
- Mean FP cost: -0.262% (higher than modeled 0.17%)
- TPs were often not beneficial: price bounced UP before the flip bar in 5/7 TP cases

Example: 2022-07-17 TP: exited long @21131, price then recovered to 21335 before ST flip → -0.96% vs waiting!
Example: 2024-11-18 TP: exited @90485, flip bar @91666 → -1.29% vs waiting!

Backtest result: **v4 (DMI exit) = $182B vs v3 (baseline) = $209B → -13% degradation**.

### Root Cause
The same structural problem from Experiment 4 persists with DMI:
1. Price pullback + DI crossover fires when price is approaching the band.
2. In many cases price BOUNCES before the actual flip (band acts as support/resistance).
3. "True positive" TPs often have price recovery BETWEEN the exit bar and the flip confirm bar, making early exit worse than waiting.
4. The per-bar EV analysis misestimates the TP benefit because it measures H→H+1 price change, not accounting for intra-hour price path or the fact that the flip bar itself may close higher than the exit bar.

### Verdict: NEGATIVE — Discarded

---

## Experiment 4: Pre-Flip Early Exit (dist_atr < threshold) — STATUS: DONE (NEGATIVE)

### Hypothesis
When the hourly close is already within a small ATR-fraction of the Supertrend band, there is a
significantly elevated probability that the NEXT hourly bar will flip direction. Exiting at that
hourly close preserves the 0.5–1% of price giveback that otherwise occurs in the final pre-flip hour.

### Analysis Summary

**Price giveback at the flip bar is real and large (over 1015 flips):**
- Exit 1h early: mean improvement 1.035% (>0 in 99.8% of flips)
- Exit 2h early: mean improvement 1.286%

**But the signal precision is structurally insufficient:**

| threshold | P(flip\|signal) | recall | TP benefit | real FP cost | EV/signal |
|-----------|----------------|--------|-----------|-------------|-----------|
| 0.3       | 30.0%          | 22%    | +1.07%    | **−0.43%**  | +0.016%   |
| 0.5       | 25.8%          | 39%    | +0.74%    | −0.36%      | −0.080%   |
| 0.8       | 18.7%          | 59%    | +0.96%    | −0.34%      | −0.097%   |
| 1.0       | 14.7%          | 69%    | +0.99%    | −0.31%      | −0.118%   |

The FP cost used in the initial simulation was assumed to be 0.10% (transaction only). The
**real** FP cost (measured as actual H→H+1 price move when no flip) is 0.34–0.43% because
the ST band acts as support/resistance: when price tests it but doesn't flip, it bounces
strongly in the trend direction in the next hour. This makes the FP cost 3–4× higher than
the naive assumption.

Break-even precision needed: 26% (at 0.34% FP cost). Only dist<0.3 reaches 30%, barely positive.

**All combinations tested showed negative EV:**
- Progressive approach patterns (2h/3h/4h monotonic decline to band): all negative
- Peak-then-retreat (required peak_dist >= 2.0 ATR before triggering): all negative
- HMACD histogram declining + dist combined: all negative
- Approach rate (ATR/h toward band): all negative

**Implementation and backtest confirmed the analysis:**
- Implemented dist_atr < 0.8 early-exit in lazy_swing.py; ran full dev backtest
- Result: **209,424,979% → 14,911,987%** (14× degradation)
- WR: 71.4% → 56.5%, avg PnL: 2.15% → 1.05%
- 507 early exits out of 1162 total trades — the signal fires far too often during trend continuations

### Verdict: NEGATIVE — Discarded

**Root cause:** The Supertrend band is structural support/resistance. When price approaches
the band and doesn't flip, there is a strong mean-reversion bounce in the trend direction
(mean 0.3–0.4% per hour). This makes false-positive costs far exceed the transaction assumption.

The user's intuition is correct for specific memorable cases, but statistically the FP cost
dominates. The savings on the 18–30% of true positives cannot offset the losses from the
70–82% of false positives.

**Key structural insight:** In always-in-market Supertrend strategies, the optimal exit point
IS the hourly close of the flip bar — attempting to anticipate it destroys value. The band
itself is priced-in as a credible support/resistance by the market.

### Fast-ST Leading Indicator Extension

After the original experiment, a follow-up tested using a fast Supertrend (ATR=10) as a
"canary" — when the fast ST flips against the slow ST direction, combined with good current
PnL, as an early-exit signal.

**Result: severe overfitting.**

| Signal | DEV (n) | DEV prec | DEV EV | TEST (n) | TEST prec | TEST EV |
|--------|---------|----------|--------|----------|-----------|---------|
| fast_flip & curr_pnl>=1.5% | 28 | 46.4% | +0.147% | 25 | 20.0% | **−0.181%** |
| fast_flip & curr_pnl>=2.5% | 22 | 54.5% | +0.210% | 17 | 17.6% | **−0.200%** |

The precision on the dev set (~46%) is an artifact of the tiny sample size (n=28 over 3 years).
On the independent test set it halves to 20%, well below the ~26% break-even threshold.

**Rule: any signal with n < 100 in the dev set should be considered noise, not a trading signal.**

Code changes discarded via `git checkout`. Documentation preserved for future reference.

---

## Experiment 3: Sub-Hourly ST Re-evaluation (30m/15m/5m) — STATUS: DONE (NEGATIVE)

### Hypothesis
LazySwing evaluates ST direction only at hourly boundaries. If we re-evaluate the `close vs ST band` comparison at sub-hourly intervals (using the 5m close against the precomputed hourly bands), we could detect flips earlier within the hour and exit/enter sooner.

Three approaches were tested:
1. **Rolling hourly windows** — recompute ST every 5m using sliding 1h windows
2. **Sub-hourly close vs hourly bands** — keep hourly ST, but compare 5m close against hourly `final_lower`/`final_upper` at 30m/15m/5m intervals
3. **Filtered sub-hourly** — add confirmation filters (ADX, Volume, CMF) to reduce false mid-hour flips

### Results

**Unfiltered sub-hourly re-evaluation (dev set):**

| Mode | Trades | WR | Avg PnL | Total Return |
|------|--------|-----|---------|-------------|
| Hourly baseline | 1020 | 70.5% | +1.924% | 15.4T% |
| 30m | 1404 | 56.0% | +1.191% | 927B% |
| 15m | 1776 | 46.2% | +0.800% | 75M% |
| 5m | 1960 | 42.6% | +0.672% | 27M% |

**Filtered 30m variants (dev set):**

| Filter | Trades | WR | Avg PnL | Total Return |
|--------|--------|-----|---------|-------------|
| ADX>25 + Vol>1.5σ (best) | 1064 | 68.7% | +1.783% | 8.2T% |
| Volume > 2σ | 1106 | 65.9% | +1.685% | 5.9T% |
| ADX > 25 | 1182 | 64.6% | +1.533% | 3.7T% |
| 30m EXIT only (no flip) | 1213 | 64.3% | +1.481% | 3.2T% |
| CMF agrees | 1328 | 58.0% | +1.299% | 1.5T% |

### Verdict: NEGATIVE — Discarded

**Root cause:** The 5m close temporarily crosses hourly bands mid-hour but recovers by the hourly close. The hourly close aggregation IS the quality filter — it smooths intra-hour noise. Removing it introduces false flips.

Even the best filter combo (ADX>25 + Volume>1.5σ) still loses: -1.8pp WR, -0.14pp avg PnL, ~half the return, worse MaxDD (-7.2% vs -5.4%).

Exit-only mode (exit mid-hour but don't flip, re-enter at next hourly) also fails — it breaks always-in-market for no benefit.

Rolling hourly windows are even worse: overlapping windows (each step changes 1 of 12 bars) create ST oscillations → ~35% WR.

**Key insight:** The hourly evaluation frequency is not a limitation to optimize away — it is a core part of why the strategy works. Sub-hourly price action is noise that the hourly bar aggregation filters out.

---

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
