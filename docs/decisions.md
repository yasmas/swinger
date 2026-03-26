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

---

## [2026-02-26] Problem 3 Analysis: Missed Long Opportunities (no change — v8 retained)

Investigated periods where the strategy was flat while price rallied significantly. Analyzed specific missed windows (Jan 27–30 2025, Jun 22–26 2024, Nov 2024, Oct 2024) bar-by-bar to identify blocking conditions.

**Root cause findings:**
- **EMA-200 is the dominant blocker** in the specific windows flagged. Jan 27–30 2025 was a flash crash (BTC dropped ~6% on the DeepSeek news) — price temporarily dipped below EMA-200, and the strategy correctly stayed out of the crash. It re-entered once EMA was satisfied on Jan 30. Jun 22–26 2024 turned out not to be a missed opportunity at all — price fell 7.2% during that window; the EMA filter was correct to block.
- MACD < signal was the largest aggregate blocker (52% of missed hours), but this is structural: histogram crossing zero equals MACD crossing signal, so the strategy is already using the earliest possible MACD-based signal.

**Five approaches tested and rejected (all hurt on net):**

| Approach | 2020 Δ | 2021 Δ | 2022 Δ | 2023 Δ | 2024 Δ | 2025 Δ | Sum Δ |
|---|---|---|---|---|---|---|---|
| ADX 20→15 | -41pt | +41pt | -1pt | +9pt | +19pt | -6pt | +21pt |
| EMA-1% grace | +77pt | +13pt | +0pt | -19pt | +14pt | -4pt | +81pt |
| EMA recent-48h (as v9) | +232pt | -68pt | -12pt | -33pt | -51pt | -18pt | +50pt |
| Post-cover flip (Option A: MACD bullish, 12h) | +494pt | -338pt | -34pt | +5pt | -132pt | -55pt | -60pt |
| Post-cover bounce (Option B: +1.5% bounce + hist, 24h) | +516pt | -373pt | -125pt | +132pt | -116pt | -49pt | -14pt |
| Post-cover recovery (Option C: MACD cross, 24h) | +15pt | -56pt | -9pt | +15pt | -72pt | +6pt | -101pt |

**Recurring pattern:** 2020 is an outlier (short-heavy year — post-cover logic fires frequently and happens to be well-timed). Every other approach consistently hurts 2021 and/or 2024, the two largest bull years.

**Lesson learned (third time):** The EMA-200 filter and standard MACD entry conditions are load-bearing. The strategy already captures most actionable long entries. Apparent "missed opportunities" visible on the chart are either: (a) correct avoidances of choppy/declining conditions, or (b) brief gaps the strategy closes within a few bars. Relaxing entry conditions costs more across most years than the edge cases they capture. **Decision: keep v8 unchanged for entry relaxation approaches.**

---

## [2026-02-26] Weak Golden Cross Filter — adopted as v9

Analyzed 2026 partial-year data and found two losing long trades that were "dead-cat bounces" — weak MACD golden crosses with near-zero histogram values at entry. Rather than relaxing entries, this tightens them: require a minimum MACD histogram strength at golden cross.

**Key insight:** MACD histogram in basis points of price (`hist / price * 10000`) is a scale-invariant measure of cross strength. Genuine trend starts show histogram > 2bps within 1–2 bars; dead-cat bounces hover near zero.

**Critical design decision:** The filter **delays** rather than **blocks**. When a golden cross fires but histogram is below the threshold, the cross is remembered for up to N bars. If histogram strengthens above the threshold while MACD stays bullish within that window, the entry fires. If MACD goes bearish or the window expires, the cross is cancelled. This avoids permanently missing trades — it only delays weak ones by 1–2 bars.

**Grid search (real backtest, 7 years, 17 parameter combinations):**

| | win=1 | win=2 | win=3 | win=5 | win=8 |
|---|---|---|---|---|---|
| **bps>=1** | -83pt | | 0pt | -206pt | -262pt |
| **bps>=2** | -47pt | **+80pt** | +29pt | -158pt | -243pt |
| **bps>=3** | -182pt | | -1pt | -172pt | -249pt |
| **bps>=4** | | | -314pt | | |
| **bps>=5** | -451pt | | -191pt | -360pt | -335pt |

**Winner: 2bps / window=2** (+80pt vs v8). Year-by-year:

| Year | Market | B&H | v8 | v9 | Delta |
|------|--------|-----|-----|-----|-------|
| 2020 | Covid+Bull | +305% | +447% | +556% | **+109pt** |
| 2021 | Mega Bull | +63% | +526% | +441% | -85pt |
| 2022 | Bear | -64% | +99% | +75% | -24pt |
| 2023 | Recovery | +155% | +153% | +153% | 0pt |
| 2024 | Full Bull | +119% | +262% | +343% | **+81pt** |
| 2025 | Correction | -6% | +75% | +62% | -13pt |
| 2026 | Bear (partial) | -27% | +12% | +24% | **+12pt** |
| **Total** | | | **+1574%** | **+1654%** | **+80pt** |

The filter helps in trending years (2020, 2024, 2026) by avoiding weak entries that reverse. The cost in 2021 (-85pt) is the structural trade-off: in a clean bull run, even "weak" crosses often work, so delaying them by 1–2 bars means entering at slightly worse prices. Window=2 limits this cost vs window=3 (-154pt in 2021).

**Decision: adopt 2bps/w2 as v9.** Parameters: `min_cross_hist_bps: 2.0`, `cross_confirm_window: 2`.

---

## [2026-02-27] MACD Death Cross on Re-entries — adopted as v10

Investigated a losing trade in 2025 (Jan 17-27) where a trend continuation re-entry bought into a downturn and held for 10 days to a loss. Root cause: the MACD death cross exit was globally disabled (v3 decision — too noisy on 1H bars), so the only exits were stop-loss/trailing stop (8%, too wide) and RSI overbought reversal (never reached 70).

Re-entry trades are more speculative than fresh MACD-cross entries, so they should have a tighter leash. Tested enabling MACD death cross exclusively for re-entry positions, with a minimum gap threshold to filter noisy crosses.

**Winner: 2bps instant threshold** — MACD death cross triggers exit only when gap ≥ 2bps of price. Beats baseline in 4/5 years tested, with the best improvement in the most recent year (2025: +62% → +85%).

**Decision: adopt as v10.** Parameter: `reentry_macd_exit_bps: 2.0`.

---

## [2026-02-28] OBV Threshold Analysis — not adopted

Paper trading on Feb 28, 2026 entered a short that the backtest did not take. Investigation revealed the OBV check (`OBV < OBV_EMA`) had a margin of just 0.003% (4 units). Live data variation tipped OBV below the threshold in paper trading while backtest OBV remained above.

Analyzed adding a 2bps minimum margin to the OBV check. Across 107 short trades in 2025–2026:
- Filtered trades: 10 (removed 4 wins, 6 losses)
- Win rate: 67.3% → 70.1% (+2.8pt improvement)
- Total PnL: $82,006 → $76,683 (−$5,323 reduction)

The threshold improves win rate but reduces PnL because two of the filtered trades were large winners (+$1,874 and +$2,731).

**Decision: not adopted.** The current binary check is kept. Accept that live/backtest divergence may occur when OBV is borderline — the 2bps threshold's win-rate improvement doesn't compensate for the PnL reduction.

---

## [2026-02-28] MACD Lag on Fast Moves — documented as known limitation

Analyzed why a Feb 28, 2026 short was "late" and covered at a loss. BTC dropped $2,400 (−3.7%) in a single hour (05:00→06:00). The MACD death cross didn't confirm until 06:00, after the bulk of the drop. By 07:00, price had already bounced.

This is structural to MACD-based strategies — the indicator lags by design. Faster MACD periods would catch moves earlier but generate more false signals. The current 12-26-9 parameters are calibrated for best overall performance across 7 years, accepting that some fast moves will be missed or entered late.

**Decision: no change.** Documented as a known limitation in `strategy-macd-rsi-advanced.md`.

---

## [2026-02-28] Hybrid MACD for Short Entry — analyzed, not adopted

Investigated using faster MACD parameters specifically for short entry while keeping standard parameters for exit. Hypothesis: drops are faster than rallies (confirmed: 1.03x–1.24x faster), so a faster entry signal could catch them earlier.

**Background analysis confirmed:**
- 2025: Down moves 1.03x faster than up moves
- 2026: Down moves 1.24x faster than up moves (more pronounced in bearish period)

**Hybrid approach tested:** Use faster MACD for entry decisions only, standard (12-26-9) for exit decisions.

| Config | Avg Win Rate | Total Short PnL (7 yrs) | vs Standard |
|--------|--------------|-------------------------|-------------|
| Standard (12-26-9, EMA200) | 42.0% | $25.6k | baseline |
| Hybrid 50% (6-13-4, EMA100) | **45.4%** | $38.4k | **+$12.8k (+50%)** |
| Hybrid 75% (9-19-7, EMA150) | 42.7% | **$40.9k** | **+$15.3k (+60%)** |

**Year-by-year results show hybrid helps most in difficult years:**
- 2021 (choppy): Standard -$7.4k → Hybrid 50% +$8.8k (massive improvement)
- 2024 (trending): Standard -$15.7k → Hybrid 50% -$4.2k (damage reduction)

**Decision: not adopted yet.** Results are promising (+50-60% improvement in short-only PnL) but need validation in paper trading before committing to production. The faster entry signals catch drops 2–3 hours earlier on average, but also generate ~10% more trades. Documenting for potential future implementation.

---

## [2026-02-28] 5-Minute Short Entry with Scaled MACD — best performer, not adopted

Extended the hybrid MACD investigation to test checking short entries on 5-minute bars while keeping hourly trend context.

**Approaches tested:**

1. **Pure 5m entry (standard MACD 12-26-9):** Check entry on every 5m bar using standard MACD periods. Result: **-$21k PnL** (too noisy, overtrading).

2. **Fully scaled 5m (MACD 144-312-108):** Scale all indicators to 12x periods on 5m bars. Result: Near-zero trades (EMA-2400 too smoothed, rarely allows entries).

3. **Hybrid 5m/1h:** Use 5m bars with scaled MACD for *entry timing*, but 1h bars for *trend context* (EMA200, ADX, OBV) and exits.

**Hybrid 5m/1h results (7 years, short-only):**

| Multiplier | MACD on 5m | Effective Speed | Trades | Win Rate | Total PnL | vs Baseline |
|------------|------------|-----------------|--------|----------|-----------|-------------|
| 12x | 144-312-108 | Same as 1h | 719 | 42% | $31.0k | +$5.4k (+21%) |
| **9x** | **108-234-81** | **25% faster** | **715** | **45%** | **$46.4k** | **+$20.8k (+81%)** |
| 6x | 72-156-54 | 50% faster | 767 | 46% | $28.7k | +$3.1k (+12%) |

**Winner: 9x multiplier** — The 5m MACD with 9x scaling (108-234-81 periods) provides a ~25% faster response than hourly while still filtering noise. Combined with 1h trend filters, this achieves:
- **+81% improvement** vs hourly baseline ($25.6k → $46.4k)
- **+13% improvement** vs best hourly hybrid ($40.9k → $46.4k)
- Best win rate (45%) of all tested configurations

**Why 9x works best:**
- 12x is too slow — essentially the same signal timing as hourly
- 6x is too fast — catches more entries but also more false signals
- 9x is the sweet spot: fast enough to catch drops earlier, slow enough to filter noise

**Decision: not adopted yet.** This is the best performer in backtesting, but requires implementation changes (separate 5m/1h data flows, different entry vs exit timeframes). Document for potential future enhancement. Recommend paper trading validation before production.

---

## [2026-03-26] v17: Shorter Supertrend ATR Period (10 vs 14)

Grid search over `supertrend_atr_period` [10, 12, 14] with multipliers [2.5, 2.75, 3.0].

**Results (ATR10 vs ATR14 baseline, ST multiplier 3.0):**

| Set | v16 (ATR14) | v17 (ATR10) | Delta |
|-----|-------------|-------------|-------|
| Dev Return | +504,752% | +572,482% | +13.4% |
| Dev MaxDD | -16.56% | -13.11% | improved |
| Dev Sharpe | 6.44 | 6.56 | +0.12 |
| Test Return | +1,334,075% | +1,423,902% | +6.7% |
| Test MaxDD | -16.72% | -11.32% | improved |
| Test Sharpe | 6.47 | 6.41 | -0.06 |

Win rate drops slightly (65.5→64.3% dev, 64.6→62.6% test) but avg PnL/trade holds at 0.58-0.60%.

**Live data analysis (Feb-Mar 2026):** ATR10 underperformed by ~2% ($116k vs $118k). Only 3 of 30 trades diverged — all via `supertrend_trailing` exit. Two cases ATR14's tighter trailing stop caught better hourly-close exits; one case ATR10's wider stop survived a wick and rode a bigger move. With only 3 divergent trades in 6 weeks, this is sampling noise, not structural.

**Decision: adopt ATR10.** The dev/test improvement (+13% return, -3.5pp drawdown) across thousands of trades outweighs 3 anecdotal live-period cases. The mechanism is sound: shorter ATR makes entry and trailing bands more responsive to current volatility.
