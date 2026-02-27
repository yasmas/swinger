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

### v5: Trend Continuation Re-entry

After a profitable exit, allow re-entry with relaxed conditions: no MACD golden cross required, just MACD > signal (already bullish). Shorter cooldown (2 bars vs 4).

- **+164% return**, 39 trades, 55% time in market, 74% win rate
- 74% of entries use trend continuation path (76% win rate, +75% total contribution)
- December rally captured in chain of profitable re-entries instead of sitting out
- Profit factor 2.94, avg hold 5.2 days

### v6: Short Selling

Added short positions when strong bearish indicators align. Shorts sized at 50% of cash with tighter 6% stop/trailing floors (vs 8% for longs). Required a fresh MACD death cross on the exact bar.

- **+237% return** on 2024, 12 short trades, 67% win rate
- Shorts profitable during pullbacks, worst loss -2.2%
- But: missed extended downtrends where MACD death cross and ADX didn't align on the same bar (e.g. Jun 22-24 drop)

**Lesson:** Requiring an exact-bar death cross for short entry is too restrictive — same problem v5 solved for longs.

### v7: Trend Continuation Short Entry

Relaxed short entry: accept MACD already below signal (bearish), not just fresh death cross. Mirrors the long-side trend continuation logic.

- **+269% return** on 2024, 80 short trades, 66% win rate, +80% short PnL
- Jun 22-24 drop now captured (+6.3% short)
- Worst short loss unchanged at -2.2%

### v8: OBV + MACD Histogram Short Filter

Investigated short-side churn: 55% of v7's 525 shorts were "churn" trades (PnL between -1% and +1%). Researched volume confirmation, OBV divergence, Bollinger Band breakdowns, and histogram momentum. First implemented BB breakdown filter (Strategy E), but it was far too restrictive with the actual stop parameters — cut shorts by 60% and destroyed returns. Reverted and implemented Strategy D instead.

Two new confirmation filters on short entry:
1. **OBV bearish** — On-Balance Volume below its 20-period EMA (institutional selling pressure)
2. **MACD histogram declining** — histogram more negative than previous bar (bearish momentum accelerating)

This is a gentler filter (24% trade reduction) that removes the right shorts:

| Year | Market | B&H | v7 | v8 | Delta |
|------|--------|-----|-----|-----|-------|
| 2020 | Covid+Bull | +305% | +474% | +447% | -26% |
| 2021 | Mega Bull | +63% | +457% | +526% | **+70%** |
| 2022 | Bear | -64% | +95% | +99% | +3% |
| 2023 | Recovery | +155% | +144% | +153% | +9% |
| 2024 | Full Bull | +119% | +269% | +262% | -7% |
| 2025 | Correction | -6% | +62% | +75% | **+12%** |

Shorts reduced from 525 to 398 (-24%), total short PnL *increased* from +601% to +641%. v8 wins in 4/6 years.

### v9: Weak Golden Cross Filter

Analyzed partial 2026 data and identified losing long trades caused by "dead-cat bounces" — weak MACD golden crosses where the histogram is near zero at the cross bar. These tend to reverse quickly, generating stop-loss exits.

Added a minimum histogram strength filter on golden cross entries, measured in basis points of price (scale-invariant). The filter **delays** rather than blocks: when a cross fires but histogram < 2bps, the cross is remembered for up to 2 bars. If histogram strengthens above 2bps while MACD stays bullish, entry fires. If MACD turns bearish or the window expires, the cross is cancelled.

Grid-searched 17 parameter combinations (bps x window) using full real backtests across 7 years. Optimal: `min_cross_hist_bps=2.0`, `cross_confirm_window=2`.

| Year | Market | v8 | v9 | Delta |
|------|--------|-----|-----|-------|
| 2020 | Covid+Bull | +447% | +556% | **+109pt** |
| 2021 | Mega Bull | +526% | +441% | -85pt |
| 2022 | Bear | +99% | +75% | -24pt |
| 2023 | Recovery | +153% | +153% | 0pt |
| 2024 | Full Bull | +262% | +343% | **+81pt** |
| 2025 | Correction | +75% | +62% | -13pt |
| 2026 | Bear (partial) | +12% | +24% | **+12pt** |
| **Total** | | **+1574%** | **+1654%** | **+80pt** |

### v10: MACD Death Cross on Re-entries (current)

Investigated a losing trade in 2025 (Jan 17-27) where a trend continuation re-entry bought into a downturn and held for 10 days to a loss. Root cause: the MACD death cross exit was globally disabled (v3 decision — too noisy on 1H bars, 54% win rate), so the only exits were stop-loss/trailing stop (8%, too wide) and RSI overbought reversal (never reached 70).

Hypothesis: re-entry trades are more speculative than fresh MACD-cross entries, so they should have a tighter leash. Tested enabling MACD death cross exclusively for re-entry positions, with a minimum gap threshold to filter noisy crosses.

Tested 5 variants across 5 years (2020-2023, 2025):

| Variant | Description | 2020 | 2021 | 2022 | 2023 | 2025 |
|---------|-------------|------|------|------|------|------|
| Baseline (v9) | No MACD exit | +555% | +441% | +75% | +153% | +62% |
| Any cross | Unfiltered death cross on re-entries | +629% | +564% | +78% | +90% | +42% |
| In-loss only | Cross only when trade is losing | +472% | +419% | +83% | +90% | +47% |
| **2bps gap** | Cross + gap ≥ 2bps of price | **+675%** | **+540%** | **+99%** | +129% | **+85%** |
| 3bps gap | Cross + gap ≥ 3bps of price | +803% | +508% | +99% | +119% | +80% |
| 2bps + 2-bar window | Cross persists 2 bars for gap check | +598% | +556% | +79% | +92% | +44% |

The **2bps instant threshold** won: beats baseline in 4/5 years, with the best improvement in the most recent year (2025: +62% → +85%). The threshold filters noisy crosses while catching strong divergences that signal real trend reversals.

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

### OBV (On-Balance Volume)

Cumulative volume indicator that adds volume on up bars and subtracts on down bars. When OBV falls below its 20-period EMA, it signals net selling pressure — institutional money is flowing out. Used as a short entry confirmation: only short when OBV confirms bearish flow.

## Entry Rules

### Standard Entry

All must be true simultaneously:

1. **MACD golden cross** — MACD line crosses above signal line (fresh crossover), or a cross occurred within the last `cross_confirm_window` bars and MACD remains bullish
2. **Histogram strength** — MACD histogram >= `min_cross_hist_bps` basis points of price (filters dead-cat bounces; weak crosses are delayed up to `cross_confirm_window` bars rather than blocked)
3. **RSI in momentum zone** — RSI between 40 and 70
4. **Trend is strong** — ADX > 20
5. **Long-term uptrend** — price > EMA-200
6. **Cooldown elapsed** — at least 4 bars since last exit

### Trend Continuation Re-entry

After a profitable exit, relaxed conditions apply (shorter cooldown, no fresh MACD cross needed):

1. **MACD bullish** — MACD > signal (already above, no crossover needed)
2. **RSI in momentum zone** — RSI between 40 and 70
3. **Trend is strong** — ADX > 20
4. **Long-term uptrend** — price > EMA-200
5. **Cooldown elapsed** — at least 2 bars since last exit

## Long Exit Rules

Any one triggers a sell:

1. **Overbought reversal** — RSI was above 70 and drops below 65, AND position is in profit (100% win rate across 6 years). Won't fire at a loss — that's what stops are for.
2. **Stop-loss** — price < entry - max(3.0 x ATR, 8% x entry price)
3. **Trailing stop** — price < peak - max(3.0 x ATR, 8% x peak price)
4. **MACD death cross (re-entry only)** — when MACD crosses below signal with a gap ≥ `reentry_macd_exit_bps` (default 2bps of price). Only active for trend continuation re-entry positions; standard entries are not affected. The bps threshold filters noisy crosses.
5. **MACD death cross (global, optional, off by default)** — if `exit_on_macd_cross` is true, any MACD death cross triggers exit regardless of entry type or gap size. Disabled by default (too noisy on 1H bars).

## Short Entry Rules

All must be true simultaneously:

1. **MACD bearish** — MACD line < signal line (already bearish, no fresh crossover needed)
2. **RSI not extreme** — RSI <= 60
3. **Strong trend** — ADX >= 25 (higher bar than long entry)
4. **Long-term downtrend** — price < EMA-200
5. **OBV bearish** — OBV below its 20-period EMA (institutional selling pressure)
6. **MACD histogram declining** — histogram more negative than previous bar (accelerating bearish momentum)
7. **Cooldown elapsed** — at least 4 bars since last exit

Position sized at `short_size_pct` (50%) of cash, not full allocation, to limit risk.

## Short Exit Rules

Any one triggers a cover:

1. **Oversold reversal** — RSI was below 30 and bounces above 35, AND short is in profit
2. **Stop-loss** — price > entry + max(3.0 x ATR, 6% x entry price)
3. **Trailing stop** — price > trough + max(3.0 x ATR, 6% x trough price)
4. **MACD golden cross** — MACD crosses above signal, cover unconditionally

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
| `exit_on_macd_cross` | false | Whether MACD death cross triggers exit (all positions) |
| `reentry_macd_exit_bps` | 2.0 | Min MACD-signal gap in bps to trigger death cross exit on re-entry trades (0 = any cross) |
| `trend_reentry` | true | Enable relaxed re-entry after profitable exits |
| `trend_reentry_cooldown` | 2 | Cooldown bars for trend continuation re-entry |
| `trend_reentry_rsi_max` | 70 | Max RSI for trend continuation re-entry |
| `enable_short` | false | Enable short selling |
| `short_adx_threshold` | 25 | Minimum ADX for short entry (higher bar than longs) |
| `short_rsi_entry_high` | 60 | Maximum RSI for short entry |
| `short_rsi_oversold` | 30 | RSI oversold threshold for short exit |
| `short_rsi_exit_confirm` | 35 | RSI must rise above this to confirm oversold reversal |
| `short_stop_loss_pct` | 6.0 | Minimum short stop-loss distance as % of entry price |
| `short_trailing_stop_pct` | 6.0 | Minimum short trailing stop distance as % of trough price |
| `short_size_pct` | 50 | % of cash to allocate to each short position |
| `min_cross_hist_bps` | 2.0 | Minimum MACD histogram at golden cross in basis points of price (0 = disabled) |
| `cross_confirm_window` | 2 | Bars to wait for histogram to strengthen after a weak golden cross |
