# Intraday Trend Strategy — Implementation Tracker

## Implementation Phases

- [x] **Phase I-1: Indicator Helper Functions**
  Create `src/strategies/intraday_indicators.py` with pure functions:
  `compute_hma`, `compute_supertrend`, `compute_keltner`, `compute_bollinger`, `compute_squeeze`, `compute_vwap_daily`.
  Reuse existing `compute_adx`, `compute_atr`, `compute_ema` from `macd_rsi_advanced.py`.
  Unit tests for each function. *(17 tests, all passing)*

- [x] **Phase I-2: Strategy Skeleton — `prepare()` + Indicator Precomputation**
  Create `src/strategies/intraday_trend.py` with `IntradayTrendStrategy(StrategyBase)`.
  `__init__` reads all params, `prepare()` precomputes all indicators on full DataFrame.
  Register in `registry.py`. Verify indicators compute without errors.

- [x] **Phase I-3: Entry Logic — `on_bar()` for BUY/SHORT**
  Implement entry path: regime filter → directional bias → entry trigger → entry filter.
  State tracking for position, direction, stops.

- [x] **Phase I-4: Exit Logic — Stops, Supertrend Flip, Circuit Breaker**
  Supertrend trailing stop, hard stop (2%), Supertrend flip exit, daily circuit breaker (6%).
  Force liquidate on last bar.

- [x] **Phase I-5: Keltner Midline Bounce Entry**
  Secondary re-entry in established trends. Track `_supertrend_direction_bars`.
  Configurable via `enable_keltner_bounce` param.

- [x] **Phase I-6: Config YAML + Data Prep + First Backtest Run**
  Create `config/intraday_trend_dev.yaml` for dev set (2022-2024).
  Combined 3 CSV files into `data/BTCUSDT-5m-2022-2024-combined.csv`. First run: -24.65%.

- [x] **Phase I-7: Report Enhancement — Intraday-Specific Metrics**
  Created `tmp/analyze_intraday.py` with trades/day, win rate, profit factor, avg bars held,
  regime breakdown, MFE/MAE, max consecutive losses, transaction cost impact.

## Optimization Phases

- [x] **Opt v1→v2: Tune Core Parameters** — Grid search (81 combos) on Supertrend mult, Keltner mult, ADX threshold, volume multiplier. Added cooldown_bars and min_hma_slope_bps. Result: +42.55%, Sharpe 0.51, 1153 trades.

- [x] **Opt v2→v3: Fix Biggest Loser Pattern** — Added breakout_confirm_bars (2), increased cooldown to 12, higher HMA slope (2.0 bps), short_adx_threshold (35). Result: +74.50%, Sharpe 0.89, 617 trades.

- [x] **Opt v3→v4: Improve Exit Quality** — Added breakeven_trigger_pct (1.2%). Tested tighter trailing Supertrend (2.0, 2.5) but both hurt performance by cutting winners short — disabled. Result: +81.77%, Sharpe 0.95, 618 trades.

- [x] **Opt v4→v5: Regime-Specific Tuning** — Added min_atr_pct volatility floor (0.18%). Flat regime ATR avg 0.163% vs trending 0.20-0.31%. Grid search 0.12-0.20% confirmed 0.18% as sweet spot. Flat losses: -17.27% → -1.02%. Result: +106.97% gross, +27.48% after costs, Sharpe 1.15, 465 trades.

- [x] **Final Validation: Run v5 on Test Set** — Test set (2020, 2021, 2025, 2026-01): +133.46% gross, +64.92% after costs, Sharpe 1.33, max DD -11.70%, 45.3% win rate. No overfitting — test set outperforms dev set. Beats BTC B&H on risk-adjusted basis.

- [x] **Opt v5→v6: Fix Jumpy Exit Problem** — Root cause analysis confirmed strategy exits too fast: 25% of trades held <1hr with 3% win rate causing -100% PnL drag; winners hold median 295min vs losers 85min. Supertrend ATR period 10 + multiplier 3.0 flipped on single-candle noise. Four-axis grid search (36 combos): supertrend_atr_period [14,21] × supertrend_multiplier [3.5,4.0,4.5] × min_hold_bars [6,12,24] × hma_period [34,55]. Added `min_hold_bars` param (gates Supertrend exit only; hard stops always fire). Winner: ST(14/3.5) + MH=12 + HMA=55 — short holds <1hr dropped from 24.5% → 5.7%, avg hold 36→48 bars, >4hr trades grew 119→160. Result: +113.30% gross, +40.99% after costs, Sharpe 1.19, 421 trades.

## Performance Summary

| Version | Gross Return | After Costs | Sharpe | Trades | Win% | Profit Factor | Max DD |
|---------|-------------|-------------|--------|--------|------|---------------|--------|
| v1      | -24.65%     | -302.53%    | -0.08  | 2968   | 31.8%| 0.99          | -      |
| v2      | +42.55%     | -131.28%    | 0.51   | 1153   | -    | -             | -      |
| v3      | +74.50%     | -25.59%     | 0.89   | 617    | 33.9%| 1.28          | -17.02%|
| v4      | +81.77%     | -21.67%     | 0.95   | 618    | 34.1%| 1.30          | -17.88%|
| v5 Dev  | +106.97%    | +27.48%     | 1.15   | 465    | 38.1%| 1.42          | -12.82%|
| v5 Test | +133.46%    | +64.92%     | 1.33   | 393    | 45.3%| 1.61          | -11.70%|
| v6 Dev  | +113.30%    | **+40.99%** | **1.19**| 421   | 35.9%| **1.176**     | -12.47%|
