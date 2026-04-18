# ETH-PERP-INTX LazySwing grid search

Period: **2025** in-sample uses `data/ETH-PERP-INTX-5m-all.csv` (Coinbase INTX 5m, 2025-01-01 → 2025-12-31)
Period: **2026** forward tests use `data/ETH-PERP-INTX-5m-2026.csv`

Initial cash $100,000.

## Summary 

Among the grids we ran, **30m resampling with Supertrend length 20 and multiplier 1.5 (ST 20/1.5)** is the strongest **30m** configuration across every slice: the 2025 grid, 2026 YTD, and April 2026 (ahead of the other 30m candidate, ST 25/1.5). 
For **1h** bars, **ST 20/1.0** delivers the **highest return** in all three windows and the **best win rate** among the compared 1h settings in 2025 and April 2026; on 2026 YTD only **ST 16/1.25** in the forward five edges slightly higher on win rate (~81% vs ~78%). *Percent returns are simulator outputs with heavy compounding—use them for ranking, not as literal forecasts.*

### 30m resample (sorted by return)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
**| 30min | 20 | 1.5 | 22287292.6885 | 13.7491 | 63.5161 | -6.8586 | 1059 |**
| 30min | 25 | 1.5 | 21778771.7293 | 13.7288 | 62.4765 | -6.8586 | 1067 |
| 30min | 25 | 1.75 | 4351948.8093 | 12.2877 | 64.0187 | -8.9261 | 857 |
| 30min | 25 | 2.0 | 789613.9841 | 10.7778 | 63.1367 | -11.9339 | 747 |

### 1h resample (sorted by return)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
**| 1h | 20 | 1.0 | 304278856.2325 | 16.88 | 77.1991 | -11.1193 | 865 |**
| 1h | 20 | 1.25 | 23478643.8871 | 14.1779 | 75.3687 | -11.1193 | 679 |
| 1h | 16 | 1.25 | 17714376.6776 | 13.9586 | 74.9267 | -11.1193 | 683 |
| 1h | 14 | 1.25 | 15977493.7686 | 13.9231 | 74.3363 | -11.1193 | 679 |
| 1h | 12 | 1.25 | 15796560.152 | 14.81 | 74.9258 | -11.1193 | 675 |
| 1h | 10 | 1.25 | 13608405.5704 | 14.6196 | 73.5549 | -11.1193 | 693 |
| 1h | 8 | 1.25 | 12392286.4245 | 14.7971 | 73.913 | -11.1193 | 691 |

---

Forward tests use `data/ETH-PERP-INTX-5m-2026.csv` (Coinbase INTX 5m). Configurations: best **two** from the 30m table and best **three** from the 1h table (2025, sorted by return).

2026 YTD ends **2026-04-17** (last bar in the download). The April window is **2026-04-01–2026-04-30**; available bars stop at the same last timestamp, so April is a **partial** month.

### Out-of-sample: 2026 YTD (2026-01-01 → 2026-04-17)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
| 1h | 20 | 1.0 | 5536.537 | 17.2907 | 77.8761 | -8.3413 | 227 |
| 1h | 16 | 1.25 | 3372.4416 | 17.8713 | 81.25 | -7.6112 | 161 |
| 30min | 20 | 1.5 | 3245.7656 | 17.1294 | 65.035 | -6.3339 | 287 |
| 1h | 20 | 1.25 | 3230.288 | 16.746 | 81.0976 | -7.6112 | 165 |
| 30min | 25 | 1.5 | 2973.5606 | 16.2242 | 64.3836 | -7.2786 | 293 |

### Out-of-sample: April 2026 (2026-04-01 → 2026-04-30, partial through data end)

| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |
|----------|--------|------|----------------|--------|------------|----------|---------|
| 1h | 20 | 1.0 | 73.7186 | 23.7172 | 82.1429 | -1.9091 | 29 |
| 1h | 20 | 1.25 | 66.5264 | 20.232 | 77.2727 | -2.4042 | 23 |
| 1h | 16 | 1.25 | 66.5264 | 20.232 | 77.2727 | -2.4042 | 23 |
| 30min | 20 | 1.5 | 59.3006 | 19.1897 | 57.1429 | -5.3508 | 43 |
| 30min | 25 | 1.5 | 58.7346 | 18.9863 | 59.0909 | -4.8708 | 45 |

## Findings

**30m — ST 20 / 1.5**  
Across **2025** (full grid), **2026 YTD**, and **April 2026**, this setting **beats the other 30m variant we tracked (ST 25/1.5)** on return each time. It also shows **lower drawdown than the leading 1h configs** on 2026 YTD in the forward batch.

**1h — ST 20 / 1.0**  
**Highest return** in the 2025 grid and in **both** 2026 forward windows. **Win rate** is **best among the 1h rows** in **2025** and **April 2026**; on **2026 YTD** only **ST 16/1.25** (included in the forward five) is slightly higher on win rate—so ST 20/1.0 is **almost always** the win-rate leader among the primary 1h candidates, with that one exception.

**Out-of-sample (2026)**  
Forward tests reuse the top **two 30m** and **three 1h** configs from the 2025 sort. April is **partial** through the last downloaded bar.

See: `eth_oos_forward_results.csv` for OOS metrics.

## Hall of fame (canonical 2025 backtests)

Reference implementations for the two highlighted configurations—**full-year 2025**, same data as the grid—live under `data/hall-of-fame/lazyswing/eth-perp/`:

| Config | YAML | HTML report | Trade log (CSV) |
|--------|------|-------------|-----------------|
| **30m ST 20/1.5** | [eth-perp-30m-st20-m15.yaml](../../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15.yaml) | [eth-perp-30m-st20-m15-report-2025.html](../../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15-report-2025.html) | [eth-perp-30m-st20-m15-trades.csv](../../data/hall-of-fame/lazyswing/eth-perp/eth-perp-30m-st20-m15-trades.csv) |
| **1h ST 20/1.0** | [eth-perp-1h-st20-m10.yaml](../../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10.yaml) | [eth-perp-1h-st20-m10-report-2025.html](../../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10-report-2025.html) | [eth-perp-1h-st20-m10-trades.csv](../../data/hall-of-fame/lazyswing/eth-perp/eth-perp-1h-st20-m10-trades.csv) |

Regenerate HTML + CSV from the YAML with:

`PYTHONPATH=src python scripts/generate_eth_perp_hall_of_fame_reports.py`
