# LazySwing Improvement Context

## Experiment 10: Confirmation Supertrend (Dual-ST Filter) — STATUS: DONE (CONDITIONALLY POSITIVE)

### Hypothesis
A wider/slower "confirmation" Supertrend can filter whipsaw entries. When the primary
ST(20,2.5) flips, only enter the new direction if the confirmation ST agrees. If it
disagrees (the wider ST hasn't flipped yet), exit the current position but stay flat
until both STs align. This preserves optimal exit timing (Exp 4 lesson) while filtering
the lower-quality entries that the wider ST doesn't confirm.

### Motivation
Analysis of recent prod losses (March 2026): 4-5 consecutive whipsaw losses in a ranging
BTC market (66k-68k). The primary ST kept flipping in a tight range. A wider confirmation
ST would not flip during such ranges, preventing entries on these whipsaw signals.

### Key Analytical Finding — Wider ST Direction Predicts Trade Outcome
Before implementing, we verified that the wider ST direction at entry time discriminates
winners from losers (analysis of v3 trade logs with various wider ST configs):

| Wider ST Config | Agree WR (DEV) | Disagree WR (DEV) | Gap | Agree WR (TEST) | Disagree WR (TEST) | Gap |
|-----------------|---------------|-------------------|-----|----------------|-------------------|-----|
| ST(24, 3.0) | 77.3% | 65.2% | 12.1pp | 72.8% | 59.9% | 12.9pp |
| ST(28, 3.0) | 77.1% | 65.2% | 11.9pp | 73.0% | 59.4% | 13.6pp |
| ST(32, 3.0) | 77.0% | 65.0% | 12.0pp | 72.7% | 59.5% | 13.2pp |

This is the **strongest single predictor of trade quality found** — 12pp WR gap on DEV,
13pp on TEST, consistent across all wider ST configurations tested.

### Implementation
Added `confirm_st_atr_period` and `confirm_st_multiplier` parameters to LazySwingStrategy.
When `confirm_st_atr_period > 0`:
1. A second Supertrend is computed on the same hourly bars
2. On primary ST flip → exit immediately (as always)
3. Before entering opposite side → check if confirmation ST agrees with the new direction
4. If agrees → enter normally
5. If disagrees → stay flat (cash), re-check every hourly close
6. When both STs align → enter at next hourly close

Default: `confirm_st_atr_period=0` (disabled, current v3 behavior).

### Results — Actual Backtests (5m bars, full strategy)

**DEV (2022-2024):**

| Config | Trades | WR | Avg PnL | Avg Win | Avg Loss | <12h Trades | Return |
|--------|--------|-----|---------|---------|----------|-------------|--------|
| Baseline (v3) | 716 | 71.4% | +2.15% | +3.31% | −0.74% | 155 | 209,425,000% |
| + ST(24,3.0) | 643 | **72.0% (+0.6pp)** | +2.22% | +3.36% | **−0.71%** | 135 | 73,234,106% |
| + ST(32,3.0) | 649 | 71.5% (+0.1pp) | +2.20% | +3.37% | −0.74% | 133 | 72,865,437% |
| + ST(32,3.5) | 591 | 71.6% (+0.2pp) | +2.28% | +3.47% | **−0.72%** | 110 | 32,963,742% |

**TEST (2020-2026):**

| Config | Trades | WR | Avg PnL | Avg Win | Avg Loss | <12h Trades | Return |
|--------|--------|-----|---------|---------|----------|-------------|--------|
| Baseline (v3) | 768 | 66.1% | +2.11% | +3.74% | −1.06% | 169 | 358,285,159% |
| + ST(24,3.0) | 673 | **68.5% (+2.4pp)** | +2.29% | +3.80% | **−1.01%** | 129 | 162,795,042% |
| + ST(28,3.0) | 672 | **68.6% (+2.5pp)** | +2.31% | +3.81% | **−0.97%** | 128 | 187,136,896% |
| + ST(32,3.0) | 675 | **68.6% (+2.5pp)** | +2.30% | +3.81% | **−1.00%** | 125 | 184,139,674% |

### Analysis

**What improves:**
- WR increases on both datasets (DEV +0.6pp, TEST +2.5pp) — the only approach since
  Experiment 2 to achieve this
- Average PnL per trade improves (+0.07pp DEV, +0.20pp TEST)
- Average loss improves (less negative: DEV −0.74%→−0.71%, TEST −1.06%→−0.97%)
- Short-duration whipsaw trades reduced by ~13% (DEV: 155→135, TEST: 169→128)
- Short-trade WR also improves (DEV: 51→52.6%, TEST: 35.5→39.1%)

**The cost:**
- Trade count drops ~10-12% (fewer trades taken due to cash periods during disagreement)
- Compound return reduced ~65% on DEV (209B%→73B%), ~48% on TEST (358B%→163B%)
- In absolute terms still enormous returns, but in relative terms ~2-3x less

**Why it works (and why it's different from Experiment 1):**
Experiment 1 showed that any entry filter destroys compounding. This filter is different
because it's not based on a noisy/arbitrary indicator — it uses the SAME type of signal
(Supertrend) at a different scale. The wider ST captures the higher-timeframe trend
structure. When it disagrees, the primary flip is genuinely more likely to be a whipsaw
within a larger trend, not a true trend reversal. The 12pp WR gap confirms this is
capturing real structure, not noise.

The return cost comes from going flat during disagreement periods. Some of these periods
would have been profitable trades (the disagree group still has 65% WR on DEV). But the
trades we skip are significantly lower quality than the ones we take.

### Best Configuration
**ST(24,3.0)** — Only config that improves WR on BOTH datasets:
- DEV: +0.6pp WR, +0.07pp avg PnL, −0.03pp avg loss improvement
- TEST: +2.4pp WR, +0.18pp avg PnL, −0.05pp avg loss improvement
- 10-12% fewer trades, 2-3x lower compound return

### Verdict: CONDITIONALLY POSITIVE

The confirmation ST objectively improves win rate and trade quality on both datasets. The
trade-off is moderate compound return reduction. Whether to deploy depends on the
operator's priority:
- **If maximizing compound return**: keep v3 baseline (no confirmation)
- **If prioritizing WR and reducing painful losing streaks**: enable confirm ST(24,3.0)

The confirmation ST is implemented as an optional parameter — the live config can enable
it without any other code changes. Recommended for evaluation in paper trading before live.

---

## Experiment 9: Minimum Holding Period (Anti-Whipsaw) — STATUS: DONE (NEGATIVE)

### Hypothesis
Suppress ST flip exits for the first N hourly bars after entry. If the ST flips back
during the hold period, the whipsaw is absorbed for free. If the flip was real, we take
a slightly bigger loss but avoid two whipsaw trades.

### Results

| Min Hold | Trades | WR | Avg PnL | Return (DEV) | Return (TEST) |
|----------|--------|-----|---------|-------------|--------------|
| 0h (baseline) | 716/768 | 71.4% / 66.1% | +2.15% / +2.11% | 209B% | 358B% |
| 2h | 716/768 | 70.4% / 65.6% | +2.09% / +2.08% | 145B% | 275B% |
| 4h | 714/768 | 68.6% / 63.7% | +2.02% / +2.01% | 86B% | 159B% |
| 6h | 714/764 | 65.3% / 62.7% | +1.87% / +1.93% | 29B% | 80B% |
| 12h | 695/736 | 60.4% / 59.2% | +1.65% / +1.62% | 4.5B% | 4.8B% |

### Verdict: NEGATIVE — Every increase in min hold uniformly reduces WR AND return.

Holding through a real ST flip means the price has already moved significantly against the
position. The flip IS informative — suppressing it makes losses bigger without recovering
enough on the whipsaws that flip back.

---

## Key Finding: Holding Period Dominates Trade Outcome

Analysis of v3 trade logs across both datasets:

| Holding Period | DEV Trades | DEV WR | DEV Avg PnL | TEST Trades | TEST WR | TEST Avg PnL |
|---------------|-----------|--------|-------------|------------|--------|-------------|
| < 4h | 41 | 61.0% | +0.30% | 41 | 24.4% | −0.55% |
| 4-12h | 114 | **47.4%** | +0.07% | 128 | **39.1%** | −0.18% |
| 12-24h | 159 | 56.6% | +0.64% | 197 | 50.8% | +0.16% |
| 1-3d | 315 | **81.3%** | +2.40% | 327 | **84.1%** | +3.04% |
| 3-7d | 84 | **98.8%** | +7.31% | 73 | **97.3%** | +8.27% |

Short-duration trades (< 12h) are the primary WR drag: 155 trades at 51% WR (DEV),
169 trades at 35.5% WR (TEST). These are whipsaw trades where the ST flips back quickly.
Trades lasting 1+ days have 81-84% WR.

This motivated Experiments 8, 9, and 10 — all attempts to reduce the impact of these
short-duration whipsaws.

---

## Experiment 8: Delayed Entry After ST Flip — STATUS: DONE (NEGATIVE)

### Hypothesis
After an ST flip, exit immediately (preserving Exp 4 timing) but delay entering the
opposite side by N hourly closes. Go flat during the delay. If the ST flips back before
the delay expires, the whipsaw entry is avoided entirely.

### Results

| Delay | Mult | Trades | WR | Avg PnL | Return (DEV) |
|-------|------|--------|-----|---------|-------------|
| 0h | 2.5 | 716 | 71.4% | +2.15% | 209,425,000% |
| 1h | 2.5 | 709 | **51.6%** | +1.21% | 298,789% |
| 2h | 2.5 | 697 | 51.8% | +1.26% | 377,026% |
| 3h | 2.5 | 688 | 50.7% | +1.22% | 272,294% |
| 0h | 2.7 | 674 | 67.5% | +2.08% | 51,272,223% |
| 1h | 2.7 | 670 | 50.0% | +1.10% | 83,794% |
| 0h | 3.0 | 572 | 66.4% | +2.22% | 12,434,142% |
| 1h | 3.0 | 569 | 47.5% | +1.23% | 50,618% |

### Verdict: NEGATIVE — Catastrophically bad.

Delaying entry by even 1 hour drops WR from 71.4% to 51.6% (-20pp). The entry price
degrades significantly during the delay — the move in the new direction often happens in
the first hour after the flip. By entering late, we get a worse price and the trade is more
likely to end in a loss when the eventual reversal happens.

Higher multiplier (2.7, 3.0) with delay is even worse. The combination of reduced WR from
wider bands AND delayed entry is catastrophic.

---

## Experiment 7: Flat Trade Indicator Correlation Analysis — STATUS: DONE (INFORMATIONAL)

### Hypothesis
Trades that end up "flat" (< 0.25% PnL per holding day) are wasted round-trips that pay
transaction costs for no meaningful return. If we can identify market conditions at entry
that predict flat outcomes, we could potentially filter or size-reduce those trades.

Tested 3 volatility indicators and 2 volume-based directional-pressure indicators, all
computed on 1h bars at each trade's entry timestamp.

### Indicators Tested

**Volatility (3):**
1. ATR% (14) — ATR(14) / close, normalised range-based volatility
2. Bollinger Band Width (20, 2σ) — (upper − lower) / middle
3. Realised Volatility (20h) — rolling 20-period std-dev of hourly log returns

**Directional pressure / volume-based (2):**
1. OBV Slope (20h) — linear regression slope of On-Balance Volume over 20 bars
2. Volume Imbalance Ratio (20h) — sum(volume on up candles) / sum(volume on down candles)

### Data
- Dataset: dev (BTC 2022-2024, v3 config)
- 716 round-trip trades total
- 73 classified as flat (|pnl_pct / days_held| < 0.25%)
- 643 non-flat
- All 73 flat trades exited via st_flip (none via proximity)
- Split: 38 long, 35 short

### Results — Volatility Indicators (all significant)

| Indicator | Mean (flat) | Mean (non-flat) | Point-biserial r | Spearman ρ (|pnl/d| vs ind) |
|-----------|------------|-----------------|------------------|-------------------------------|
| ATR% (14) | 0.67% | 0.83% | r = −0.107, p = 0.004** | ρ = +0.209, p < 0.001*** |
| BB Width (20, 2σ) | 2.37% | 3.11% | r = −0.105, p = 0.005** | ρ = +0.211, p < 0.001*** |
| Realised Vol (20h) | 0.43% | 0.57% | r = −0.129, p = 0.0005*** | ρ = +0.244, p < 0.001*** |

All three volatility measures tell the same story: **flat trades enter in low-volatility
environments**. When volatility at entry is low, the Supertrend flip produces a near-zero
move — the flip is whipsawing through noise rather than catching a real trend shift.

Realised Volatility has the strongest effect (highest ρ = 0.244, most significant
point-biserial p = 0.0005).

### Results — Directional Pressure Indicators (weak / not significant)

| Indicator | Mean (flat) | Mean (non-flat) | Point-biserial r | Spearman ρ |
|-----------|------------|-----------------|------------------|------------|
| OBV Slope (20h) | −323 | +20 | r = −0.081, p = 0.03* | ρ = +0.045, p = 0.23 |
| Vol Imbalance (20h) | 0.995 | 1.079 | r = −0.051, p = 0.17 | ρ = +0.071, p = 0.06 |

OBV Slope is marginally significant for the binary classification (p = 0.03) — flat trades
enter when OBV is declining (waning volume conviction). But it does NOT correlate with the
continuous |pnl/day| (p = 0.23), so the effect is fragile.

Volume Imbalance is not significant at the 0.05 level. Flat trades enter when up/down volume
is roughly balanced (~1.0), but p-values of 0.06–0.17 mean this could be noise.

### Why Volume Indicators Are Weak
LazySwing enters on Supertrend flips, which are ATR-based (price range). The entry trigger is
inherently a volatility event — it doesn't require volume confirmation. Volume conditions at
entry are orthogonal to the trigger mechanism, so they don't strongly predict trade outcome.
Volatility indicators correlate because they measure the same thing the trigger depends on.

### Practical Implications

**This confirms Experiment 1's finding from a different angle:** low-volatility entries produce
flat/losing trades, and filtering them would improve WR. But Experiment 1 proved that any
entry filter destroys compounding returns in an always-in-market strategy.

Potential uses that preserve always-in-market:
- Position sizing: reduce size when realised vol is below a threshold (e.g. < 0.43%)
- Tighter proximity exits in low-vol regimes (cheaper to re-enter when wrong)
- Awareness metric: track vol at entry in live dashboard as a confidence signal

### Verdict: INFORMATIONAL

Low volatility at entry reliably predicts flat outcomes (p < 0.001). Volume-based pressure
indicators add minimal information. The correlation is real but moderate (ρ ≈ 0.24), meaning
vol alone is not a precise filter. Combined with Experiment 1's lesson (don't skip trades),
the actionable path would be position sizing or regime-aware parameters, not entry filtering.

Script: `analyze_flat_trades.py`. Flat trades CSV: `reports/flat_trades_dev.csv`.

---

## Experiment 6: OBV MACD T-Channel as Entry/Exit Signal or Early-Exit Overlay — STATUS: DONE (NEGATIVE)

### Hypothesis
The OBV MACD indicator (from a TradingView Pine Script) combines On-Balance-Volume with MACD
and a T-Channel step filter to produce trend-direction signals. Two hypotheses were tested:

1. **Standalone strategy:** Use OBV MACD T-Channel flips as the sole entry/exit signal (replacing
   Supertrend), operating always-in-market. If the T-Channel captures volume-driven trend changes
   better than ST, it could yield higher returns.
2. **Early-exit overlay:** Use the OBV MACD T-Channel as an early-exit signal for LazySwing — when
   the T-Channel flips against the current LazySwing trade before the ST does, exit early to preserve
   profit, then resume trading on the next ST flip.

### Indicator Pipeline (TradingView "OBV MACD Indicator")
1. OBV Shadow — normalize OBV deviation by its stdev, scale by price-range stdev, anchor to high/low
2. Fast line — DEMA(shadow, ma_length)
3. Slow line — EMA(close, slow_length) (note: EMA of price, not OBV)
4. MACD — Fast − Slow
5. Signal — rolling linear-regression endpoint of MACD over signal_length bars
6. T-Channel — Alex Grover step function; flips direction (+1/−1) only when the signal breaks
   its running mean-absolute-deviation band

### Standalone Strategy Results

Two-phase grid search on dev (1,514 combos, ~2 min):
- Phase 1: swept ma_length, slow_length, signal_length with default v_len/window_len/tchannel_p
- Phase 2: top-10 from Phase 1, swept v_len, window_len, tchannel_p

| Metric | Best OBV MACD (dev) | LazySwing v3 (dev) |
|--------|--------------------|--------------------|
| Total Return | +759% | +209,425,000% |
| Trades | 889 | 716 |
| Win Rate | 44.7% | 71.4% |
| Best params | ma=25, slow=65, sig=20, v=20, w=14, tp=1.5 | ST(20, 2.5) |

The OBV MACD T-Channel tops out at ~45% win rate regardless of parameters. With compounding over
~900 trades, this is catastrophic — roughly 5 orders of magnitude worse than LazySwing.

### Early-Exit Overlay Results

Used the best OBV MACD params to check whether T-Channel flips against LazySwing trades can
predict profitable early exits (analyzed on all 716 LazySwing v3 dev trades):

| Metric | Value |
|--------|-------|
| Trades where OBV MACD flipped early | 328 / 716 (45.8%) |
| Beneficial early exits (saved profit) | 173 / 328 (52.7%), avg +0.66% |
| Harmful early exits (lost profit) | 155 / 328 (47.3%), avg −2.79% |
| Net avg impact per early exit | −0.97% |
| Compounded: LazySwing only | 359,227,121% |
| Compounded: Hybrid (OBV early exit) | 19,162,632% |
| Hybrid / LazySwing ratio | 0.053× (18× worse) |

The OBV MACD is essentially a coin flip for early-exit decisions. It fires on ~46% of trades, is
right about half the time, and the cost of being wrong (cutting a winner short, avg −2.79%) far
exceeds the benefit of being right (saving on a reversal, avg +0.66%).

### Root Cause
The OBV MACD T-Channel responds to volume-weighted momentum shifts that are frequently transient.
The Supertrend band, by contrast, is a price-and-volatility-based level that acts as structural
support/resistance. The T-Channel flips too eagerly — it detects reversals that ultimately don't
materialize because the ST band holds.

This is the same structural problem seen in Experiments 4 and 5: the ST flip is the optimal exit
point for this class of always-in-market trend strategy. Signals that attempt to anticipate the
flip introduce more false positives than true positives.

### Verdict: NEGATIVE — Discarded

Code preserved on branch `experiment-obv-macd` (not merged to main).

---

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
