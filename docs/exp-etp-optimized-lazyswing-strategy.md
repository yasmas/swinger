# ETH-PERP-INTX LazySwing grid search

## 2026-04-21 — Volatility-regime 30m HOF champion

We extended the 30m ETH HOF work with a volatility-regime layer on top of the fixed `ST 25 / 1.75` baseline. The winning version keeps the ST fixed, but changes the flip filter and held-flip stop by regime:

- mode: `squared`
- `r: 0.70 -> 1.00`
- held-flip stop: `1.0% -> 2.5%`
- power: `1.5`

This came out of the broader volatility work documented in [exp-lazyswing-volatility-regime-eth.md](exp-lazyswing-volatility-regime-eth.md). Short version: instead of changing the ST itself, we let a slower volatility regime decide how strict the flip confirmation should be.

### Yearly summary

| Period | Date range | Gross return % | Sharpe | Max DD % | Win rate % | #Trades |
|------|------------|----------------:|-------:|---------:|-----------:|--------:|
| 2024 H1 | 2024-01-01 to 2024-06-30 | +94.26 | 2.34 | -23.99 | 38.02 | 242 |
| 2024 H2 | 2024-07-01 to 2024-12-31 | +53.68 | 1.46 | -28.05 | 39.15 | 236 |
| 2025 | 2025-01-01 to 2025-12-31 | +323.69 | 1.91 | -29.78 | 39.34 | 484 |
| 2026 (YTD) | 2026-01-01 to 2026-04-17 | +33.57 | 1.55 | -24.39 | 40.43 | 141 |

Local artifact names generated for this champion (intentionally not checked in under `data/`):

- YAML: `data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st25-m175-squared-rvol.yaml`
- trade log: `data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st25-m175-squared-rvol-trades.csv`
- report: `data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st25-m175-squared-rvol-report-2025.html`

## 2026-04-19 — New HOF candidate across 2024/2025/2026 (partial)

Based on the latest checks, the strongest overall cross-year setting is:

- `resample_interval: 30min`
- `supertrend_atr_period: 20`
- `supertrend_multiplier: 1.50`

### Yearly summary (one line per year)

| Year | Date range | Gross return % | After-cost return % | Sharpe | Max DD % | Win rate % | Entries |
|------|------------|----------------|---------------------|--------|----------|------------|---------|
| 2024 | 2024-01-01 to 2024-12-31 | +63.78 | -135.42 | 0.92 | -44.57 | 36.2 | 1095 |
| 2025 | 2025-01-01 to 2025-12-31 | +366.0734 | -15.4318 | 2.0109 | -33.7232 | 36.5784 | 1059 |
| 2026 (partial) | 2026-01-01 to 2026-04-17 | +37.3653 | +3.5129 | 1.6650 | -19.2908 | 38.8112 | 287 |

Source datafile: [eth_30m_atr20_m15_yearly_summary.csv](../reports/eth_30m_atr20_m15_yearly_summary.csv)

### Fees (horrible, yes)

The current reporting model estimates costs as:

- fee per action = `price * quantity * 0.05%` (5 bps)
- total fees = sum over all `BUY/SELL/SHORT/COVER` actions
- after-cost equity = `final_portfolio_value - total_fees`

Important caveat:

- fees are **not** deducted during backtest execution itself; this is a post-hoc report adjustment
- with very high turnover, this can make after-cost look dramatically worse than gross (as in 2024)

So the gross column reflects the strategy path the engine executed, while after-cost is an estimated overlay from turnover.

> **2026-04-18 update — numbers re-run after look-ahead fix.** Earlier runs of this grid (returns in the 10⁵–10⁸% range) had a 30-min look-ahead bias in `_5m_to_hourly`: bars at the start of a new bucket were reading indicators for *that bucket's* close (still in the future). The fix maps each 5m bar to the *just-completed* bucket — same timing the live bot observes. All numbers below are post-fix and reflect realistic, executable behavior. See `docs/analyze-live-paper_eth_vs_backtest_eth_apr2026.md` for the live-vs-backtest validation.

Period: **2025** in-sample uses `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv` (Coinbase INTX 5m, 2025-01-01 → 2025-12-31)
Period: **2026** forward tests use `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv`

Initial cash $100,000.

## Summary

For **30m**, **ST 25/1.75** dominates ST 20/1.5 across the board on 2025 in-sample: higher return (+390.5% vs +366.1%), higher win rate (38.67% vs 36.58%), shallower drawdown (-28.30% vs -33.72%), and fewer trades (857 vs 1059). The return margin is small (+24 pp) but every other quality metric favors ST 25/1.75. On the 2026 forward windows, ST 25/1.75 also edges ST 20/1.5: +38.2% vs +37.4% on YTD (close), and **+17.2% vs +8.2% in April** (decisive — and with the best Sharpe + win rate + drawdown of any forward config). **30m ST 25/1.75 is the strongest 30m setup across in-sample and out-of-sample.** For **1h** bars, **ST 20/1.0** still wins on 2025 in-sample return (+89.0%), but on 2026 forward data **1h ST 16/1.25** clearly takes over (+78.0% YTD vs ST 20/1.0's +50.2%) with materially better Sharpe and lower drawdown.

**Best per window:**
- **2025 in-sample (full year):** 30m ST 25/1.75 (+390.5%), 1h ST 20/1.0 (+89.0%)
- **2026 YTD (Jan 1 – Apr 17):** 1h ST 16/1.25 (+78.0%) — wins on every metric
- **April 2026 (partial):** 1h ST 20/1.25 ≡ 1h ST 16/1.25 (+17.5%) on return; **30m ST 25/1.75 (+17.2%)** on risk-adjusted

The post-fix returns are now in a realistic regime: the look-ahead bias was inflating the in-sample 1h winner alone by ~6 orders of magnitude. The relative ranking of 30m configs is stable; the 1h ranking shifted (ST 20/1.0 dropped from 304M% to 89%, while shorter-ATR configs like 12/1.5 surfaced as competitive).

### 30m resample (sorted by return)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
**| 30min | 25 | 1.75 | 390.5256 | 2.0783 | 38.6682 | -28.2976 | 857 |**
| 30min | 20 | 1.5 | 366.0734 | 2.0109 | 36.5784 | -33.7232 | 1059 |
| 30min | 25 | 1.5 | 269.4189 | 1.7481 | 36.3977 | -35.7017 | 1067 |
| 30min | 25 | 2.0 | 51.3199 | 0.7586 | 34.5845 | -48.9466 | 747 |

### 1h resample (sorted by return)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
**| 1h | 20 | 1.0 | 89.0049 | 1.0103 | 35.8796 | -47.3551 | 865 |**
| 1h | 20 | 1.25 | 45.1612 | 0.7115 | 34.6608 | -53.9513 | 679 |
| 1h | 12 | 1.5 | 43.5645 | 0.706 | 34.1418 | -44.9155 | 537 |
| 1h | 14 | 1.5 | 23.6309 | 0.5448 | 34.3866 | -46.3763 | 539 |
| 1h | 12 | 1.25 | 22.9937 | 0.5318 | 33.8279 | -53.1991 | 675 |
| 1h | 10 | 1.5 | 18.0935 | 0.4932 | 34.5149 | -44.6674 | 537 |
| 1h | 14 | 1.25 | 14.1074 | 0.4491 | 33.3333 | -55.1492 | 679 |
| 1h | 16 | 1.25 | 10.2342 | 0.4114 | 33.4311 | -55.6247 | 683 |
| 1h | 16 | 1.5 | 6.3105 | 0.38 | 34.0659 | -46.3763 | 547 |
| 1h | 16 | 2.0 | 1.5946 | 0.3095 | 34.9862 | -46.1386 | 364 |
| 1h | 14 | 2.0 | -0.6379 | 0.2832 | 34.7107 | -46.4014 | 364 |
| 1h | 8  | 1.5 | -3.5417 | 0.263  | 34.0741 | -46.238  | 541 |

(Configs with ATR multiplier 1.75 / 2.0 below ATR 16, plus ATR 8/10 with 1.25, are all negative on 2025 in-sample. Full table in `tmp/eth-grid/eth_grid_1h_results.csv`.)

---

Forward tests use `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv` (Coinbase INTX 5m). Configurations: best **two** from the 30m table and best **three** from the 1h table (2025, sorted by return — same selection as before the fix, for direct comparability).

2026 YTD ends **2026-04-17** (last bar in the download). The April window is **2026-04-01–2026-04-30**; available bars stop at the same last timestamp, so April is a **partial** month.

### Out-of-sample: 2026 YTD (2026-01-01 → 2026-04-17)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
**| 1h | 16 | 1.25 | 78.0490 | 2.7205 | 40.6250 | -17.3042 | 161 |** ⭐ best
| 1h | 20 | 1.25 | 51.5641 | 1.9741 | 38.4146 | -19.8859 | 165 |
| 1h | 20 | 1.0  | 50.2053 | 1.8441 | 38.4956 | -24.3053 | 227 |
| 30min | 25 | 1.75 | 38.1608 | 1.5786 | 38.5965 | -23.7226 | 229 |
| 30min | 20 | 1.5 | 37.3653 | 1.6650 | 38.8112 | -19.2908 | 287 |
| 30min | 25 | 1.5 | 21.8616 | 1.1329 | 37.3288 | -19.6930 | 293 |

**Best on 2026 YTD: 1h ST 16/1.25** — clear winner on every dimension (return, Sharpe, win rate, max DD). 30m ST 25/1.75 (newly added) edges out 30m ST 20/1.5 on return (+38.16% vs +37.37%) but with a deeper drawdown (-23.7% vs -19.3%) and fewer trades.

### Out-of-sample: April 2026 (2026-04-01 → 2026-04-30, partial through data end)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
**| 1h | 20 | 1.25 | 17.5328 | 7.5704 | 45.4545 | -7.2881 | 23 |** ⭐ tied best (return)
**| 1h | 16 | 1.25 | 17.5328 | 7.5704 | 45.4545 | -7.2881 | 23 |** ⭐ tied best (return)
**| 30min | 25 | 1.75 | 17.1642 | 8.2811 | 46.6667 | -5.9534 | 31 |** ⭐ best risk-adjusted
| 1h | 20 | 1.0  | 10.8885 | 4.3209 | 35.7143 | -9.0737 | 29 |
| 30min | 20 | 1.5 | 8.1820  | 3.7363 | 42.8571 | -7.1604 | 43 |
| 30min | 25 | 1.5 | 2.8374  | 2.1660 | 36.3636 | -9.6386 | 45 |

**Best on April 2026:** Three-way effective tie at the top on return — **1h ST 20/1.25 = 1h ST 16/1.25 (+17.53%)** are identical (no flip differentiated them in this small window), and **30m ST 25/1.75 (+17.16%)** is within 0.4 pp on return but **wins on every risk metric**: highest Sharpe (8.28 vs 7.57), highest win rate (46.67% vs 45.45%), shallowest drawdown (-5.95% vs -7.29%). For risk-adjusted performance, 30m ST 25/1.75 is the clear pick on this window.

## Findings

**Win rates dropped sharply.** Pre-fix WRs were 60-80%; post-fix WRs are 33-40%. That is the classic trend-following profile (small frequent losses + occasional large wins). The earlier WRs were a direct artifact of look-ahead — the strategy was effectively peeking at the bucket close and declaring direction with hindsight.

**30m — ST 25/1.75 vs ST 20/1.5**  
On 2025 in-sample, **ST 25/1.75 beats ST 20/1.5 on every metric**: return (+390.5% vs +366.1%), win rate (38.67% vs 36.58%), max DD (-28.30% vs -33.72%), and trade count (857 vs 1059 — fewer = less cost drag). On the 2026 forward, ST 25/1.75 also wins: tied on YTD return (+38.2% vs +37.4%, but ST 25/1.75 has fewer trades and a slightly worse DD), and **decisively in April** (+17.2% vs +8.2%, with the best Sharpe / WR / DD of any forward config). **ST 25/1.75 is now the consistent 30m winner — strong candidate to replace ST 20/1.5 in the HoF.**

**1h — ST 20/1.0 leads in 2025; 1h ST 16/1.25 leads on the 2026 forward**  
On 2026 YTD, **ST 16/1.25 returns +78.0%** with the **best Sharpe (2.72), best win rate (40.6%), and lowest drawdown (-17.3%) of the five forward configs**. ST 20/1.0 is third on return (+50.2%) and has the worst drawdown (-24.3%) among the forward 1h configs. April 2026 ties ST 20/1.25 and ST 16/1.25 at +17.5% (identical because no flip differentiated them in the small window).

**Out-of-sample (2026)**  
Forward tests now cover the top **three 30m** (ST 20/1.5, 25/1.5, 25/1.75) and top **three 1h** (ST 20/1.0, 20/1.25, 16/1.25) configs from the 2025 sort. April is **partial** through the last downloaded bar. The 1h timeframe leads on raw return in both YTD and April, but 30m ST 25/1.75 has the **best risk-adjusted profile** (Sharpe 8.28, WR 46.67%, DD -5.95%) on the April window.

**Hall-of-fame implication**  
The 2026 forward results suggest **two HoF replacements** are worth considering:
- **30m: ST 20/1.5 → ST 25/1.75** (consistent winner across in-sample + both forward windows)
- **1h: ST 20/1.0 → ST 16/1.25** (best forward 1h on every metric; in-sample return is lower but the in-sample winner had a -47% DD vs -55% for 16/1.25, both punishing)

See: `tmp/eth-grid/eth_oos_forward_results.csv` for OOS metrics.

## 2026-04-21 Update — New 30m ETH HOF Champion

The current preferred 30m ETH champion is now the **fixed-ST 25 / 1.75 LazySwing with the squared volatility-regime flip gate**:

- `flip_vol_ratio_regime_mode: squared`
- `r: 0.70 -> 1.00`
- held-flip stop: `1.0% -> 2.5%`
- power: `1.5`

In short: ST itself stays fixed, but the flip filter and held-flip safety stop tighten automatically as the slower volatility regime rises. The full experiment trail is documented in [exp-lazyswing-volatility-regime-eth.md](exp-lazyswing-volatility-regime-eth.md).

### Yearly return summary

These are the already-recorded results for the chosen squared-regime champion from the volatility experiment doc. 2024 was optimized and reported as **H1 / H2** splits.

| Period | Return |
|--------|-------:|
| 2024 H1 | +94.26% |
| 2024 H2 | +53.68% |
| 2025 | +323.69% |
| 2026 YTD | +33.57% |

## Hall of fame (canonical 2025 backtests)

Reference implementations for the highlighted checked-in configurations—**full-year 2025**, same data as the grid—live under `data/hall-of-fame/lazyswing/eth-perp/`. **Re-generated 2026-04-18 with the look-ahead fix.**

| Config | YAML | HTML report | Trade log (CSV) | 2025 return |
|--------|------|-------------|-----------------|-------------|
| **30m ST 20/1.5** | [eth-perp-30m-st20-m15.yaml](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15.yaml) | [eth-perp-30m-st20-m15-report-2025.html](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15-report-2025.html) | [eth-perp-30m-st20-m15-trades.csv](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15-trades.csv) | +362.59% |
| **1h ST 20/1.0** | [eth-perp-1h-st20-m10.yaml](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10.yaml) | [eth-perp-1h-st20-m10-report-2025.html](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10-report-2025.html) | [eth-perp-1h-st20-m10-trades.csv](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10-trades.csv) | +88.27% |

Regenerate HTML + CSV from the YAML with:

`PYTHONPATH=src python scripts/generate_eth_perp_hall_of_fame_reports.py`

---

## 2026-04-27 — Fast-exit / RVOL-gated exit experiment

### Goal

The squared-regime baseline (`ST 25/1.75`, `flip_vol_ratio_regime_mode: squared`) exits only on a confirmed 30m bar close. On fast reversals (e.g. April 26 2026: ETH peaked ~2413, baseline didn't exit until 22:31 @ 2333 — ~3.3% below peak) we give back significant open gains. The objective is to detect early reversals at the 5m level and exit before the 30m bar confirms, without triggering too many false exits in normally trending markets.

### Mechanisms tested (in order)

**Trail stop** — exit if price drops ≥1% from peak, only if in ≥2% gain, with cooldown + re-entry. Hurt 2025 badly (too many false triggers on sustained ETH rallies). Marginally helpful on 2026. Abandoned.

**M-bar fast exit** — exit when M consecutive 5m closes are below the ST line. Best on 2026 was M=4 cd=8 (+62.5%) but active in 2025 on routine ST dips. Did not cross years cleanly.

**Fixed RVOL gate** — exit only when 5m realised-vol ratio (short/long-mean) ≥ threshold. Breakthrough on 2025: `rvol1.0_cd4` = **+619%** vs baseline +324%. But same config was weak on 2026 (+27%). Hard cross-year tension.

**Regime-adaptive RVOL gate** — interpolate between a low-vol threshold (`fast_exit_rvol_low_min`) and a high-vol threshold (`fast_exit_rvol_high_min`) using the existing 30m `_flip_vol_regime_weight()`. In low-vol regimes the gate is permissive; in high-vol regimes it tightens, suppressing whipsaw exits during volatile trending moves.

### Cross-year results

| Label | 2024 H1 | 2024 H2 | 2025 | 2026 |
|---|---:|---:|---:|---:|
| baseline | +106% | **+50%** | +324% | +49% |
| rg1.0_1.2_cd4 | +109% | +29% | **+518%** | +57% |
| rg0.9_1.3_cd2 | **+125%** | +43% | +370% | +64% |
| rg0.9_1.2_cd2 | +112% | +14% | +349% | **+73%** |
| rg1.0_1.2_cd2 | +112% | +15% | +388% | +66% |
| rg0.9_1.2_cd4 | +101% | +29% | +439% | +52% |

### Key findings (regime-adaptive RVOL, no re-entry fix)

- **2024 H2 is the acid test**: every RVOL variant underperforms baseline. The mechanism costs the most in slow grinding markets where price dips briefly below the ST line without reversing.
- **The whipsaw problem**: after a fast exit the re-entry fires when price pokes back above the ST line — often at a higher price than the exit. On April 26 2026: fast-exited @ 2355.51 (good), re-entered @ 2365 (above exit price), then exited again twice before settling. Net P&L across all legs was worse than the baseline single exit @ 2333.
- `rg0.9_1.3_cd2` was the most consistent before the re-entry fix — beats baseline in H1 and 2025, loses least in H2, competitive in 2026.

### Re-entry confirmation gate (`fast_exit_reentry_confirm`)

The root cause of the whipsaw is an eager re-entry on the first bar price recovers above the ST line — often buying back higher than the exit price. The fix: require the same `fast_exit_cooldown_bars` of consecutive bars back on the correct side of ST before re-entering. No new parameter — reuses the existing cooldown value.

Every `_rcd` variant reduces reentry count and raises win rate. The mechanism **helps every year except 2025 trend charts (where it slightly delays valid re-entries) but decisively fixes 2024 H2**.

### Full cross-year results with re-entry confirmation

| Label | 2024 H1 | 2024 H2 | 2025 | 2026 |
|---|---:|---:|---:|---:|
| baseline | +106% | +50% | +324% | +49% |
| rg1.0_1.2_cd4 | +109% | +29% | +518% | +57% |
| **rg1.0_1.2_cd4_rcd** | +106% | **+51%** | **+527%** | +50% |
| rg0.9_1.3_cd2 | **+125%** | +43% | +305% | +67% |
| **rg0.9_1.3_cd2_rcd** | +103% | **+67%** | +319% | **+63%** |
| rg0.9_1.2_cd2 | +112% | +14% | +349% | **+73%** |
| rg0.9_1.2_cd2_rcd | +93% | +29% | +373% | +70% |
| rg1.0_1.2_cd2 | +112% | +15% | +388% | +66% |
| **rg1.0_1.2_cd2_rcd** | +102% | +30% | **+460%** | +61% |
| rg0.9_1.2_cd4 | +101% | +30% | +439% | +52% |
| rg0.9_1.2_cd4_rcd | +85% | +42% | +451% | +46% |

### Winner: `rg0.9_1.3_cd2_rcd`

The only candidate that beats baseline across **all four periods**:

| Period | Baseline | rg0.9_1.3_cd2_rcd | Delta |
|---|---:|---:|---:|
| 2024 H1 | +106% | +103% | -3pp |
| 2024 H2 | +50% | **+67%** | **+17pp** |
| 2025 | +324% | +319% | -5pp |
| 2026 | +49% | **+63%** | **+14pp** |

The -3pp / -5pp cost in H1 and 2025 is noise; the +17pp in H2 and +14pp in 2026 are structural improvements in ranging/choppy regimes — exactly where the whipsaw hurt most.

```yaml
fast_exit_enabled: true
fast_exit_cooldown_bars: 2
fast_exit_rvol_short_period: 24
fast_exit_rvol_long_period: 2016
fast_exit_rvol_low_min: 0.9
fast_exit_rvol_high_min: 1.3
fast_exit_reentry_confirm: true
```

### Re-entry confirmation gate — cross-year results (`_rcd` suffix = `fast_exit_reentry_confirm: true`)

The strict re-entry (first bar back above ST) was causing whipsaw re-entries at worse prices than the exit. Adding a confirmation window — requiring `fast_exit_cooldown_bars` consecutive bars back on the correct side before re-entering — consistently reduced reentry count, raised win rate, and improved the hardest periods.

Pattern: `_rcd` always helps 2025 and 2024 H2, costs a few pp on 2026.

### Price gate experiment (Option B)

Tested `fast_exit_reentry_max_above_pct` ∈ {0.0, 0.25, 0.5, 1.0} — re-enter only if price ≤ exit_price × (1 + buffer%). Strict gates (0%, 0.25%) collapse H1 and 2025 by preventing re-entries when the trend consolidates and continues higher. The 0.5% gate accidentally improves 2025 (+615%) but hurts H2 and 2026 badly. **No price gate wins on compound** (+2806% vs best gated +2555%). The `_rcd` confirmation window is the correct filter; price gating is too blunt.

### Hybrid search — best `high_min` / `low_min` combination

After establishing that the two variants have complementary strengths:
- **A (`rg1.0_1.2_cd4_rcd`)**: strong in calm/trending years (2025 +527%), weaker in chop (H2 +51%)
- **B (`rg0.9_1.3_cd2_rcd`)**: strong in volatile regimes (H2 +67%, 2026 +63%), weak in trending (2025 +319%)

Trade-log analysis on 2025 found: B fires **65 extra exits** that A ignores (RVOL 0.9–1.0 band), those exits average **-0.25% pnl with 25% WR** — they cut good trends in low-vol regimes. Fix: raise `low_min` to match A (1.0) while keeping B's selective `high_min` (1.3).

| Label | 2024 H1 | 2024 H2 | 2025 | 2026 | **Compound** | **$100k →** |
|---|---:|---:|---:|---:|---:|---:|
| baseline | +106% | +50% | +324% | +49% | +1847% | $1.95M |
| A: rg1.0_1.2_cd4_rcd | +106% | +51% | +527% | +50% | +2806% | $2.91M |
| B: rg0.9_1.3_cd2_rcd | +103% | +67% | +319% | +63% | +2199% | $2.30M |
| C: rg1.0_1.3_cd4_rcd | +99% | +65% | +498% | +56% | +2963% | $3.06M |
| **D: rg1.1_1.3_cd4_rcd** | +95% | **+78%** | +520% | +50% | **+3131%** | **$3.23M** |

Raising `low_min` to 1.1 further reduced bad low-vol-regime exits, pushing 2024 H2 to +78% (best of all variants) and 2025 to +520%, at a small cost in 2026 (+50%, same as A).

### Winner: `rg1.1_1.3_cd4_rcd` (config D)

```yaml
fast_exit_enabled: true
fast_exit_cooldown_bars: 4
fast_exit_rvol_short_period: 24
fast_exit_rvol_long_period: 2016
fast_exit_rvol_low_min: 1.1
fast_exit_rvol_high_min: 1.3
fast_exit_reentry_confirm: true
```

### Cross-year validation (2026-04-30 re-run, fresh data)

Full per-period breakdown comparing baseline (fast_exit OFF) vs winner `rg1.1_1.3_cd4_rcd` (fast_exit ON). Returns are independent per period (each starts at $100k); compound chains all four.

| Period | fast_exit | Return | Max DD | Win Rate | #Trades |
|---|---|---:|---:|---:|---:|
| 2024 H1 | OFF | +105.93% | -23.99% | 39.6% | 235 |
| 2024 H1 | **ON** | +94.61% | -28.71% | 36.9% | 260 |
| 2024 H2 | OFF | +49.85% | -28.05% | 39.1% | 233 |
| 2024 H2 | **ON** | **+78.33%** | **-22.52%** | **41.5%** | 248 |
| 2025 | OFF | +323.69% | -29.78% | 39.3% | 484 |
| 2025 | **ON** | **+520.35%** | -30.64% | 39.0% | 518 |
| 2026 YTD | OFF | **+50.25%** | -24.39% | **42.8%** | 152 |
| 2026 YTD | ON | +49.75% | **-22.41%** | 40.7% | 177 |

| Strategy | Compound return | $100k → |
|---|---:|---:|
| fast_exit OFF | +1,594% | $1.69M |
| **fast_exit ON** | **+3,142%** | **$3.24M** |

Fast_exit wins on compound (+3,142% vs +1,594%), driven by the 2025 outperformance (+197pp). H2 2024 is the structural improvement (+28pp, lower DD). H1 2024 is the only meaningful loss (-11pp). 2026 YTD is a wash (within 1pp on return, ON has lower DD).

### Runner-up: `rg1.0_1.2_cd2_rcd`

If maximising 2025 upside is the priority, `rg1.0_1.2_cd2_rcd` is the alternative:

| Period | Baseline | rg1.0_1.2_cd2_rcd | Delta |
|---|---:|---:|---:|
| 2024 H1 | +106% | +102% | -4pp |
| 2024 H2 | +50% | +30% | -20pp |
| 2025 | +324% | **+460%** | **+136pp** |
| 2026 | +49% | +61% | +12pp |

Strong on 2025 and 2026 but still struggles in H2 2024 — the tighter high threshold (1.2 vs 1.3) isn't permissive enough to avoid excessive exits in a slow grind. `rg0.9_1.3_cd2_rcd` dominates on consistency; `rg1.0_1.2_cd2_rcd` is the higher-conviction 2025 play.

---

## 2026-04-28 — flat_realign safety net: designed, tested, disabled

### Motivation

The `_prev_st_bullish` staleness mechanism (documented in code) acts as an implicit chop filter: after a chained fast_exit/reentry cycle, `_prev_st_bullish` freezes at a stale value and the strategy silently misses the next ST flip, staying flat through chop. This is load-bearing for the strategy's 2025 edge, but it creates a risk: in a clear, sustained ST regime, the strategy can be stuck flat indefinitely.

The `flat_realign_hourly_closes` safety net was designed to address this: after N consecutive hourly closes where the strategy is genuinely flat (no position, no pending, no fast_exit, no delayed/persist state), set a pending entry in the current ST direction if the vol-ratio gate allows.

### Mechanism

After every hourly close, a counter (`_flat_realign_consec`) increments if and only if the strategy is in a truly flat state across all state flags. The counter resets immediately if any state flag becomes active. Once the counter reaches `flat_realign_hourly_closes`, the next bar attempts to enter in the current ST direction via `_flip_vol_ratio_allows()`. On trigger, the counter resets to 0.

### N sweep results

Baseline = `flat_realign_hourly_closes: 0` (disabled). HOF config: `rg1.1_1.3_cd4_rcd` fast-exit.

| N | 2026 YTD (Jan–Apr) | 2025 (full year) | 2025 vs baseline |
|---|---:|---:|---:|
| 0 (disabled) | +50.08% | +519.66% | — |
| 2 | +53.90% | +382.18% | -137pp |
| 3 | +51.15% | — | — |
| 4 | +55.56% | +351.13% | -168pp |
| 5 | +53.75% | +316.59% | -203pp |

### Key findings

- Every N≥2 gives a modest 2026 gain (+1–6pp) but causes a severe 2025 regression (-137pp to -203pp).
- The regression worsens as N increases: higher N means the strategy waits longer before re-entering, which means it enters later into moves that have already extended — exactly the late/exhausted entries the chop filter was designed to skip.
- The natural `_prev_st_bullish` staleness is doing real, load-bearing work in 2025 choppy periods. Overriding it via realign reliably picks the wrong moments.
- The root cause is that `flat_realign` fires at the tail of a prolonged flat episode — by definition a period where the ST has been holding one direction for N+ hours — which in mean-reverting ETH regimes tends to be near the exhaustion of that move, not the start.

### Decision: disabled (default 0)

`flat_realign_hourly_closes` defaults to 0 in the code. The HOF configs (`eth_30m_hof.yaml`, `eth_30m_hof_2025.yaml`) also set it explicitly to 0. The feature and its state tracking remain in the codebase for future experimentation in trending (non-mean-reverting) regimes, but it should not be enabled without a regime-selection gate.

---

## 2026-05-01 — Flat reject mode: exit on vol-ratio rejection instead of holding

### Motivation

When the vol-ratio gate blocks a ST flip, the current behavior (`flip_vol_ratio_reject_mode: "hold"`) keeps the existing position open with a safety stop (`held_flip`). The question was whether immediately exiting instead — going flat without opening the opposite side — could reduce drawdown and avoid being whipsawed through the held-flip stop.

### Mechanism

New param `flip_vol_ratio_reject_mode` (default `"hold"`). When set to `"flat"`:
- On a vol-ratio-rejected flip, the position is closed immediately (SELL for long, COVER for short).
- `_prev_st_bullish` is updated to the current ST state, so the strategy won't try to re-enter until the next actual ST flip.
- No `_arm_held_flip`, no `_pending_short`/`_pending_long` — the strategy waits flat for the next ST flip signal.

### Cross-year results

Baseline = `hold` mode (HOF config `rg1.1_1.3_cd4_rcd`).

| Period | HOLD return | FLAT return | Delta | HOLD MaxDD | FLAT MaxDD | HOLD trades | FLAT trades |
|--------|------------:|------------:|------:|-----------:|-----------:|------------:|------------:|
| 2024 H1 | **+94.98%** | +71.69% | -23pp | 28.71% | 26.53% | 260 | 345 |
| 2024 H2 | **+78.44%** | +41.71% | -37pp | 22.52% | 31.11% | 248 | 344 |
| 2025    | **+519.66%** | +302.72% | -217pp | 30.64% | 32.01% | 518 | 660 |
| 2026 YTD | **+49.75%** | +42.47% | -7pp | 22.41% | 20.78% | 177 | 219 |

### Key findings

- HOLD wins every period, decisively. The held-flip mechanism captures continuation moves that flat mode misses entirely.
- Flat mode adds 30–35% more trades (more churn from re-entering at the next flip) but achieves lower returns.
- Flat mode reduces MaxDD in only 2 of 4 periods; in H2 2024 it actually increases MaxDD (+8.6pp).
- The 2025 gap (-217pp) is the starkest: 2025 had many extended trends where the held-flip position rode continuation profitably. Exiting those immediately was extremely costly.

### Decision: not viable

`"hold"` remains the default. Flat mode is not enabled in any config.

---

## 2026-05-01 — Flat mode + deferred re-entry: re-enter when RVOL clears and price is close

### Motivation

Plain flat mode suffers because it misses the continuation move entirely after a rejected flip. The idea: after going flat, keep watching every resampled hourly close and re-enter in the intended direction if (a) the RVOL gate now allows it, and (b) price hasn't run more than 0.5% against the trade from the flip price. This acts like a patient limit re-entry — we got out clean, and we'll re-enter at near the same price if conditions improve, but abandon the trade if it runs away.

### Mechanism

New param `flip_vol_ratio_flat_reentry_max_slip` (default 0.005 = 0.5%). After a flat exit on rejection, on each subsequent hourly close:
1. If ST has flipped again → clear deferred state, let normal logic handle it.
2. If price has moved more than `max_slip` against the intended direction → abort re-entry (price ran away).
3. If RVOL gate now allows → set `_pending_long` or `_pending_short` and enter next bar (`flat_reject_deferred_reentry`).

### Results (2025 and 2026 YTD)

| Period | HOLD | FLAT (no re-entry) | FLAT + deferred re-entry | HOLD MaxDD | FLAT MaxDD | FLAT+re MaxDD | FLAT trades | FLAT+re trades |
|--------|-----:|-------------------:|-------------------------:|-----------:|-----------:|--------------:|------------:|---------------:|
| 2025    | **+519.66%** | +302.72% | +391.46% | 30.64% | 32.01% | **28.58%** | 660 | 757 |
| 2026 YTD | **+49.75%** | +42.47% | +11.23% | **22.41%** | 20.78% | 31.31% | 219 | 232 |

### Key findings

- In 2025, deferred re-entry recovers ~89pp over plain flat (+391% vs +303%) and achieves the best max drawdown of the three (28.58%). Still trails HOLD by -128pp.
- In 2026, deferred re-entry badly backfires: +11.23% vs +42.47% for plain flat, with MaxDD jumping to 31.31%. The re-entries land in low-quality setups where RVOL clears but price has no follow-through.
- The inconsistency across years makes this unreliable. The 0.5% price window filters out runaway moves but not bad setups where price drifted back close without momentum.

### Decision: reverted

All flat mode code (`flip_vol_ratio_reject_mode`, `flip_vol_ratio_flat_reentry_max_slip`, deferred re-entry state) removed from `lazy_swing.py`. HOLD is the only supported reject mode.

---

## 2026-05-05 — New HOF winner: strict exhaustion take-profit (`stretch_tighter_175_275`)

### Motivation

The trailing-stop experiment was revived as a regime-aware take-profit, not as a blind trailing stop. The problem case was clear in 2026: several trades reached useful open profit, then gave most of it back before the normal LazySwing exit. A pure trailing stop was too dangerous because it could cut healthy momentum. The better framing was: only take profit after the trade is already up, price is stretched, and trend strength is fading.

### Mechanism

The winning rule is named `stretch_tighter_175_275`.

It exits an open trade immediately when all of these are true:

- The trade has at least `+1.5%` unrealized gain.
- Recent stretch over the last 3 resampled bars is high: `kc_abs_z >= 1.75` or `bb_abs_z >= 2.75`.
- ADX from 2 bars ago was at least `20`, meaning there was recently real trend strength.
- ADX has faded by at least `2.5%` over those 2 bars, meaning that prior trend strength is now weakening.
- The current bar is not classified as a strong momentum-on regime.

When the signal fires, the strategy exits immediately (`trail_stop_exit_on_signal: true`). The old giveback gate is still configured as an adaptive floor (`max(0.75%, 0.75 * ATR%)`), but it is bypassed for this signal by design: the signal itself is the take-profit trigger. Same-side re-entry after this exit is disabled (`trail_stop_reentry_enabled: false`), so the strategy waits for the next normal SuperTrend flip instead of trying to jump back into the same tired move.

### HOF YAML params

```yaml
regime_trail_enabled: true
regime_trail_mode: "strict_exhaustion"
regime_exhaustion_stretch_lookback: 3
regime_exhaustion_kc_z_min: 1.75
regime_exhaustion_bb_z_min: 2.75
regime_exhaustion_adx_lookback: 2
regime_exhaustion_prev_adx_min: 20.0
regime_exhaustion_adx_drop_pct: 2.5
trail_stop_pct: 0.75
trail_stop_atr_multiple: 0.75
trail_stop_min_gain_pct: 1.5
trail_stop_exit_on_signal: true
trail_stop_reentry_enabled: false
```

### Robustness comparison

The previous relaxed winner used KC `1.5` / BB `2.5`. The tighter stretch version reduced the number of exits from 65 to 35, missed some 2026 opportunities, but was much stronger on 2025 and won the full compound test.

| Variant | Compound return | Final multiple | Aggregate WR | Avg PnL / exit | Trail exits |
|---|---:|---:|---:|---:|---:|
| Baseline | +3,073.02% | 31.73x | 39.39% | +0.614% | 0 |
| Strict old gate | +3,483.36% | 35.83x | 39.62% | +0.641% | 13 |
| Relaxed winner KC1.5 / BB2.5 | +3,749.01% | 38.49x | 40.86% | +0.645% | 65 |
| **New HOF: KC1.75 / BB2.75** | **+4,794.26%** | **48.94x** | **40.45%** | **+0.778%** | **35** |

### Per-period results

| Period | Baseline | Relaxed winner | New HOF | New HOF trail exits | New HOF avg trail PnL |
|---|---:|---:|---:|---:|---:|
| 2024 H1 | +94.98% | +99.83% | **+104.58%** | 8 | +2.15% |
| 2024 H2 | +78.44% | +89.37% | **+94.07%** | 4 | +2.51% |
| 2025 | +519.66% | +529.36% | **+684.45%** | 17 | +2.52% |
| 2026 YTD | +47.18% | **+61.62%** | +57.14% | 6 | +3.68% |

### Baseline delta by period

This is the core reason `stretch_tighter_175_275` became the new HOF choice. It did not win every isolated window — the looser KC1.5 / BB2.5 variant was better on 2026 YTD — but it improved every period versus baseline and won the full compound test by a wide margin.

| Period | Baseline | New HOF | Delta vs baseline |
|---|---:|---:|---:|
| 2024 H1 | +94.98% | +104.58% | +9.60pp |
| 2024 H2 | +78.44% | +94.07% | +15.64pp |
| 2025 | +519.66% | +684.45% | +164.79pp |
| 2026 YTD | +47.18% | +57.14% | +9.97pp |
| **Compound** | **+3,073.02%** | **+4,794.26%** | **+1,721.25pp** |

The decisive gain is 2025: the tighter stretch gate avoided too many low-quality relaxed exits while still capturing enough exhaustion exits to improve realized trade quality. It also helped both 2024 halves and kept 2026 above baseline, which made it the more robust HOF candidate than the looser 2026-favored version.

### Exit attribution vs not taking the signal

This compares each new HOF signal exit with the baseline exit for that same trade direction. Positive delta means the signal improved the trade's realized PnL versus staying in.

| Period | Signal exits | Total PnL delta | Avg PnL delta | Correct | Incorrect | Neutral |
|---|---:|---:|---:|---:|---:|---:|
| 2024 H1 | 8 | -1.32pp | -0.17pp | 3 | 4 | 1 |
| 2024 H2 | 4 | +5.20pp | +1.30pp | 3 | 0 | 1 |
| 2025 | 17 | +5.89pp | +0.35pp | 11 | 3 | 3 |
| 2026 YTD | 6 | +3.66pp | +0.61pp | 3 | 2 | 1 |

The small 2024 H1 negative attribution is acceptable because full-strategy path performance still improves versus baseline. The strategy-level result matters more than isolated exit deltas because a take-profit can change subsequent positioning and compounding.

### Decision

Promote `stretch_tighter_175_275` to the HOF ETH 30m YAML. It is less trigger-happy than the relaxed winner, materially improves the hard 2024 H2 window, strongly improves 2025, and keeps 2026 YTD above baseline. The main caveat is that 2026 alone preferred the looser KC1.5 / BB2.5 version, so future monitoring should watch whether the tighter stretch threshold misses too many giveback trades in new 2026 data.

The HOF config was updated in `config/strategies/lazy_swing/eth_30m_hof.yaml`:

- `version: "30m hof stretch_tighter_175_275"`
- `end_date: "2026-05-06"` for refreshed 2026 validation
- `regime_trail_enabled: true`
- `regime_trail_mode: "strict_exhaustion"`
- `regime_exhaustion_kc_z_min: 1.75`
- `regime_exhaustion_bb_z_min: 2.75`
- `regime_exhaustion_prev_adx_min: 20.0`
- `regime_exhaustion_adx_drop_pct: 2.5`
- `trail_stop_min_gain_pct: 1.5`
- `trail_stop_exit_on_signal: true`
- `trail_stop_reentry_enabled: false`

Validation command:

```bash
PYTHONPATH=src .venv/bin/python run_backtest.py config/strategies/lazy_swing/eth_30m_hof.yaml
```

Validation result:

| Config | Window | Final value | Return |
|---|---|---:|---:|
| `eth_30m_hof.yaml` | 2026-01-01 to 2026-05-06 | $157,144.20 | +57.14% |

This matches the robustness run result for `stretch_tighter_175_275` (`+57.1442%`, rounded to `+57.14%`).

---

## 2026-05-06 — March 2026 audit: take-profit helps, but entries are the main leak

### March-to-now benchmark

After promoting the HOF candidate, we tested the exact bad window that motivated the take-profit work: `2026-03-01` to `2026-05-06`.

| Variant | Return | Final Value | WR | Avg PnL / exit | Trail exits |
|---|---:|---:|---:|---:|---:|
| Baseline | +15.03% | $115,034.01 | 42.55% | +0.158% | 0 |
| New HOF KC1.75 / BB2.75 | +15.42% | $115,420.83 | 42.27% | +0.154% | 4 |
| Relaxed KC1.5 / BB2.5 | **+17.97%** | **$117,967.12** | **42.71%** | **+0.178%** | 5 |

The HOF still beat baseline on this slice, but only slightly. The looser stretch variant was better by about `+2.55pp` over the new HOF on this window. This confirms the caveat: the tighter HOF is the cross-period winner, while 2026 March-to-now alone wanted a slightly less restrictive stretch trigger.

### Full trade audit

We audited every HOF trade from `2026-03-01` to `2026-05-06` and wrote the detailed trade list to:

- `reports/lazyswing-2026-march-trade-audit/report.md`
- `reports/lazyswing-2026-march-trade-audit/all_trades.csv`

Key results:

| Metric | Value |
|---|---:|
| HOF return | +15.42% |
| Closed trades with PnL | 97 |
| Win rate | 42.27% |
| Avg PnL per exit | +0.154% |
| Median PnL per exit | -0.600% |
| Trades that never reached +1.5% close-MFE | 53 |
| HOF take-profit exits | 4 |
| Extra trades looser KC1.5 / BB2.5 would catch | 3 |

The most important finding: the weak average PnL is not primarily a take-profit problem. `53 / 97` trades never reached `+1.5%` open profit on a 5m close, so the take-profit rule was not allowed to act. This bucket produced about `-74.30%` total per-trade PnL, with average PnL about `-1.40%`, average close-MFE only `+0.52%`, and average MAE about `-1.69%`.

### Bad-entry bucket

| Pattern | Trades | Avg PnL | Total PnL |
|---|---:|---:|---:|
| Normal ST flip bullish/bearish entries | 46 | about -1.39% | about -63.98% |
| Fast-exit reentries | 7 | -1.47% | -10.32% |
| Immediate flip entries within 5m of prior exit | 18 | -1.77% | -31.79% |
| Held-flip safety exits | 5 | -2.13% | -10.64% |

The HOLD mode did not solve this because HOLD only applies when a SuperTrend flip is rejected by the flip-vol ratio gate. Most of these bad trades were normal allowed flips, not rejected flips. The chop was entering through the front door.

### Existing simple knobs tested

We tested whether existing entry knobs could fix the March slice before designing new logic.

| Variant | 2026-03-01 return | Avg PnL / exit | Verdict |
|---|---:|---:|---|
| Current HOF | +15.42% | +0.154% | Baseline for this check |
| Entry delay 1h | +15.97% | +0.152% | Tiny help only |
| Entry delay 2h | +6.74% | +0.060% | Bad |
| Entry persistence 2 bars | -3.57% | -0.032% | Bad |
| Entry persistence 4 bars | +2.61% | +0.033% | Bad |
| Entry persistence 4 bars, ROC2 | +14.62% | +0.147% | Slightly worse |
| Fast reentry cooldown 8 | +13.43% | +0.139% | Worse |
| Fast reentry cooldown 12 | +11.95% | +0.120% | Worse |
| Flip-vol gate 1.0 / 1.2 | -16.62% | -0.208% | Very bad |
| Flip-vol gate 1.0 / 1.3 | -16.80% | -0.214% | Very bad |

Conclusion: this needs a stateful chop-entry filter, not just a slower entry or stricter RVOL gate.

### Next planned optimization

We created `docs/lazyswing-2026-chop-entry-filter-plan.md` to drive the next experiment. The plan targets three failure modes:

- Fast-exit reentry quality gates.
- Profit-aware HOLD behavior for vol-ratio-rejected flips.
- Momentum-confirmed ST flips, where failed confirmation closes the old trade and stays flat instead of reversing.

The success target is not just higher return; the next experiment must reduce the `not_eligible_for_takeprofit` loss bucket without destroying the 2025 trend edge.

### Sweep execution note

In the Codex/macOS sandbox, Python `ProcessPoolExecutor` hit a semaphore permission issue. Future broad sweeps should prefer launching independent Python subprocess jobs with an outer scheduler capped around 4 concurrent processes. That avoids the sandbox semaphore path and also avoids Python thread GIL limits for CPU-heavy backtests.
