# Nasdaq SwingParty: Optimized Scoring — NATR-7

**Experiment date:** 2026-04-16
**Simulation:** 11 weeks, 2025-05-26 → 2026-04-10
**Universe:** Nasdaq-100 (~100 symbols, 5-min bars from Massive)
**SwingParty params:** 1h resample, ST(10, 2.0), max_positions=3, entry_persist=4 bars / 1% drift

---

## What we tested

Weekly screener simulation: each week W, score the full Nasdaq-100 universe with a given indicator, pick the **top decile (~10 stocks)** for SwingParty trading in week W+1. Repeat for 11 weeks and track accumulated portfolio return.

Five scoring methods were benchmarked. This document compares the winner — **NATR-7** — against the next three best.

---

## NATR-7 — How it works

> `NATR(7) = ATR(7) / close × 100`

Wilder ATR with period 7, divided by Friday's closing price, expressed in percent. No secondary filter, no ROC component — pure volatility rank across the full universe. The idea: stocks that moved the most relative to their price in week W are most likely to keep generating large intraday swings in W+1 for SwingParty to exploit.

---

## Results: Terminal Accumulated Return (G1 / max_positions=3)

| Scoring method | Description | Terminal return |
|---|---|---|
| **NATR-7** | ATR(7) / close × 100, top decile | **+466.2%** |
| Momentum | Mean absolute weekly ROC, top decile | +258.3% |
| Shock+Vol+ROC | Composite: vol shock + ROC | +235.7% |
| ATR-ROC5 | ATR(14) filter → rank by 5-day \|ROC\| | +228.0% |

NATR-7 beats the next-best by **+81%** in terminal accumulated return over 11 weeks.

---

## Week-by-Week Accumulated Return (NATR-7 G1/max3)

| Week (W+1) | Weekly return | Accumulated |
|---|---|---|
| 2025-05-26 | +8.2% | +8.2% |
| 2025-06-23 | +16.9% | +26.5% |
| 2025-07-28 | +10.8% | +40.2% |
| 2025-09-01 | +6.2% | +48.9% |
| 2025-09-29 | +12.5% | +67.5% |
| 2025-10-27 | +23.0% | +106.1% |
| 2025-12-01 | +15.3% | +137.6% |
| 2026-01-05 | +20.4% | +186.1% |
| 2026-02-02 | **+39.0%** | +297.8% |
| 2026-03-02 | +13.6% | +351.8% |
| 2026-04-06 | +25.3% | **+466.2%** |

No losing weeks across all 11 simulations.

---

## Why NATR-7 wins

**Momentum and composite methods** capture trend direction or ROC magnitude, but they pre-filter or blend signals in ways that can dilute pure volatility selection. ATR-ROC5 applies an ATR(14) pre-filter that reduces pool size to ~4 stocks per group, causing high variance and missed opportunities.

**NATR-7 is minimal and direct**: a short-window ATR normalized by price selects the highest-volatility stocks at the moment of selection, which are exactly the stocks SwingParty's SuperTrend flips will capture most profit from. No additional ROC signal adds noise.

Key characteristics of NATR-7 top-decile stocks: mean NATR ~4–7% (vs ~2–3% for lower deciles). Recurring names: MSTR, TSLA, APP, INTC, MU, STX, PLTR, MRVL — highly liquid, high-beta tech names with large intraday moves.

---

## Consistency across groups

| Group | max3 terminal | max4 terminal | max5 terminal |
|---|---|---|---|
| G1 (top decile) | **+466.2%** | +437.6% | +361.0% |
| G2 | +240.7% | +181.4% | +178.7% |
| G3 | +164.9% | +141.2% | +149.7% |

Strong monotonic signal: higher NATR decile → better results. G1 dominates clearly; fewer slots (max3) tends to be best, keeping the portfolio concentrated in the strongest signals.

---

## Champion configuration

- **Screener:** NATR-7, group 1 (top decile), ~10 stocks
- **Strategy:** SwingParty, 1h resample, ST(10, 2.0), max_positions=3
- **Entry persistence:** 4 bars / 1% drift
- **Files:**
  - Config: `data/hall-of-fame/swingparty/NASDAQ100-swingparty-natr7-N11-g1-max3.yaml`
  - Report: `data/hall-of-fame/swingparty/NASDAQ100-swingparty-natr7-N11-g1-max3.html`
  - Trades: `data/hall-of-fame/swingparty/NASDAQ100-swingparty-natr7-N11-g1-max3.csv`
