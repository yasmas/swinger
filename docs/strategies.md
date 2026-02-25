# MACD + RSI Advanced

## Summary

A trend-following strategy that combines MACD momentum signals with RSI confirmation, filtered by ADX trend strength and a long-term EMA bias. Designed to avoid the whipsaw problem that plagues naive MA crossover strategies on high-frequency data.

The core idea: only enter when multiple independent indicators agree that a real trend is underway, and use ATR-based dynamic stops to protect capital. Even though the data feed is 5-minute bars, all indicators are computed on internally resampled 1-hour OHLCV bars to filter out intrabar noise. The 5-minute granularity is preserved for precise stop-loss execution.

This replaces the earlier `ma_crossover_rsi` strategy which suffered from:
- 1,390 trades/year with a 35% win rate
- 93% of exits triggered by RSI(14) dipping below 50 on 5-minute noise
- Only 17% time in market during a +119% BTC rally
- MA spreads under 0.05% at 90% of entries (trading on noise, not trends)

## Indicators

### MACD (Moving Average Convergence Divergence)

Measures the relationship between two exponential moving averages. The MACD line is `EMA(fast) - EMA(slow)`, and the signal line is an EMA of the MACD line itself. When MACD crosses above its signal, momentum is shifting bullish. Default: 12-26-9.

### RSI (Relative Strength Index)

Oscillator (0-100) measuring the ratio of recent gains to recent losses. Values above 70 indicate overbought conditions; below 30 indicates oversold. Used here to confirm momentum on entry (RSI 40-70) and detect exhaustion on exit (reversal from >70). Default period: 14.

### ADX (Average Directional Index)

Measures trend strength regardless of direction on a 0-100 scale. ADX > 20 indicates a meaningful trend; below 20 is ranging/choppy. Used as a gate to suppress all signals during sideways markets where MACD crossovers produce whipsaws. Default period: 14.

### ATR (Average True Range)

Measures volatility as the average of recent bar ranges (high-low, including gaps). Used to set dynamic stop-loss and trailing-stop distances that adapt to current market conditions — tight stops in calm markets, wider stops in volatile ones. Default period: 14.

### EMA-200 (Exponential Moving Average)

Long-term trend bias. Only take long entries when price is above the 200-period EMA, ensuring we trade in the direction of the dominant trend.

## Entry Rules

All must be true simultaneously:

1. **MACD golden cross** — MACD line crosses above signal line
2. **RSI in momentum zone** — RSI between `rsi_entry_low` (40) and `rsi_overbought` (70)
3. **Trend is strong** — ADX > `adx_threshold` (20)
4. **Long-term uptrend** — price > EMA-200
5. **Cooldown elapsed** — at least `cooldown_bars` (12) since last exit

## Exit Rules

Any one triggers a sell:

1. **MACD death cross** — MACD line crosses below signal line
2. **Overbought reversal** — RSI was above 70 and drops back below 70
3. **Stop-loss** — price < entry price - `atr_stop_multiplier` (2.0) x ATR
4. **Trailing stop** — price < peak-since-entry - `atr_trailing_multiplier` (1.5) x ATR

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
| `adx_period` | 14 | ADX lookback period |
| `adx_threshold` | 20 | Minimum ADX for entry |
| `atr_period` | 14 | ATR lookback period |
| `atr_stop_multiplier` | 2.0 | Stop-loss distance in ATR units |
| `atr_trailing_multiplier` | 1.5 | Trailing stop distance in ATR units |
| `ema_trend_period` | 200 | Long-term trend EMA period |
| `cooldown_bars` | 12 | Minimum resampled bars between exit and next entry |
