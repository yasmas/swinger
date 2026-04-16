# Experiment: Extended-Hours Edge in SwingParty

**Date:** 2026-04-15
**Strategy:** SwingParty (LazySwing coordinator, volume-breakout scorer)
**Period:** 2025-01-01 → 2026-04-15 (15.5 months)
**Settings:** max_positions=3, ST(10, 2.0), entry_persist_max_bars=4, drift=1%, cost=0.05%
**Data:** Massive/Polygon 5m aggregates (extended hours 4 AM–8 PM ET included)

---

## Motivation

The live bot (`nasdaq-momentum-apr13-26`) fetches bars during **4 AM–8 PM ET** (Mon–Fri), so both pre-market and after-hours bars enter the strategy. While investigating a live LRCX entry that didn't fire after a short cover, we found that Polygon returns sparse bars in extended hours causing the hourly resample to stall. Before deciding whether to restrict to RTH, we ran a controlled experiment to understand whether extended-hours trades add value or introduce noise.

---

## Method

Two 10-stock universes were backtested under identical settings on the same 15-month window:

- **Momentum10** — the live bot's universe (high-beta semi/software/fintech): AVGO, AXON, INTC, INTU, LRCX, MPWR, MRVL, PLTR, TEAM, ZS
- **Random10** — 10 randomly-drawn Nasdaq-100 names (seed=2025, excluding Momentum10): AAPL, AMGN, COST, KHC, KLAC, NXPI, PDD, REGN, ROP, TXN

Each completed trade was classified by session at entry and exit:
- **RTH:** 9:30 AM–4:00 PM ET weekdays
- **Extended:** 4:00 AM–9:30 AM or 4:00 PM–8:00 PM ET weekdays

**Groups:**
| | Exit RTH | Exit Extended |
|---|---|---|
| **Entry RTH** | A | B |
| **Entry Extended** | C | D |

Data quality was validated on the Momentum10 run: 102 sampled extended-hours trades (12.2%), 1,113 bars inspected — zero last-trade-carry bars (flat OHLC + vol < 20). Median volume at actual trade bars: 11,676 shares. See full check below.

---

## Results

### Momentum10 — AVGO AXON INTC INTU LRCX MPWR MRVL PLTR TEAM ZS
**Total return: +166,410% | 1,290 completed trades**

| Group | Description | Trades | % | Win rate | Avg PnL% | Avg win% | Avg loss% | Profit factor |
|-------|-------------|-------:|--:|---------:|---------:|---------:|----------:|--------------:|
| A | RTH → RTH | 454 | 35% | 70.5% | +1.10% | +1.90% | −0.80% | 7.82 |
| B | RTH → extended | 336 | 26% | 75.6% | +1.97% | +2.90% | −0.90% | 8.37 |
| C | Extended → RTH | 256 | 20% | 70.7% | +1.84% | +2.98% | −0.89% | 10.47 |
| **D** | **Extended → extended** | **244** | **19%** | **74.2%** | **+2.63%** | **+3.87%** | **−0.92%** | **32.97** |

### Random10 — AAPL AMGN COST KHC KLAC NXPI PDD REGN ROP TXN
**Total return: +4,631% | 1,297 completed trades**

| Group | Description | Trades | % | Win rate | Avg PnL% | Avg win% | Avg loss% | Profit factor |
|-------|-------------|-------:|--:|---------:|---------:|---------:|----------:|--------------:|
| A | RTH → RTH | 537 | 41% | 72.4% | +0.71% | +1.18% | −0.52% | 5.09 |
| B | RTH → extended | 329 | 25% | 69.0% | +0.79% | +1.50% | −0.77% | 5.36 |
| **C** | **Extended → RTH** | **237** | **18%** | **73.4%** | **+1.40%** | **+2.21%** | **−0.83%** | **7.25** |
| D | Extended → extended | 194 | 15% | 67.5% | +1.03% | +1.89% | −0.76% | 3.73 |

---

## Key Findings

### 1. RTH-only (Group A) is never the best group in either universe
Across both runs, pure RTH trades have the lowest avg PnL% and are competitive but not exceptional on win rate. The market is most efficiently priced and mean-reverting during regular hours.

### 2. The extended-hours edge is real — but universe-dependent
- In **Momentum10**, Group D (both legs extended) is by far the standout: PF=32.97, avg PnL +2.63%, avg win +3.87%. These positions catch a multi-session directional move in high-beta names that hasn't been fully re-priced by market open.
- In **Random10**, Group D is the **weakest** group (PF=3.73, avg PnL +1.03%). Extended-hours moves in more defensive or lower-beta names (REGN, AMGN, KHC, ROP, COST) don't sustain into RTH the same way.

### 3. Extended entry → RTH exit (Group C) is the most consistent cross-universe pattern
Group C is the second-best group in Momentum10 (PF=10.47) and the best in Random10 (PF=7.25). The pattern — enter on an extended-hours ST flip, exit when RTH volume re-confirms — appears to be a robust edge regardless of universe. Possible explanation: extended-hours ST flips on real volume are early signals of institutional accumulation/distribution that plays out once the regular session opens.

### 4. Losses are essentially flat across groups and universes
Avg loss ranges from −0.52% to −0.92% across all groups and both runs. The session at entry/exit does not meaningfully change the loss magnitude — it only affects win size. Restricting to RTH would not improve the loss profile.

### 5. The performance gap between universes dwarfs the session effect
Momentum10 returns 36× more than Random10 (+166,410% vs +4,631%) on identical strategy parameters. Universe selection is the dominant factor; session classification is a second-order optimization on top of it.

---

## Data Quality Check (Momentum10 run)

**Zero carry-price bars found** in 102 sampled extended-hours trades (12.2% of B+C+D), 1,113 bars inspected with criterion: flat OHLC AND vol < 20 → 0 hits.

Flat OHLC bars appeared in 10.2% of inspected bars but all carried real volume — they are single-print block trades where O=H=L=C by definition, not Polygon carry artifacts.

Volume at exact entry/exit bars (n=202):

| min | p10 | p25 | median | p75 | p90 | max |
|----:|----:|----:|-------:|----:|----:|----:|
| 52 | 311 | 1,482 | 11,676 | 112,759 | 435,282 | 4,271,236 |

Sample bar windows confirm real two-sided price discovery during extended hours (e.g. AVGO 04:00 ET: vol=2,031 spread=$1.12; AVGO 07:00 ET: vol=3,917 spread=$0.34).

---

## Implications

1. **Do not restrict to RTH.** Eliminating extended-hours fetching would cut ~60–65% of trades and the majority of dollar PnL across both universes, with no data-quality justification.
2. **For the Momentum10 live bot** specifically, extended-hours trades — especially Group D — are a significant alpha source. The current `is_market_open` gate (4 AM–8 PM ET in `MassiveRest`) is correctly calibrated.
3. **The live staleness bug** (Polygon returning no bars for empty 5m windows → hourly resample never completing) should be fixed separately so persist entries like the LRCX case can evaluate on sparse after-hours data. Candidate fix: relax `resample_latest_hour` to accept fewer than 12 bars per hour during extended sessions.

---

## Files

| File | Description |
|------|-------------|
| `config/strategies/swing_party/nasdaq_momentum_2025_backtest.yaml` | Momentum10 backtest config |
| `config/strategies/swing_party/nasdaq_random10_2025_backtest.yaml` | Random10 backtest config |
| `data/backtests/nasdaq-momentum-2025/{SYM}-5m-2025-2026.csv` | Momentum10 raw 5m data |
| `data/backtests/nasdaq-random10-2025/{SYM}-5m-2025-2026.csv` | Random10 raw 5m data |
| `reports/swing_party/Nasdaq_Momentum_2025_Backtest_swing_party_v1.csv` | Momentum10 trade log |
| `reports/swing_party/Nasdaq_Random10_2025_Backtest_swing_party_v1.csv` | Random10 trade log |
| `scripts/download_nasdaq_momentum_tickers.py` | Momentum10 data download |
| `scripts/analyze_market_hours_trades.py` | Session analysis script |
