# Design Decisions

Logged immediately when made, before implementation. Keep entries short and high-level.
Full technical details (schemas, code snippets, formats) belong in `detailed design.md`.

---

## [2026-02-25] Project Language & Stack

Python with pandas, PyYAML, Jinja2, Plotly, and pytest. See `detailed design.md` for the full dependency list.

---

## [2026-02-25] Data Layer: DataSource / DataParser Split

Separate "where to fetch data" (DataSource) from "how to normalize the format" (DataParser). Adding a new file format = new parser only. Adding a new fetch mechanism = new data source only. See `detailed design.md` for interface details.

---

## [2026-02-25] Standard OHLCV DataFrame Schema

All parsers produce a DataFrame with a `date` index and columns: `open`, `high`, `low`, `close`, `volume`. Schema details in `detailed design.md`.

---

## [2026-02-25] Trade Log CSV Schema

All strategies write to a uniform CSV with columns: `date`, `action`, `symbol`, `quantity`, `price`, `cash_balance`, `portfolio_value`, `details` (JSON). Uniformity enables common reporting. Full column spec in `detailed design.md`.

---

## [2026-02-25] Configuration Format

YAML files drive the controller, specifying backtest parameters, data source, and a list of strategies. Multiple strategies in one config enables comparison runs.

---

## [2026-02-25] Historical BTC Data Source

Use Binance Data Vision for BTCUSDT klines. Downloaded 5m and 1m, Jan 2024 – Jan 2026. Note: timestamps are milliseconds pre-2025, microseconds from Jan 2025 onward.

---

## [2026-02-25] HTML Report Chart Design

Two-panel Plotly chart with shared x-axis: top panel shows asset price with BUY/SELL markers and hover tooltips; bottom panel shows % invested over time. Details in `detailed design.md`.

---

## [2026-02-25] Reporting Output Location

Generated reports go in `reports/`. Source code in `src/`. One-time scripts in `tmp/`.

---

## [2026-02-25] Short Selling Support (v6)

Added short selling to Portfolio (short_sell/cover) and to the MACD RSI Advanced strategy. Shorts require higher conviction: ADX >= 25, price below EMA-200, and MACD bearish. Position sized at 50% of cash to limit risk. Worst-case short loss capped around -2% by 6% stop-loss/trailing-stop floors.

---

## [2026-02-25] Trend Continuation Short Entry (v7)

Relaxed short entry to accept MACD already-bearish (not just exact death cross bar), mirroring the long-side trend continuation logic. This fixed missed shorts where ADX and MACD signals didn't align on the same bar. Short count went from 12 to 80, total return from +237% to +269% on 2024 data.

---

## [2026-02-25] Versioned Report Filenames

Added `version` field to backtest config. Propagated through Config, Controller, and Reporter so CSV and HTML filenames include the version (e.g. `_v7.html`). Version also displayed in the HTML report title.

---

## [2026-02-25] Buy-and-Hold Baseline in Reports

Added B&H return and CAGR to the HTML report stats bar, computed from first/last price in the dataset. No separate simulation needed.

---

## [2026-02-25] Multi-Year Validation (2020–2025)

Ran strategy v7 on 6 years of BTC data. Strategy beats B&H in 5 of 6 years, with the only shortfall in 2023 (-11% alpha) during a straight-line recovery. Max drawdown never exceeds -20%. Confirms the strategy generalizes and is not overfit to 2024.

---

## [2026-02-26] Problem 1 Analysis: RSI Exit → Re-Entry Churn (no change)

Investigated the pattern where RSI overbought triggers a sell, followed by a trend-continuation re-buy a few hours later — sometimes at a loss. Tested every combination of:
- **Volume ratio filter** on re-entries (vol/SMA20 >= 1.0)
- **Gap-time filter** (skip quick re-entries < 3h / 6h)
- **ADX conditioning** (apply filter only when trend is weak)
- **ATR% conditioning** (apply filter only when volatility is low/high)
- **Raising RSI exit threshold** in low-volatility conditions to avoid the exit entirely

All filters tested across 6 years (2020–2025, 151 trend-continuation re-entries totalling +310.8%). Every filter either hurt overall PnL or only helped in recent years while destroying returns in 2020–2021 bull markets. The RSI overbought exit consistently outperforms staying in position — even in the calmest conditions, exit+re-enter beats holding with trailing stop by 40%+. **Decision: keep current behavior as-is.** The churn is the unavoidable cost of an excellent exit signal.

---

## [2026-02-26] Problem 2 Analysis: Short Entry Noise Reduction

Investigated short-side churn (55% of 525 short trades are "churn" with PnL between -1% and +1%). Exit logic (MACD golden cross + RSI oversold reversal + stops) was validated first — covering and re-shorting always outperforms staying short, so exits are not the problem.

Shifted focus to **entry quality**. Researched best practices for short entry confirmation (volume confirmation, OBV divergence, Bollinger Band breakdowns, MACD histogram momentum, Stochastic RSI). Designed and modeled three alternative entry filters.

**First attempt (Strategy E: BB Breakdown + Volume) — reverted.** The initial model used wrong stop parameters (trailing 4% vs actual 6%, ATR multipliers 1.5/2.0 vs actual 3.0/3.0). With actual wider stops, the BB filter was far too restrictive — cut shorts from 525 to 207 and destroyed +448% of short PnL. Lost short profits compounded into smaller long positions, causing massive overall return drops. **Lesson: always validate models against actual strategy parameters.**

**Second attempt (Strategy D: OBV + MACD Histogram) — adopted as v8.** Added two confirmation filters to short entry: (1) OBV below its 20-period EMA (bearish institutional flow), (2) MACD histogram declining bar-over-bar (accelerating bearish momentum). This is a gentler filter — 24% trade reduction vs E's 60%.

| Year | v7 Return | v8 Return | Delta | v7 Shorts | v8 Shorts | v7 Short PnL | v8 Short PnL |
|------|-----------|-----------|-------|-----------|-----------|-------------|-------------|
| 2020 | +474% | +447% | -26% | 66 | 45 | +110% | +113% |
| 2021 | +457% | **+526%** | **+70%** | 122 | 94 | +157% | +179% |
| 2022 | +95% | +99% | +3% | 113 | 89 | +153% | +150% |
| 2023 | +144% | +153% | +9% | 53 | 30 | +24% | +35% |
| 2024 | +269% | +262% | -7% | 80 | 67 | +80% | +76% |
| 2025 | +62% | **+75%** | **+12%** | 91 | 73 | +78% | +88% |
| **Total** | | | | **525** | **398** | **+601%** | **+641%** |

v8 wins in 4/6 years, total short PnL *increases* from +601% to +641% despite 24% fewer trades. **Decision: adopt Strategy D as v8.**
