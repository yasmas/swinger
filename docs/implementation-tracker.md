# Intraday Trend Strategy — Implementation Tracker

## Implementation Phases

- [ ] **Phase I-1: Indicator Helper Functions**
  Create `src/strategies/intraday_indicators.py` with pure functions:
  `compute_hma`, `compute_supertrend`, `compute_keltner`, `compute_bollinger`, `compute_squeeze`, `compute_vwap_daily`.
  Reuse existing `compute_adx`, `compute_atr`, `compute_ema` from `macd_rsi_advanced.py`.
  Unit tests for each function.

- [ ] **Phase I-2: Strategy Skeleton — `prepare()` + Indicator Precomputation**
  Create `src/strategies/intraday_trend.py` with `IntradayTrendStrategy(StrategyBase)`.
  `__init__` reads all params, `prepare()` precomputes all indicators on full DataFrame.
  Register in `registry.py`. Verify indicators compute without errors.

- [ ] **Phase I-3: Entry Logic — `on_bar()` for BUY/SHORT**
  Implement entry path: regime filter → directional bias → entry trigger → entry filter.
  State tracking for position, direction, stops.

- [ ] **Phase I-4: Exit Logic — Stops, Supertrend Flip, Circuit Breaker**
  Supertrend trailing stop, hard stop (2%), Supertrend flip exit, daily circuit breaker (6%).
  Force liquidate on last bar.

- [ ] **Phase I-5: Keltner Midline Bounce Entry**
  Secondary re-entry in established trends. Track `_supertrend_direction_bars`.
  Configurable via `enable_keltner_bounce` param.

- [ ] **Phase I-6: Config YAML + Data Prep + First Backtest Run**
  Create `config/intraday_trend_dev.yaml` for dev set (2022-2024).
  Combine data files if needed. Run end-to-end backtest, generate CSV + HTML.

- [ ] **Phase I-7: Report Enhancement — Intraday-Specific Metrics**
  Add trades/day, win rate, profit factor, avg bars held, regime breakdown,
  max consecutive losses, transaction cost impact.

## Optimization Phases

- [ ] **Opt v1→v2: Tune Core Parameters** — Grid search on HMA period, Supertrend mult, Keltner mult, ADX threshold
- [ ] **Opt v2→v3: Fix Biggest Loser Pattern** — Analyze worst losses, add confirmation/filters
- [ ] **Opt v3→v4: Improve Exit Quality** — MFE/MAE analysis, breakeven stop, profit targets
- [ ] **Opt v4→v5: Regime-Specific Tuning** — Per-regime performance, adjust thresholds
- [ ] **Final Validation: Run v5 on Test Set** — Out-of-sample validation, strategy comparison
