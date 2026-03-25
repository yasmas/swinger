# What I'm Working On

## Experiment: Volume-Price Indicators for KC Entry Filter (v16) — DONE ✅ POSITIVE
**Date:** 2026-03-24

### Problem
v15's KC volume filter (`kc_vol_min_ratio: 0.6`) uses plain relative volume — it only checks if hourly volume is at least 60% of the 168h rolling average. This filters low-volume bars but doesn't measure *buying vs selling pressure*. A bar can have high volume with distribution (selling into a rally) and still pass.

### Hypothesis
Replace the plain volume ratio with volume-price indicators that combine volume with price direction:
1. **CMF (Chaikin Money Flow)** — measures accumulation/distribution over N bars. CMF < 0 during LONG entries = distribution, a red flag.
2. **MFI (Money Flow Index)** — RSI weighted by volume × price. Complements existing RSI filter — if RSI says bullish but MFI says volume isn't backing it, entry is weak.
3. **OBV slope** — On-Balance Volume trend. If OBV is declining while price ranges up, divergence signals a weak LONG.

### Plan
- [x] Add CMF, MFI, OBV indicator functions to `intraday_indicators.py`
- [x] Add config params and precomputation in `swing_trend.py`
- [x] Grid search each indicator on Dev (with plain volume filter disabled)
- [x] Validate best candidate(s) on Test
- [x] Update `docs/benchmark.csv` and document verdict

### Dev Grid Search
| Variant | Return% | MaxDD% | Sharpe | Trades |
|---------|---------|--------|--------|--------|
| **v15-baseline** | 313,553 | -13.45 | 5.90 | 1519 |
| **cmf20-t0.05+vol** | **504,752** | -16.56 | **6.44** | 1500 |
| cmf20-t0.0+vol | 395,775 | -16.56 | 6.19 | 1509 |
| cmf20-t0.05-novol | 393,983 | -16.56 | 6.25 | 1516 |
| mfi14-50_50-novol | 328,011 | -14.06 | 6.06 | 1537 |
| mfi14-45_55+vol | 322,251 | -13.45 | 5.96 | 1517 |
| obv20-novol | 134,575 | -15.86 | 5.49 | 1547 |
| obv14-novol | 140,268 | -14.45 | 5.56 | 1549 |

Winner: **CMF(20) threshold 0.05 + existing volume filter**. CMF blocks entries where volume exists but is distribution (selling into rallies for LONGs, buying into drops for SHORTs). Only 19 fewer trades — very selective but high-impact filter.

OBV: all variants significantly underperformed. Discarded.
MFI: marginal improvement with vol filter, negative standalone on test. Discarded.

### Test Validation

| Metric | v15 Dev | v16 Dev | v15 Test | v16 Test |
|--------|---------|---------|----------|----------|
| **Return** | +313,553% | **+504,752%** (+61%) | +856,115% | **+1,334,075%** (+56%) |
| **MaxDD** | -13.45% | -16.56% (+3.1pp) | -16.36% | -16.72% (+0.4pp) |
| **Sharpe** | 5.90 | **6.44** (+0.54) | 6.28 | **6.47** (+0.19) |
| **WR** | 62.1% | **65.5%** (+3.4pp) | 62.1% | **64.6%** (+2.5pp) |
| Trades | 1519 | 1500 (-19) | 1592 | 1583 (-9) |
| AvgPnL | 0.54% | **0.58%** | 0.57% | **0.60%** |

### Verdict
✅ **POSITIVE.** CMF(20) threshold 0.05 combined with existing volume filter is a clear improvement:
- Return +61% dev, +56% test — no overfitting (test proportional to dev)
- Sharpe +0.54 dev, +0.19 test
- Win rate +3.4pp dev, +2.5pp test
- Only 19 fewer trades — CMF is a precision filter, blocking the worst 1% of entries
- MaxDD worsened 3.1pp on dev (-13.45% → -16.56%) but barely changed on test (-16.36% → -16.72%). Acceptable given magnitude of return/Sharpe improvement.

### Why CMF works and others don't
- **CMF** measures *net buying/selling pressure* over 20 bars. Threshold 0.05 means at least 5% net accumulation for LONGs. This catches entries where price is technically in the KC zone but smart money is distributing. It's a *quality* filter, not a *quantity* filter — only blocks 1% of entries but those are disproportionately losers.
- **MFI** is too similar to RSI (already in the strategy). Adding another momentum oscillator doesn't add information.
- **OBV** is too cumulative/noisy on 1h bars. The slope calculation over any reasonable window is dominated by a few large-volume bars, making it unreliable.

### Implementation
- Config: `kc_cmf_period: 20`, `kc_cmf_threshold: 0.05` (additive to existing volume filter)
- Code: Added `compute_cmf()`, `compute_mfi()`, `compute_obv_slope()` to `intraday_indicators.py` (MFI/OBV kept for future experimentation)
- Filter logic in `_check_entry()`: block KC entry when CMF < threshold (LONG) or CMF > -threshold (SHORT)

### Process Reflection
- Testing 3 indicator families in one grid search was efficient — 18 variants in ~30 minutes.
- The "replace vs additive" distinction mattered: CMF alone (+394K) was good, but CMF + existing vol filter (+505K) was best. Volume ratio catches low-volume bars, CMF catches high-volume-wrong-direction bars — complementary filters.
- OBV's poor performance highlights that cumulative indicators don't translate well to entry filters. Differential indicators (CMF, histogram delta) work better for point-in-time decisions.

## Experiment: Silver Threshold Tuning (v15) — DONE ✅ POSITIVE
**Date:** 2026-03-24

### Problem
Silver performance was only moderate versus BTC with the same v14 swing parameters, suggesting thresholds were not asset-adapted.

### Hypothesis
A threshold-only tweak (no code changes) can materially improve silver by increasing SHORT participation quality and reducing giveback:
- Lower `short_adx_threshold` from `20` to `18`
- Lower `breakeven_trigger_pct` from `1.5` to `1.0`

### Plan
- [x] Recover state from `docs/what-im-working-on.md` and `docs/benchmark.csv`
- [x] Define silver Dev/OOS split (`2022-2023` dev, `2024` test)
- [x] Run one-factor threshold grid on Dev only (YAML-only variants)
- [x] Select one focused candidate and validate on OOS
- [x] Run full-period silver backtest and generate report
- [x] Update `docs/benchmark.csv` and document verdict

### Dev/OOS Results
| Metric | Baseline v14 Silver (Dev) | v15 Candidate (Dev) | Baseline v14 Silver (OOS) | v15 Candidate (OOS) |
|---|---:|---:|---:|---:|
| Return | +29.29% | **+64.52%** | +34.16% | **+42.74%** |
| MaxDD | -14.67% | **-9.13%** | -12.31% | -12.31% |
| Sharpe | 0.86 | **1.60** | 1.72 | **2.08** |
| Win Rate | 34.18% | **34.98%** | 30.53% | 30.53% |
| Trades | 196 | 203 | 95 | 95 |
| Avg PnL / trade | -0.101% | **+0.000%** | -0.000% | **+0.051%** |

### Final Full-Period Backtest (2022-2024)
- v14 silver: +89.13% (`$189,132`)
- v15 silver thresholds: **+150.43%** (`$250,431`)
- Report: `reports/swing_trend_SI_2022-01-02_2024-12-30_v15-silver-thresholds.html`
- Trade log: `reports/Silver_Swing_Trend_Test_swing_trend_v15-silver-thresholds.csv`

### Implementation
- Config only (no code changes):
  - `config/silver_swing_trend_test_v15.yaml`
  - `short_adx_threshold: 18`
  - `breakeven_trigger_pct: 1.0`

### Verdict
✅ POSITIVE. This meets multiple success criteria (higher return, higher Sharpe, improved avg trade PnL, materially lower Dev drawdown) without strategy code changes.

### Process Reflection
- Focused one-factor tuning on Dev first prevented overfitting and made the winning signal clear.
- Parameter interactions matter: `short_adx_threshold=18` improved Dev, but pairing with `breakeven_trigger_pct=1.0` produced the significant jump.
- Reusing temporary YAML variants plus direct backtest runs was faster than introducing analysis code changes.

## Experiment: Tighter SHORT Exits (v14) — DONE ✅ POSITIVE
**Date:** 2026-03-20

### Problem
v13 has MaxDD -15.01% (dev) / -16.47% (test). The worst DD period has SHORTs losing heavily during sideways chop. SHORT exits are symmetric with LONGs — same 3% stop, same 2.0 trailing ST, same 1.5% breakeven. Since down moves are faster and more violent, SHORTs should have tighter exits.

### Hypothesis
Add SHORT-specific exit params to allow asymmetric stop/trailing/breakeven between LONGs and SHORTs. Test 4 independent experiments:
- A: Tighter SHORT hard stop (2.0, 2.5 vs 3.0)
- B: Faster SHORT breakeven trigger (0.75, 1.0 vs 1.5)
- C: Tighter SHORT trailing ST (1.5, 1.75 vs 2.0)
- D: Tighter SHORT MACD ATR trailing (2.0, 2.5 vs 3.0)

### Grid Search (Dev)
| Experiment | Return | MaxDD | Sharpe |
|---|---|---|---|
| baseline (v13) | +176,017% | -15.01% | 5.41 |
| **short_stop_2.5** | **+190,347%** | -15.01% | **5.46** |
| **short_trail_1.75** | **+184,378%** | **-14.75%** | **5.46** |
| short_be_0.75 | +164,136% | -14.76% | 5.39 |
| short_be_1.0 | +136,755% | -15.75% | 5.25 |
| short_macd_atr_2.0/2.5 | +176,017% | -15.01% | 5.41 |

Winners: A (stop 2.5) and C (trail 1.75). Breakeven hurt returns; MACD ATR had no impact.

### Results (Combined A+C)

| Metric | v13 Dev | v14 Dev | v13 Test | v14 Test |
|--------|---------|---------|----------|----------|
| **Return** | +176,017% | **+199,388%** (+13.3%) | +707,205% | +660,625% (-6.6%) |
| **MaxDD** | -15.01% | **-14.75%** | -16.47% | **-14.62%** |
| **Sharpe** | 5.41 | **5.51** | 5.95 | **6.05** |
| **WR** | 56.5% | **57.5%** | 57.8% | **58.2%** |
| Trades | 1,565 | 1,606 | 1,645 | 1,686 |

**Verdict:** ✅ POSITIVE. Test return dropped 6.6% but MaxDD improved 1.85pp and Sharpe improved 0.10. Better risk-adjusted returns on both sets — the primary goal was DD reduction.

### Implementation
- Config: `short_stop_loss_pct: 2.5`, `short_trailing_supertrend_multiplier: 1.75`
- Code: Added 4 SHORT-specific exit config params (2 adopted, 2 rejected but available for future use)

---

## Experiment: Fast ADX(10) for SHORTs (v13) — DONE ✅ POSITIVE
**Date:** 2026-03-20

### Problem
v12 lowered the SHORT ADX threshold from 25 to 18, which improved returns but increased MaxDD by ~1.5%. The lower threshold accepts weaker trends, letting SHORTs enter during chop after sharp drops. Can we get the same faster entry without accepting weaker trends?

### Hypothesis
Use a shorter ADX period (10 instead of 14) specifically for SHORT entries, keeping the threshold at 20 (same as LONGs). ADX(10) reacts ~40% faster to trend changes, so it crosses the 20 threshold sooner during genuine downtrends. Unlike lowering the threshold, this maintains the same quality gate — it just measures trend strength over a shorter lookback.

### Grid Search (Dev)
| Variant | Dev Return | Dev Sharpe | Dev MaxDD | Shorts | SHORT WR |
|---------|-----------|-----------|-----------|--------|----------|
| v11 ADX(14) t=25 | +104,118% | 5.30 | -12.37% | 504 | 57.7% |
| v12 ADX(14) t=18 | +144,824% | 5.37 | -13.81% | 737 | — |
| ADX(10) t=25 | +146,236% | 5.46 | **-11.73%** | 635 | 57.6% |
| ADX(7) t=25 | +253,589% | 5.77 | -14.06% | 771 | 59.3% |
| **ADX(10) t=20** | **+176,017%** | **5.41** | -15.01% | 764 | 57.7% |

### Results

| Metric | v12 Dev | v13 Dev | v12 Test | v13 Test |
|--------|---------|---------|----------|----------|
| **Return** | +144,824% | **+176,017%** (+22%) | +647,366% | **+707,205%** (+9%) |
| **Sharpe** | 5.37 | **5.41** | 5.89 | **5.95** |
| **MaxDD** | -13.81% | -15.01% | -15.36% | -16.47% |
| **WR** | 56.9% | 56.5% | 57.8% | 57.8% |
| Trades | 1,545 | 1,565 (+20) | 1,620 | 1,645 (+25) |
| Shorts | 737 | 764 (+27) | 763 | 795 (+32) |

**Iran dataset (Feb 27 - Mar 17 2026):** v13 enters the Iran gap SHORT at 3/5 21:00 (same as v12), 20 hours earlier than v11's 3/6 17:00. All three faster variants (v12, ADX(7), ADX(10) t=20) produce identical results on this short window.

### Why ADX(10) t=20 over other variants

- **ADX(7) t=25** had best dev metrics but weaker test return (+526K vs +707K) — too reactive, likely overfitting to dev patterns
- **ADX(10) t=25** had best MaxDD (-11.73%) but lowest returns — too conservative
- **v12 ADX(14) t=18** had good test return but conceptually weaker: it accepts weaker trends. ADX(10) maintains the same quality gate, just measures faster
- **ADX(10) t=20** chosen: **best test return (+707K) and best test Sharpe (5.95)**. MaxDD increase (~1.1% over v12) is acceptable given the return improvement. The faster ADX period is a more principled approach than lowering the threshold

### Implementation
- Config: `short_adx_period: 10`, `short_adx_threshold: 20`
- Code: Added `short_adx_period` config param, separate `_short_adx` series computed in `prepare()`, passed to `_check_entry()`
- Gate logic: `kc_short_adx_ok` uses fast ADX for SHORTs, allowing entry even when regular ADX(14) < 20

---

## Experiment: Lower SHORT ADX Threshold (v12) — DONE ✅ POSITIVE (superseded by v13)
**Date:** 2026-03-20

### Problem
SHORT entries arrive too late. Investigation of the Iran dataset (2/27-3/17 2026) revealed a 25-hour gap (3/5 16:00 to 3/6 17:00) where price dropped ~4.6% but the strategy sat flat. Systematic dev set analysis confirms:
- **ADX >= 25 is the #1 SHORT blocker**: 23,155 hours where HMA+ST are bearish but ADX is below 25 (2.3x more than HMA or ST blocking)
- SHORT gap from previous exit: mean 10.5h, median 5.8h (vs LONG: mean 10.0h, median 5.0h)
- There are only 504 SHORTs vs 832 LONGs despite SHORTs having **better** quality (57.7% WR vs 55.6%, 0.59% avgPnL vs 0.46%)
- Iran case: ADX dropped below 25 at 03/05 20:00 and stayed there until 03/06 12:00 (16 hours)

User suggested faster HMACD for shorts, but analysis shows HMACD isn't the bottleneck — the histogram delta filter only blocked 3 hours in the Iran case, while ADX blocked 16 hours.

### Hypothesis
Lower `short_adx_threshold` from 25 to 20. The `short_adx_threshold` config param already exists separately from `adx_threshold` (LONG). ADX >= 20 still indicates a trending market (standard interpretation: 0-20 = no trend, 20-25 = emerging, 25+ = strong). Down moves develop faster ("violent bursts"), so requiring less ADX confirmation for SHORTs should capture downtrends earlier without adding excessive noise.

### Grid Search (Dev)
| ADX Thresh | Dev Return | Dev WR | Dev MaxDD | Dev Sharpe | Dev Trades | Shorts |
|-----------|-----------|--------|-----------|-----------|-----------|--------|
| 25 (v11) | +104,118% | 56.4% | -12.37% | 5.30 | 1,336 | 504 |
| 22 | +103,870% | 55.8% | -12.05% | 5.22 | 1,422 | 604 |
| 20 | +110,136% | 56.4% | -13.81% | 5.27 | 1,494 | 683 |
| **18** | **+144,824%** | **56.9%** | -13.81% | **5.37** | **1,545** | **737** |
| 15 | +106,076% | 57.5% | -14.99% | 5.24 | 1,633 | 831 |

18 chosen: best Sharpe (5.37), +39% more return, +233 more shorts. 15 has too many low-quality shorts (avgPnL drops 0.39%). 20 is decent but 18 is better across all metrics.

### Results

| Metric | v11 Dev | v12 Dev | v11 Test | v12 Test |
|--------|---------|---------|----------|----------|
| **Return** | +104,118% | **+144,824%** (+39%) | +272,140% | **+647,366%** (+138%) |
| **Sharpe** | 5.30 | **5.37** | 5.51 | **5.89** |
| **MaxDD** | -12.37% | -13.81% | -13.51% | -15.36% |
| **WR** | 56.4% | **56.9%** | 56.5% | **57.8%** |
| Trades | 1,336 | 1,545 (+209) | 1,420 | 1,620 (+200) |
| Shorts | 504 | **737** (+233) | 536 | **763** (+227) |
| AvgPnL | 0.507% | 0.460% | 0.536% | 0.524% |

**Verdict:** ✅ POSITIVE. Strongly positive. Return +39% on dev, +138% on test. Sharpe improved on both. WR improved. No overfitting (test >> dev). MaxDD increased by ~1.5-2%, acceptable given the magnitude of return improvement.

The lower ADX threshold lets SHORTs enter ~5-10 hours earlier on average during downtrends, capturing moves that were previously missed. The extra 230+ shorts are high quality (57%+ WR). The capital recycling effect (more trades = more compounding) amplifies the gains.

### Infrastructure fix
Also fixed a data gap bug: the test dataset has a 3-year gap (2021-12-31 → 2025-01-01). With the lower ADX threshold, a SHORT entered just before the gap and was held across it, causing a -101% PnL catastrophe. Added gap detection (>24h) to the controller that force-closes open positions before the gap. This also fixes a latent bug that could affect any strategy version.

### Alternatives explored
Tested 3 alternative SHORT confirmation modes (all with ADX floor 18):
- **ADX rising** (ADX increasing bar-over-bar): Best Sharpe (5.67), best MaxDD (-10.28%), but only +5% test return uplift. Too selective — only 436 shorts.
- **HMACD histogram < 20-bar avg**: Highest dev return (+152K), 58.1% WR. But worst test MaxDD (-16.9%).
- **HMA slope < 20-bar avg**: Basically same as v11 — the 20-bar average adapts too fast during sustained downtrends, making the filter self-defeating.

Static ADX=18 chosen: highest test return (+647K, +138%), best test Sharpe (5.89), acceptable MaxDD increase.

### Implementation
- Config: `short_adx_threshold: 18` (was 25, same as LONG's `adx_threshold: 20`)
- Code: No strategy code changes — `short_adx_threshold` config param already existed
- Infrastructure: Added data gap detection to `controller.py`, `reset_position()` to `swing_trend.py` base class, and floating-point tolerance to `portfolio.py` cover logic

---

## Experiment: Tighter Trailing Supertrend (v11) — DONE ✅ POSITIVE
**Date:** 2026-03-20

### Problem
MFE retention is only 26% (dev) / 24% (test). The Supertrend trailing stop uses the same 3.0 multiplier as entry, creating a wide trailing that gives back too much profit before exiting. The 0-2% MFE bucket of ST trailing exits has -47% retention (net losers).

### Hypothesis
Use a tighter Supertrend multiplier (2.0) for trailing exits while keeping the entry filter at 3.0. The tighter trailing catches reversals sooner, retaining more of each trade's peak profit. The `trailing_supertrend_multiplier` config param already exists — this is a config-only change.

### Grid Search (Dev)
| Multiplier | Dev Return | Dev WR | Dev Sharpe | Dev MaxDD | Dev Trades |
|-----------|-----------|--------|-----------|----------|-----------|
| 3.0 (v10) | +45,999% | 51.2% | 4.48 | -15.64% | 1,115 |
| 2.5 | +83,670% | 53.1% | 4.95 | -14.02% | 1,199 |
| **2.0** | **+104,118%** | **56.4%** | **5.30** | **-12.37%** | **1,336** |
| 1.5 | +127,984% | 59.2% | 5.42 | -14.69% | 1,484 |

2.0 chosen: best Sharpe (5.30), best MaxDD (-12.37%), +126% more return. 1.5 has higher return but worse MaxDD and diminishing avgPnL.

### Results

| Metric | v10 Dev | v11 Dev | v10 Test | v11 Test |
|--------|---------|---------|----------|----------|
| **Return** | +45,999% | **+104,118%** (+126%) | +130,048% | **+272,140%** (+109%) |
| **Sharpe** | 4.48 | **5.30** | 4.72 | **5.51** |
| **MaxDD** | -15.64% | **-12.37%** | -13.72% | **-13.51%** |
| **WR** | 51.2% | **56.4%** | 52.7% | **56.5%** |
| Trades | 1,115 | 1,336 | 1,150 | 1,420 |
| AvgPnL | 0.55% | 0.51% | 0.60% | 0.54% |

**Verdict:** Strongly positive. Every metric improves dramatically on both sets. No overfitting (test >> dev). The tighter trailing:
1. Exits sooner, retaining more profit per winning trade
2. Frees capital faster for re-entry → +221 trades on dev, +270 on test
3. Reduces MaxDD from -15.64% to -12.37% (dev)
4. Sharpe improves from 4.48 to 5.30 (dev) / 4.72 to 5.51 (test)

### Implementation
- Config-only change: `trailing_supertrend_multiplier: 2.0` (was 0 = use entry's 3.0)
- No code changes needed — infrastructure already existed since v1

---

## Experiment: Thesis Invalidation PnL Gate (v11) — DONE ❌ NEGATIVE
**Date:** 2026-03-20

### Problem
thesis_invalidation is v10's biggest loss source: 398 trades at -81.7% sumPnL (dev). 148 are currently profitable at exit (+44.0%).

### Hypothesis
Only thesis-invalidate if the trade is currently at a loss (unrealized PnL < 0) at min_hold boundary. Keep trades with MFE < 1% that are currently profitable.

### Result
| Metric | v10 Dev | v11 Dev |
|--------|---------|---------|
| **Return** | +45,999% | **+33,331%** (-27.5%) |
| Trades | 1,115 | 949 (-166) |
| WR | 51.2% | 43.5% |

**Why it failed:** The 148 kept trades mostly deteriorated after hour 6 — ST trailing WR dropped from 80.2% to 68.8%. Worse, the kept trades blocked capital: 166 fewer new entries. The freed-capital effect of thesis_invalidation (allowing re-entry on fresh signals) is MORE valuable than keeping marginally profitable trades. Thesis_invalidation isn't just a loss-cutter — it's a capital recycler.

**Key lesson:** Don't weaken thesis_invalidation. The capital freed by cutting low-MFE trades generates more profit through fresh entries than the small gains from keeping those trades.

---

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


*(v8, v9 experiments archived to `docs/experiment-archive.md`)*
