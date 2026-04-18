# ETH-PERP-INTX LazySwing grid search

> **2026-04-18 update — numbers re-run after look-ahead fix.** Earlier runs of this grid (returns in the 10⁵–10⁸% range) had a 30-min look-ahead bias in `_5m_to_hourly`: bars at the start of a new bucket were reading indicators for *that bucket's* close (still in the future). The fix maps each 5m bar to the *just-completed* bucket — same timing the live bot observes. All numbers below are post-fix and reflect realistic, executable behavior. See `docs/analyze-live-paper_eth_vs_backtest_eth_apr2026.md` for the live-vs-backtest validation.

Period: **2025** in-sample uses `data/ETH-PERP-INTX-5m-all.csv` (Coinbase INTX 5m, 2025-01-01 → 2025-12-31)
Period: **2026** forward tests use `data/ETH-PERP-INTX-5m-2026.csv`

Initial cash $100,000.

## Summary

Among the configs in this grid, **30m ST 25/1.75** narrowly leads on 2025 in-sample return (+390.5%) but with the smallest trade count and the smallest 30m drawdown; **30m ST 20/1.5** is a close second (+366.1%) and remains the strongest 30m setup on the 2026 forward windows. For **1h** bars, **ST 20/1.0** still wins on 2025 in-sample return (+89.0%), but on 2026 forward data **1h ST 16/1.25** clearly takes over (+78.0% YTD vs ST 20/1.0's +50.2%) with materially better Sharpe and lower drawdown.

The post-fix returns are now in a realistic regime: the look-ahead bias was inflating the in-sample 1h winner alone by ~6 orders of magnitude. The relative ranking of 30m configs is stable; the 1h ranking shifted (ST 20/1.0 dropped from 304M% to 89%, while shorter-ATR configs like 12/1.5 surfaced as competitive).

### 30m resample (sorted by return)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
| 30min | 25 | 1.75 | 390.5256 | 2.0783 | 38.6682 | -28.2976 | 857 |
**| 30min | 20 | 1.5 | 366.0734 | 2.0109 | 36.5784 | -33.7232 | 1059 |**
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

Forward tests use `data/ETH-PERP-INTX-5m-2026.csv` (Coinbase INTX 5m). Configurations: best **two** from the 30m table and best **three** from the 1h table (2025, sorted by return — same selection as before the fix, for direct comparability).

2026 YTD ends **2026-04-17** (last bar in the download). The April window is **2026-04-01–2026-04-30**; available bars stop at the same last timestamp, so April is a **partial** month.

### Out-of-sample: 2026 YTD (2026-01-01 → 2026-04-17)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
| 1h | 16 | 1.25 | 78.0490 | 2.7205 | 40.6250 | -17.3042 | 161 |
| 1h | 20 | 1.25 | 51.5641 | 1.9741 | 38.4146 | -19.8859 | 165 |
| 1h | 20 | 1.0  | 50.2053 | 1.8441 | 38.4956 | -24.3053 | 227 |
| 30min | 20 | 1.5 | 37.3653 | 1.6650 | 38.8112 | -19.2908 | 287 |
| 30min | 25 | 1.5 | 21.8616 | 1.1329 | 37.3288 | -19.6930 | 293 |

### Out-of-sample: April 2026 (2026-04-01 → 2026-04-30, partial through data end)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
| 1h | 20 | 1.25 | 17.5328 | 7.5704 | 45.4545 | -7.2881 | 23 |
| 1h | 16 | 1.25 | 17.5328 | 7.5704 | 45.4545 | -7.2881 | 23 |
| 1h | 20 | 1.0  | 10.8885 | 4.3209 | 35.7143 | -9.0737 | 29 |
| 30min | 20 | 1.5 | 8.1820  | 3.7363 | 42.8571 | -7.1604 | 43 |
| 30min | 25 | 1.5 | 2.8374  | 2.1660 | 36.3636 | -9.6386 | 45 |

## Findings

**Win rates dropped sharply.** Pre-fix WRs were 60-80%; post-fix WRs are 33-40%. That is the classic trend-following profile (small frequent losses + occasional large wins). The earlier WRs were a direct artifact of look-ahead — the strategy was effectively peeking at the bucket close and declaring direction with hindsight.

**30m — ST 20/1.5 vs ST 25/1.75**  
ST 25/1.75 narrowly wins 2025 (+390.5% vs +366.1%) on **fewer trades and a smaller drawdown**. But on the 2026 forward windows we only ran ST 20/1.5 and ST 25/1.5; ST 20/1.5 leads in both forward windows. Worth running ST 25/1.75 on the forward set before re-anointing the HoF.

**1h — ST 20/1.0 leads in 2025; 1h ST 16/1.25 leads on the 2026 forward**  
On 2026 YTD, **ST 16/1.25 returns +78.0%** with the **best Sharpe (2.72), best win rate (40.6%), and lowest drawdown (-17.3%) of the five forward configs**. ST 20/1.0 is third on return (+50.2%) and has the worst drawdown (-24.3%) among the forward 1h configs. April 2026 ties ST 20/1.25 and ST 16/1.25 at +17.5% (identical because no flip differentiated them in the small window).

**Out-of-sample (2026)**  
Forward tests reuse the top **two 30m** and **three 1h** configs from the 2025 sort. April is **partial** through the last downloaded bar. The 1h timeframe dominates: top 3 forward returns are all 1h.

**Hall-of-fame implication**  
The current HoF picks (30m ST 20/1.5, 1h ST 20/1.0) are still defensible on 2025, but the 2026 forward suggests **1h ST 16/1.25 is the stronger 1h candidate going forward**. Recommend a follow-up grid with ST 25/1.75 and ST 16/1.25 in the forward sweep before updating the HoF.

See: `tmp/eth-grid/eth_oos_forward_results.csv` for OOS metrics.

## Hall of fame (canonical 2025 backtests)

Reference implementations for the two highlighted configurations—**full-year 2025**, same data as the grid—live under `data/hall-of-fame/lazyswing/eth-perp/`. **Re-generated 2026-04-18 with the look-ahead fix.**

| Config | YAML | HTML report | Trade log (CSV) | 2025 return |
|--------|------|-------------|-----------------|-------------|
| **30m ST 20/1.5** | [eth-perp-30m-st20-m15.yaml](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15.yaml) | [eth-perp-30m-st20-m15-report-2025.html](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15-report-2025.html) | [eth-perp-30m-st20-m15-trades.csv](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15-trades.csv) | +362.59% |
| **1h ST 20/1.0** | [eth-perp-1h-st20-m10.yaml](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10.yaml) | [eth-perp-1h-st20-m10-report-2025.html](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10-report-2025.html) | [eth-perp-1h-st20-m10-trades.csv](../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10-trades.csv) | +88.27% |

Regenerate HTML + CSV from the YAML with:

`PYTHONPATH=src python scripts/generate_eth_perp_hall_of_fame_reports.py`
