# LazySwing Experiment Archive

_Completed experiments are moved here from context-lazyswing.md to keep the working document clean._

## Sub-Hourly Resample & Parameter Grid Search (Apr 2026)

### Motivation
v4 runs on 1h resampled bars with ST(ATR=10, M=2.0). Tested whether faster resample intervals
(30m, 15m, 10m) with re-tuned ST parameters could improve returns or trade quality.

### Master Comparison Table

All results on BIP-20DEC30-CDE 5m bars. Test: Jul-Dec 2025, Live: Jan-Apr 2026.

| Config | Set | Return% | Sharpe | MaxDD% | Trades | WR% | AvgPnL% |
|--------|-----|---------|--------|--------|--------|-----|---------|
| **1h ATR10 M2.0 (v4 baseline)** | test | +533.7 | 11.48 | -6.21 | 161 | 62.7 | +1.00 |
| **1h ATR10 M2.0 (v4 baseline)** | live | +306.0 | 10.56 | -6.55 | 81 | 65.4 | +1.63 |
| **1h ATR10 M1.5** | test | +1,113 | 14.00 | -4.81 | 228 | 68.4 | +0.94 |
| **1h ATR10 M1.5** | live | +604 | 15.20 | -5.26 | 129 | 72.1 | +1.39 |
| 30m ATR14 M2.0 | test | +734 | 12.83 | -5.85 | 336 | 51.8 | +0.46 |
| 30m ATR14 M2.0 | live | +535 | 14.29 | -5.53 | 164 | 59.8 | +0.99 |
| **30m ATR14 M1.5** | test | +1,708 | 14.90 | -3.77 | 462 | 56.1 | +0.45 |
| **30m ATR14 M1.5** | live | +823 | 15.74 | -5.58 | 256 | 60.5 | +0.71 |
| 30m ATR10 M1.5 | test | +1,512 | 14.42 | -4.57 | 480 | 53.8 | +0.40 |
| 30m ATR10 M1.5 | live | +696 | 16.03 | -5.60 | 266 | 57.1 | +0.62 |

### Key Findings

1. **15m and 10m are not viable.** 10m only has 2 bars per resample from 5m data — pure noise
   (WR 33-40%). 15m is better but no combo has AvgPnL above 0.50%.

2. **1h ATR10 M1.5 is the best balanced config:**
   - 2x return vs v4 baseline on both sets
   - Higher Sharpe (14.0/15.2 vs 11.5/10.6)
   - Lower max drawdown (-4.81/-5.26 vs -6.21/-6.55)
   - Higher WR (68.4/72.1 vs 62.7/65.4)
   - AvgPnL well above 0.75% threshold on both sets (0.94/1.39)

3. **30m ATR14 M1.5 has the highest raw return** (+1,708% test, +823% live) and best test
   Sharpe (14.90), but lower WR (56/60%) and AvgPnL below 0.75% on test (0.45%).
   Trades 2-3x more frequently. Higher risk, higher reward.

4. **30m ATR14 M2.0** has the best live AvgPnL among 30m configs (+0.99%) but test AvgPnL
   is only +0.46%.

### HMACD & Indicator Entry Filters on 30m ATR14 M1.5

Tested HMACD golden-cross lookback as entry confirmation and relaxed indicator filters.

| Filter | Set | Return% | Trades | Kept% | WR% | AvgPnL% |
|--------|-----|---------|--------|-------|-----|---------|
| Baseline (no filter) | test | +1,708 | 462 | 100% | 56.1 | +0.447 |
| Baseline (no filter) | live | +823 | 256 | 100% | 60.5 | +0.707 |
| HMACD(12/26/9) lb=8 | test | +474 | 385 | 83% | 57.4 | +0.462 |
| HMACD(12/26/9) lb=8 | live | +399 | 212 | 83% | 60.4 | +0.777 |
| ADX>=20 | test | +550 | 402 | 87% | 56.7 | +0.475 |
| ADX>=20 | live | +469 | 229 | 89% | 62.0 | +0.778 |

**Verdict:** All filters destroy returns through lost compounding. ADX>=20 is the least
harmful (keeps 87-89% of trades) but still drops test return from +1,708% to +550%.
The AvgPnL improvements are marginal (+0.02-0.03% on test). Consistent with the key
structural insight from Experiments 1-12: entry filters hurt always-in-market strategies.

### Decision (Apr 2026)

Deploy two configs in parallel for live comparison:
- **Live Coinbase:** 1h ATR10 M1.5 → **v5** (safest: highest WR, fattest AvgPnL)
- **Paper Binance:** 30m ATR14 M1.5 → **v5-30m** (highest return potential, more trades)
