# MACD + RSI Advanced

## Summary

A trend-following strategy that combines MACD momentum signals with RSI confirmation, filtered by ADX trend strength and a long-term EMA bias. Designed to avoid the whipsaw problem that plagues naive MA crossover strategies on high-frequency data.

The core idea: only enter when multiple independent indicators agree that a real trend is underway, use percentage-floored dynamic stops to let winners run, and re-enter quickly after profitable exits without waiting for a fresh MACD crossover. Even though the data feed is 5-minute bars, all indicators are computed on internally resampled 1-hour OHLCV bars to filter out intrabar noise. The 5-minute granularity is preserved for precise stop-loss execution.

## Evolution

### v1: MA Crossover + RSI (`ma_crossover_rsi`)

The naive baseline. Short/long MA crossover with RSI confirmation on raw 5-minute bars.

- 1,390 trades/year, 35% win rate
- 93% of exits triggered by RSI(14) dipping below 50 on 5-minute noise
- Only 17% time in market during a +119% BTC rally
- MA spreads under 0.05% at 90% of entries (trading on noise, not trends)

**Lesson:** 5-minute indicators are dominated by noise. Need higher-timeframe signals.

### v2: MACD/RSI/ADX/ATR with Pure ATR Stops

Replaced MA crossover with MACD on 1H-resampled bars, added ADX trend filter and ATR-based dynamic stops.

- Only 3.6% time in market, all trades under 24 hours
- Trailing stop (1.5x ATR) and MACD death cross exit fired within hours
- ATR on 1H bars translates to just 1-3% of BTC price — too tight for crypto

**Lesson:** ATR-based stops alone are too tight for crypto. MACD death cross is too noisy on 1H.

### v3: Percentage-Based Stop Floors

Added `stop_loss_pct` and `trailing_stop_pct` as minimum floors: stop distance = `max(ATR * multiplier, pct * price)`. Disabled MACD death cross exit. Reduced cooldown.

- **+134% return** (vs +119% buy & hold), 31% time in market, 77% win rate
- Profit factor 3.31, avg hold 2.6 days
- But: 6 RSI overbought exits at a loss (-9.2%), rapid re-entries fragmenting trends

**Lesson:** Stops now let winners breathe. But RSI exit triggers on shallow pullbacks during strong trends.

### v4: RSI Exit Refinement

Two fixes to the RSI overbought exit: (1) only fire when position is in profit, (2) require RSI to drop below 65 (not just below 70) for exit confirmation.

- **+145% return**, 32 trades (down from 44), 49% time in market
- RSI overbought exits now 100% profitable (was 85%)
- Avg hold increased to 5.6 days, best trade +20.8% (full Feb rally captured)
- But: 37% missed upside between trades due to slow MACD cross re-entry

**Lesson:** Deeper RSI confirmation consolidates fragmented trades. But re-entry after profitable exits is too slow — MACD golden cross takes days to form.

### v5: Trend Continuation Re-entry (current)

After a profitable exit, allow re-entry with relaxed conditions: no MACD golden cross required, just MACD > signal (already bullish). Shorter cooldown (2 bars vs 4).

- **+164% return**, 39 trades, 55% time in market, 74% win rate
- 74% of entries use trend continuation path (76% win rate, +75% total contribution)
- December rally captured in chain of profitable re-entries instead of sitting out
- Profit factor 2.94, avg hold 5.2 days

## Indicators

### MACD (Moving Average Convergence Divergence)

Measures the relationship between two exponential moving averages. The MACD line is `EMA(fast) - EMA(slow)`, and the signal line is an EMA of the MACD line itself. When MACD crosses above its signal, momentum is shifting bullish. Default: 12-26-9.

### RSI (Relative Strength Index)

Oscillator (0-100) measuring the ratio of recent gains to recent losses. Values above 70 indicate overbought conditions; below 30 indicates oversold. Used here to confirm momentum on entry (RSI 40-70) and detect exhaustion on exit (reversal from >70 dropping below 65). Default period: 14.

### ADX (Average Directional Index)

Measures trend strength regardless of direction on a 0-100 scale. ADX > 20 indicates a meaningful trend; below 20 is ranging/choppy. Used as a gate to suppress all signals during sideways markets where MACD crossovers produce whipsaws. Default period: 14.

### ATR (Average True Range)

Measures volatility as the average of recent bar ranges (high-low, including gaps). Used alongside percentage-based floors to set dynamic stop-loss and trailing-stop distances. The actual stop distance is `max(ATR * multiplier, percentage * price)`, ensuring stops are never tighter than the percentage floor even when ATR is small relative to price. Default period: 14.

### EMA-200 (Exponential Moving Average)

Long-term trend bias. Only take long entries when price is above the 200-period EMA, ensuring we trade in the direction of the dominant trend.

## Entry Rules

### Standard Entry

All must be true simultaneously:

1. **MACD golden cross** — MACD line crosses above signal line (fresh crossover)
2. **RSI in momentum zone** — RSI between 40 and 70
3. **Trend is strong** — ADX > 20
4. **Long-term uptrend** — price > EMA-200
5. **Cooldown elapsed** — at least 4 bars since last exit

### Trend Continuation Re-entry

After a profitable exit, relaxed conditions apply (shorter cooldown, no fresh MACD cross needed):

1. **MACD bullish** — MACD > signal (already above, no crossover needed)
2. **RSI in momentum zone** — RSI between 40 and 70
3. **Trend is strong** — ADX > 20
4. **Long-term uptrend** — price > EMA-200
5. **Cooldown elapsed** — at least 2 bars since last exit

## Exit Rules

Any one triggers a sell:

1. **Overbought reversal** — RSI was above 70 and drops below 65, AND position is in profit (100% win rate). Won't fire at a loss — that's what stops are for.
2. **Stop-loss** — price < entry - max(3.0 x ATR, 8% x entry price)
3. **Trailing stop** — price < peak - max(3.0 x ATR, 8% x peak price)
4. **MACD death cross** (optional, off by default) — disabled because it fires within hours on 1H bars with only 54% win rate

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `resample_interval` | `1h` | Resample raw bars to this interval for indicator computation |
| `macd_fast` | 12 | MACD fast EMA period |
| `macd_slow` | 26 | MACD slow EMA period |
| `macd_signal` | 9 | MACD signal line EMA period |
| `rsi_period` | 14 | RSI lookback period |
| `rsi_entry_low` | 40 | Minimum RSI for entry |
| `rsi_overbought` | 70 | RSI overbought threshold |
| `rsi_exit_confirm` | 65 | RSI must drop below this to confirm overbought exit |
| `adx_period` | 14 | ADX lookback period |
| `adx_threshold` | 20 | Minimum ADX for entry |
| `atr_period` | 14 | ATR lookback period |
| `atr_stop_multiplier` | 3.0 | Stop-loss distance in ATR units (floor: `stop_loss_pct`) |
| `atr_trailing_multiplier` | 3.0 | Trailing stop distance in ATR units (floor: `trailing_stop_pct`) |
| `stop_loss_pct` | 8.0 | Minimum stop-loss distance as % of entry price |
| `trailing_stop_pct` | 8.0 | Minimum trailing stop distance as % of peak price |
| `ema_trend_period` | 200 | Long-term trend EMA period |
| `cooldown_bars` | 4 | Minimum resampled bars between exit and next entry |
| `exit_on_macd_cross` | false | Whether MACD death cross triggers exit |
| `trend_reentry` | true | Enable relaxed re-entry after profitable exits |
| `trend_reentry_cooldown` | 2 | Cooldown bars for trend continuation re-entry |
