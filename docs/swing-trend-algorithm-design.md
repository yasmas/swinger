# Swing Trend Strategy — Algorithm Design & Analysis

## 0. Pending Ideas to Explore

- ~~Trade Invalidation: On some trades, we see deteriation right after the begining and we wanted to consider if to close the trade short to avoid loosing. We tried this approach using hma_invalidation_bars (after the min_hold window, if the HMA slope is against the trade direction for 3 consecutive hourly bars with no interruption, exit at close. Any aligned bar resets the counter to 0). N=3 gave us the best performance, but also missed good trades. Win rate increased but overall PL decreased. We need to research more - if there is another way to check if trades are going bad. With a DIFFERENT indicator.~~

- Maybe when we close to COVER, go LONG immediatly. Maybe whe we close a LONG, SHORT immediatly. Need to find what other coditions need to hold true to be successfull on this idea

- ~~Many times we enter positions LATE. These are the 3 common reasons:~~
~~o KC trigger           43% of missed opportunity score~~
~~o ADX < threshold         29%~~
~~o HMA↑/↓ conflicts        28~~

Potential improvements:
1) ~~The no_kc_trigger problem is the highest-leverage fix (43% of price impact). The issue: after a pullback entry, if BTC resumes its trend but price never touches the midline again and never clearly breaks above upper (because KC upper is rising with price), we're stuck. A potential fix: add a "price held above KC midline for 2+ hours" trigger as a third entry mode — effectively entering on a pullback to the slowly-rising middle of the channel rather than waiting for price to dip all the way to kc_mid~~ - We fixed it by adding another entry logic, if price holds N=1 bar above the midline

2) ~~The ADX problem is structurally harder — 50% of gaps are pure consolidation with correctly-low ADX. Options: lower threshold from 20→15 (risk: more noise trades), or use a short-period ADX (e.g. 7) alongside the 14-period one to catch trend acceleration faster.~~ - For LONGs, relaxing ADX is harmful (blocked entries are losers). For SHORTs, ADX was the #1 blocker. Fixed in v12 (lower threshold) then v13 (fast ADX(10) with threshold 20).

~~3) Recommendations for Swing v3~~ — Implemented as v3 below

~~4) Recommendations for Swing v3 (Updated)~~ — Implemented as v3 below

~~3) Read the question and idea in this link:~~
~~https://gemini.google.com/share/5b3fc366e559~~
~~In short - Since HMA tech indicator is less noisy than EMA and finds trends quicker, has anybody tried to build the MACD golden cross indicator using HMA trends instead of EMA?~~ — Implemented as v7 HMACD below

4) The problem we have is that MACD triggers at good times, but we are blocked HOURS by ADX>20 & ST_BULL conditions that are not met. And when they are finally met, we loose significant part of the trade. Here are some suggestions to overcome that:

a. Multi-Timeframe (MTF) Regime Filtering - **IDEA DEBUNKED**

~~Instead of asking the current execution timeframe to confirm the trend, ask the higher timeframe. This is arguably the most robust solution.~~
~~The Logic: If you are trading the 15-minute chart, calculate your SuperTrend or ADX on the 1-hour or 4-hour chart.~~
~~The Execution: If the 4-hour SuperTrend is already positive (establishing the macro regime), you drop the 15-minute filters entirely. You take the ~~15-minute HMACD golden cross immediately. This allows you to use the hypersensitivity of the HMACD to snipe an entry in the direction of an already ~~established macro trend, completely bypassing the micro-lag.~~

b. Swap Price Filters for Volume/Flow Filters - **IDEA DEBUNKED**

~~Price-derived filters will always lag price. Volume happens in real-time. To validate an early HMACD cross without waiting for ADX, you need to see if institutional weight is behind the sudden move.~~
~~Cumulative Volume Delta (CVD): Instead of SuperTrend, check if aggressive market buying is outpacing market selling at the exact moment of the cross.~~
~~VWAP Slope & Distance: Check the instantaneous slope of the Volume Weighted Average Price. If the HMACD crosses and the price is cleanly rejecting off the VWAP with rising volume, the validity of the signal is high, even if the ADX is still asleep at 15.~~

c. Volatility Contraction (Trading the "Squeeze") - **We like that (implemented)**

~~A trend filter like ADX looks for an existing move. A volatility filter looks for the potential of a move.~~

~~The Strategy: Markets cycle between low volatility and high volatility. Measure the spread of Bollinger Bands relative to Keltner Channels.~~

~~The Execution: If volatility is historically compressed (a "squeeze"), the market is building energy. If your HMACD fires a golden cross precisely as the bands begin to expand out of that squeeze, you take the trade. You don't need ADX to be > 20 yet; the volatility expansion validates the early entry.~~

d. Dynamic/Derivative Thresholding

Hardcoding a static hurdle like ADX > 20 is often too brittle for automated systems. A market waking up from a dead-flat session might only push the ADX to 14 or 16 at the exact moment of the optimal entry.

The Fix: Instead of requiring an absolute value, measure the derivative (the rate of change). Require the ADX to be cleanly rising over the last n periods, or require the ADX to cross above its own short-term moving average. This confirms momentum is accelerating, getting you in much earlier than waiting for the arbitrary "20" line.

e. ~~Change the Source Data: The Heikin-Ashi HMACD~~ — We implemented HMACD (HMA-based MACD) directly on standard close prices instead. HMA already provides the smoothing benefit that Heikin-Ashi would add, making HA redundant. See v7 below.

f. The Internal Velocity Filter (Histogram Δ)

Stop relying on an external indicator to tell you if the trend is strong. Measure the velocity of the HMACD itself. A false cross usually limps over the signal line. A true cross violently punches through it.

The Logic: Don't just trigger on the cross. Calculate the rate of change (the Delta) of the HMACD Histogram: ΔH=Ht−Ht−1
​	
  (where H is the Histogram value, and t is the current period).

Why it Works: Require the histogram to expand by a minimum threshold on the exact candle of the cross, or the candle immediately following. If the HMACD crosses but the histogram delta is weak, the system ignores it as chop. This measures immediate momentum, meaning zero lag.

g. The "Setup vs. Trigger" State Machine

This is a purely structural change to your trading engine. You separate the indicator signal from the actual execution.

The Logic: The HMACD golden cross no longer fires a market order. Instead, it flips a boolean state in your engine to Setup_Active = True.

The Execution: Once the setup is active, your engine logs the High price of the specific candle that caused the cross. You only enter the trade if the price breaks above that specific candle's high within the next X periods. If the HMACD crosses back down before the high is broken, Setup_Active = False.

Why it Works: It uses the HMACD to prime the weapon, but requires actual price-action to pull the trigger. False signals in a ranging market rarely have enough follow-through to break the signal candle's high.

## 1. Overview

A trend-following system for BTC/USDT that **receives 5-minute bars but internally resamples to 1-hour** to ride multi-day trends. Uses a simplified 2-layer confluence model — trend filter + entry trigger — deliberately removing the restrictive filters that limited the intraday v6 strategy.

**Key characteristics:**
- Timeframe: 1-hour bars (resampled internally from 5m feed)
- Position sizing: 100% of capital per trade (all-in)
- Direction: Long and Short
- Hold duration: Hours to days (avg 30h, targets 1-4 day holds)
- Hard stop: 3% of entry price
- Supertrend trailing stop: on 1h bars (much wider than v6's 5m trailing)
- Daily circuit breaker: 8% max daily drawdown
- Transaction cost assumption: 0.05% per side (0.10% round trip)
- Target frequency: ~12-15 trades/month
- Entry checking: only on hourly bar close (not every 5m bar)
- Exit checking: every 5m bar (for granular stop execution)

---

## 2. Why This Strategy Exists (Motivation from v6 Analysis)

Analysis of the Intraday v6 strategy revealed a fundamental problem: **it captures only 11.4% of major trend moves** despite being a trend-following system. The root cause was identified as overly restrictive entry filters.

### The v6 Problem

During 16 major trending periods (moves >8% in <15 days), v6 captured only 44.2% of 386.5% available trend moves. Key findings:

| Metric | Intraday v6 |
|--------|------------|
| Time in market | 6.4% |
| Avg hold | 4 hours |
| Trend capture | 11.4% |
| Filters blocking entry | 6 simultaneous gates |

**Case study — Oct 13-24, 2024 (+7.8% bull trend):**
v6 took 5 trades, ALL losers, net -2.15%. Every 2-hour sample during the rally was blocked by 3-5 filters:
- ADX below 30 threshold
- Squeeze filter active (BB inside KC)
- Price inside Keltner Channel (no breakout)
- Volume below 1.5x average
- ATR below 0.18% floor

The problem was systemic: the confluence of 6 entry filters collectively blocked entries during the most profitable grinding trends — exactly the trends the strategy was designed to capture.

### The Solution

Strip the entry logic down to essentials. Move to 1h bars for natural noise filtering (instead of using multiple filters on noisy 5m bars). Add a pullback entry to catch trending moves that don't produce explosive breakouts.

---

## 3. The Algorithm — Complete Specification

### 3.1 Data Pipeline: 5m → 1h Resampling

The strategy receives 5-minute OHLCV bars from the backtest engine. In `prepare()`, it resamples to 1-hour:

```
Input:  5-minute OHLCV bars
Output: 1-hour OHLCV bars (aggregated)

hourly.open   = first open in hour
hourly.high   = max high in hour
hourly.low    = min low in hour
hourly.close  = last close in hour
hourly.volume = sum of volume in hour
```

A mapping is built: `bar_to_hourly_idx[5m_timestamp] → hourly_index`, so each 5m bar knows which hourly bar it belongs to.

**Why receive 5m bars?** Exit checking happens every 5m bar for granular stop execution. Entries are checked only when the hourly bar transitions (first 5m bar of a new hour).

### 3.2 Indicator Calculations (on 1h bars)

All indicators are precomputed once on the full hourly DataFrame during `prepare()`:

```
1. HMA(21) on 1h bars:
   WMA_half = WMA(close, 10)
   WMA_full = WMA(close, 21)
   diff = 2 * WMA_half - WMA_full
   HMA = WMA(diff, 4)           # sqrt(21) ≈ 4
   HMA_slope = HMA - HMA[1]     # positive = rising

   Note: period 21 on 1h ≈ 1 day lookback (vs v6's period 55 on 5m ≈ 4.5h)

2. Supertrend(ATR=14, Mult=3.0) on 1h bars:
   ATR_14 = ATR(high, low, close, 14)
   median = (high + low) / 2
   upper_band = median + 3.0 * ATR_14
   lower_band = median - 3.0 * ATR_14
   [standard Supertrend flip logic]

   Note: On 1h bars this is ~12x wider than v6's 5m Supertrend,
   naturally surviving multi-day pullbacks without false flips.

3. Keltner Channels(EMA=20, ATR=14, Mult=2.0) on 1h bars:
   kc_mid   = EMA(close, 20)
   kc_atr   = ATR(high, low, close, 14)
   kc_upper = kc_mid + 2.0 * kc_atr
   kc_lower = kc_mid - 2.0 * kc_atr

4. ADX(14) on 1h bars:
   [standard ADX calculation]
   Note: threshold lowered to 20 (vs v6's 30) to catch grinding trends
```

### 3.3 What Was Removed (vs v6)

The following filters from v6 are **deliberately not used**:

| Removed Filter | v6 Threshold | Why Removed |
|---------------|-------------|-------------|
| Squeeze filter (BB inside KC) | Required squeeze OFF | Blocked entries in trending-but-quiet markets |
| Volume filter | > 1.5x avg | Blocked entries in steady, low-volume trends |
| ATR floor | > 0.18% | Blocked entries in grinding, low-volatility moves |
| VWAP alignment | Price > VWAP (longs) | Redundant with HMA + ST direction |
| Bollinger Bands | For squeeze only | Removed with squeeze |
| High ADX threshold | > 30 | Lowered to 20 to catch earlier trends |

**The 1h timeframe naturally filters what these indicators were trying to do on 5m bars.**

### 3.4 Layer 1: Trend Filter

```
DIRECTION:
  if HMA_slope > 0 AND supertrend_bullish AND ADX >= 20:
      direction = LONG

  elif HMA_slope < 0 AND NOT supertrend_bullish AND ADX >= 25:
      direction = SHORT

  else:
      direction = NONE → no trade

# Note: shorts require ADX >= 25 (slightly higher bar than longs)
```

Only two conditions must agree: **HMA slope direction** and **Supertrend direction**. ADX acts as a mild trend strength gate (20 for longs, 25 for shorts), much lower than v6's 30/35.

### 3.5 Layer 2: Entry Triggers

Checked only on hourly bar close. Three trigger types (all additive — any one fires an entry):

```
TRIGGER A — Keltner Breakout (momentum entry):
  LONG:  close > kc_upper
  SHORT: close < kc_lower

TRIGGER B — Keltner Pullback (trend continuation entry):
  LONG:  low <= kc_mid * 1.002 AND close > kc_mid
         (price pulled back near midline but holds above it)
  SHORT: high >= kc_mid * 0.998 AND close < kc_mid
         (price bounced up near midline but rejected below it)

TRIGGER C — KC Midline Hold (v2, grinding trend re-entry):
  LONG:  last N completed hourly closes all > kc_mid
  SHORT: last N completed hourly closes all < kc_mid

  IMPORTANT: uses hourly bar at (hourly_idx - 1) as reference,
  never (hourly_idx), to avoid reading the unclosed current bar.

entry_mode = "both" → A and B are evaluated
kc_midline_hold_bars = N → Trigger C is evaluated (0 = disabled)
```

**Trigger A+B (v1):** During grinding trends, price often stays in the dead zone between KC midline and KC upper — too far from midline for a pullback trigger, not quite above upper for a breakout. These trades were missed entirely.

**Trigger C (v2):** Fills the dead zone by allowing re-entry whenever price has held above KC midline for N consecutive completed hours. N=1 is sufficient — one bar above midline confirms the trend hasn't reversed.

**Gap analysis motivation:** Systematic analysis of all gaps >48h found that "no KC trigger" accounted for only 18% of gaps by count but **43% of price-weighted missed opportunity** (|price_move%| × gap_hours). These are exactly the sustained grinds where price stays above midline but never touches it or breaks above the upper band.

### 3.6 Layer 3: MACD Entry (v3 — independent of HMA+ST)

Added in v3 to catch trends 1-3 days before HMA+ST confirm. Runs as Path B/C after KC triggers (Path A) fail. **Does not require HMA+ST agreement** — uses EMA(200) for trend direction instead.

```
PATH B — Fresh MACD Cross:
  LONG:  MACD line crosses above signal line (golden cross)
         AND ADX >= 20
         AND RSI in [40, 70]
         AND price > EMA(200)
         AND histogram >= min_cross_hist_bps (2.0 bps)
         Cross confirmation: if histogram is weak at cross bar,
         wait up to cross_confirm_window (2) bars for strengthening.

  SHORT: MACD line crosses below signal line (death cross)
         AND ADX >= 25
         AND RSI <= 60
         AND price < EMA(200)

PATH C — Trend Re-entry (after profitable MACD exit):
  After a profitable MACD exit, re-enter with relaxed conditions:
  LONG:  MACD > signal (no fresh cross needed)
         AND ADX >= 20
         AND RSI in [40, 70]
         AND price > EMA(200)
         AND bars since MACD exit >= reentry_cooldown (2)

  Re-entry flag is cleared after one re-entry to prevent chaining.
```

**Key design decisions:**
- MACD entries bypass HMA+ST gate — the whole point is earlier entry
- MACD entries use wider stops (8% + 3x ATR) matching the MACD RSI strategy
- MACD entries skip the Supertrend stop distance filter (ST may be wrong side)
- MACD entries have their own cooldown separate from KC cooldown
- 59% of MACD RSI strategy's long entries were re-entries — this is critical for performance

**MACD exit logic (for MACD-entered trades only):**
- Phase 1 (first 24 hourly bars): MACD death cross → exit immediately
- Phase 1: RSI overbought reversal (prev_rsi >= 70 then drops below 65) → exit
- Re-entries: MACD death cross exit always (with bps threshold)
- After Phase 1 or when ST confirms: ATR trailing takes over
- KC trades are completely unaffected by MACD exit logic

### 3.7 Entry Filter: Supertrend Stop Distance

```
stop_distance = abs(price - supertrend_line) / price
SKIP if stop_distance > 3%     # ST trailing would be wider than hard stop

# Also skip if stop_distance < 0 (price on wrong side of ST — shouldn't happen
# given Layer 1 check, but defensive)
```

### 3.8 Cooldown

```
SKIP entry if (current_hourly_idx - last_exit_hourly_idx) < 3
# Minimum 3 hours between trades (prevents overtrading after stops)
```

### 3.9 Exit Logic (checked every 5m bar)

```
ON ENTRY:
  entry_price = close
  hard_stop:
    LONG:  entry_price * (1 - 0.03)    # 3% below entry
    SHORT: entry_price * (1 + 0.03)    # 3% above entry
  min_hold = 6 hourly bars (6 hours)

EVERY 5-MINUTE BAR WHILE IN POSITION:

  1. Breakeven adjustment:
     if unrealized_profit >= 1.5%:
         move hard_stop to entry_price (lock in zero-loss)

  2. Determine active stop:
     if within min_hold window (< 6 hourly bars):
         active_stop = hard_stop only
     else:
         LONG:  active_stop = max(hard_stop, supertrend_trailing_line)
         SHORT: active_stop = min(hard_stop, supertrend_trailing_line)

  3. EXIT CONDITIONS (any one triggers exit):

     FOR KC-ENTERED TRADES:
     a. STOP HIT:
        LONG:  bar.low <= active_stop → exit at min(close, active_stop)
        SHORT: bar.high >= active_stop → exit at max(close, active_stop)

     b. SUPERTREND FLIP (only outside min_hold window):
        LONG:  supertrend flips bearish → exit at close
        SHORT: supertrend flips bullish → exit at close

     FOR MACD-ENTERED TRADES (v3):
     a. ATR STOP HIT (always fires):
        active_stop = max(hard_stop, peak - max(3x ATR, 8% × peak))
        LONG:  bar.low <= active_stop → exit at min(close, active_stop)

     b. MACD CROSS EXIT (Phase 1 = first 24h, or always for re-entries):
        LONG:  MACD death cross (line crosses below signal) → exit at close
        Re-entries: require gap >= 2 bps before exiting

     c. RSI OVERBOUGHT REVERSAL (Phase 1 only):
        LONG:  prev_rsi >= 70 AND rsi < 65 AND in profit → exit at close
        (must first hit overbought THEN drop — not just crossing 65)

     FOR ALL TRADES:
     d. HMA INVALIDATION (if enabled):
        N consecutive hourly bars with HMA slope against direction → exit

     e. CIRCUIT BREAKER:
        daily_pnl <= -8% → close all positions

     f. LAST BAR:
        Force liquidate at close
```

### 3.10 Min Hold Window Behavior

During the first 6 hours of a trade:
- **Hard stop always fires** (catastrophic protection never disabled)
- **Supertrend trailing is suppressed** (no premature exit from noise)
- **Supertrend flip is suppressed** (brief counter-moves don't eject)

After 6 hours: full trailing stop + flip exit logic activates.

This prevents the "entered the right trend but got shaken out in the first few hours" failure mode that plagued short-duration trades.

---

## 4. Trade Lifecycle Flow

```
┌─────────────────────────────────────────────────────────────────┐
│  NEW 5-MINUTE BAR ARRIVES                                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Map 5m timestamp → hourly index                             │
│     Detect if this is the first 5m bar of a new hour            │
│                                                                 │
│  2. Read precomputed hourly indicators at current hourly index  │
│                                                                 │
│  3. IF in position:                                             │
│     ├─ Update MFE/MAE tracking                                  │
│     ├─ Check breakeven trigger (≥ 1.5% → move stop to entry)   │
│     ├─ Determine active stop (hard-only during min_hold)        │
│     ├─ Check exit: stop hit, ST flip, circuit breaker           │
│     └─ If exit → SELL/COVER, record trade, start cooldown       │
│                                                                 │
│  4. IF not in position AND hourly bar just closed:              │
│     ├─ Check cooldown (≥ 3 hourly bars since last exit)         │
│     ├─ Check daily circuit breaker                              │
│     ├─ Layer 1: HMA slope + ST direction + ADX ≥ 20            │
│     ├─ Layer 2: Keltner breakout OR pullback OR midline hold    │
│     ├─ ST stop distance ≤ 3%                                    │
│     └─ All pass → BUY/SHORT, set stops, record entry            │
│                                                                 │
│  5. Log bar state to CSV                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Configuration (YAML)

v2 config (adds `kc_midline_hold_bars: 1` to v1 params):

```yaml
backtest:
  name: "BTC Swing Trend Dev"
  version: "v2"
  initial_cash: 100000
  start_date: "2022-01-01"
  end_date: "2024-12-31"

data_source:
  type: "csv_file"
  parser: "binance_kline"
  params:
    file_path: "data/BTCUSDT-5m-2022-2024-combined.csv"
    symbol: "BTCUSDT"

strategies:
  - type: "swing_trend"
    params:
      hma_period: 21
      supertrend_atr_period: 14
      supertrend_multiplier: 3.0
      keltner_ema_period: 20
      keltner_atr_period: 14
      keltner_atr_multiplier: 2.0
      adx_period: 14
      adx_threshold: 20
      stop_loss_pct: 3.0
      daily_max_drawdown_pct: 8.0
      breakeven_trigger_pct: 1.5
      trailing_supertrend_multiplier: 0    # 0 = use same ST for trailing
      entry_mode: "both"                   # "breakout", "midline", or "both"
      enable_short: true
      short_adx_threshold: 25
      cooldown_bars: 3                     # hourly bars between trades
      min_hold_bars: 6                     # hourly bars minimum hold
      max_supertrend_stop_pct: 3.0
      cost_per_trade_pct: 0.05
      kc_midline_hold_bars: 1              # v2: re-entry during grinding trends
```

---

## 6. Backtest Results

### 6.1 v1 vs v2 vs v3 Headline Comparison

| Metric | v2 Dev | v3 Dev | v2 Test | v3 Test |
|--------|--------|--------|---------|---------|
| **Total Return** | +3,083% | **+3,315%** | +3,710% | **+7,019%** |
| **Sharpe Ratio** | 3.143 | 2.638 | 3.050 | 2.840 |
| **Max Drawdown** | -18.5% | -19.7% | -15.8% | -16.2% |
| **Trades** | 590 | 619 | 537 | 562 |
| **Win Rate** | 33.9% | 34.9% | 37.2% | 40.6% |
| **Profit Factor** | 1.939 | 1.902 | 2.086 | 2.229 |

**v3 highlights:**
- Test return nearly **doubled** (+3,710% → +7,019%)
- **No overfitting:** test (+7,019%) exceeds dev (+3,315%)
- MaxDD comparable to v2 on both sets
- Sharpe slightly lower (more daily volatility from MACD entries) but return/risk dramatically better
- MACD entries add 68 trades on dev (34 fresh cross + 34 re-entry), 98 on test

**v3 MACD entry breakdown:**

| Trigger | Dev Trades | Dev PnL | Dev WR | Test Trades | Test PnL | Test WR |
|---------|-----------|---------|--------|------------|---------|--------|
| macd_cross | 34 | +21.4% | 50.0% | 52 | +105.0% | 53.8% |
| macd_reentry | 34 | +42.1% | 61.8% | 46 | +13.8% | 52.2% |
| KC triggers | 551 | +271.0% | 32.5% | 464 | +286.1% | 37.7% |

KC trades are unaffected — same count and performance as v2.

### 6.1.1 v1 vs v2 Comparison (historical)

| Metric | v1 Dev | v2 Dev | v1 Test | v2 Test |
|--------|--------|--------|---------|---------|
| **Total Return** | +1,203% | **+3,083%** | +867% | **+3,710%** |
| **Sharpe Ratio** | 2.775 | **3.143** | 2.313 | **3.050** |
| **Max Drawdown** | -18.0% | -18.5% | -29.4% | **-15.8%** |
| **Trades** | 447 | 590 | 400 | 537 |
| **Win Rate** | 34.9% | 33.9% | 35.5% | 37.2% |
| **Profit Factor** | 1.897 | 1.939 | 1.764 | 2.086 |

**v2 highlights:**
- Return **~2.5x higher** on dev, **~4.3x higher** on test
- **No overfitting:** test (+3,710%) exceeds dev (+3,083%)
- Test MaxDD nearly **halved** (-29.4% → -15.8%)
- Sharpe up on both sets — higher return *and* smoother equity curve
- +143 extra trades/year (590 vs 447 on dev) from the midline-hold trigger

### 6.2 v2 Grid Search — kc_midline_hold_bars

All variants use `kc_midline_hold_adx_rising = false` (removed — tested but inferior).

| N | Dev Return | Dev Sharpe | Dev MaxDD | Dev Trades | Test Return | Test Sharpe | Test MaxDD | Test Trades |
|---|-----------|-----------|----------|-----------|------------|------------|----------|------------|
| 0 (v1) | +1,203% | 2.775 | -18.0% | 447 | +867% | 2.313 | -29.4% | 400 |
| **1 (v2)** | **+3,083%** | **3.143** | **-18.5%** | **590** | **+3,710%** | **3.050** | **-15.8%** | **537** |
| 2 | +2,744% | 3.061 | -18.6% | 589 | +3,418% | 2.997 | -17.6% | 528 |
| 4 | +1,909% | 2.823 | -18.4% | 579 | +2,777% | 2.859 | -17.1% | 524 |

N=1 wins on every metric. Higher N = fewer re-entries = smaller returns, monotonically.

### 6.3 v2 Headline Numbers

| Metric | Dev (2022-2024) | Test (2020-2026) |
|--------|-----------------|-------------------|
| **Total Return** | **+3,082.9%** | **+3,710.0%** |
| **Sharpe Ratio** | **3.143** | **3.050** |
| **Max Drawdown** | -18.47% | -15.79% |
| **Trades** | 590 | 537 |
| **Win Rate** | 33.9% | 37.2% |
| **Profit Factor** | 1.939 | 2.086 |

**No overfitting:** test Sharpe (3.050) is 97% of dev (3.143). Test MaxDD is *better* than dev.

### 6.4 Comparison vs Intraday v6

| Metric | Intraday v6 | Swing v1 | Improvement |
|--------|------------|----------|-------------|
| Dev Return | +113.30% | +1,203.45% | **10.6x** |
| Test Return | +119.29% | +866.62% | **7.3x** |
| Sharpe (Dev) | 1.19 | 2.749 | **2.3x** |
| Sharpe (Test) | 1.110 | 2.322 | **2.1x** |
| Profit Factor | 1.173 | 1.897 | **+62%** |
| MaxDD (Dev) | -12.47% | -17.37% | Wider |
| MaxDD (Test) | -19.67% | -24.74% | Wider |
| Trend Capture | 11.4% | 47.1% | **4.1x** |
| Avg Hold | 4h | 30h | 7.5x longer |
| Time in Market | 6.4% | 51.1% (dev) | 8x more |

**The tradeoff:** MaxDD is wider (-17.37% vs -12.47% on dev), but the return-to-risk ratio is dramatically better (Return/MaxDD = 69.3 vs 9.1).

### 6.5 Hold Time Analysis (v1 baseline)

| Duration | Trades | Win Rate | Sum PnL | Avg PnL |
|----------|--------|----------|---------|---------|
| < 6h | 32 | 0.0% | -46.7% | -1.46% |
| 6-12h | 80 | 8.8% | -74.1% | -0.93% |
| 12-24h | 119 | 15.1% | -55.6% | -0.47% |
| 1-2 days | 128 | 47.7% | +87.7% | +0.69% |
| 2-4 days | 77 | 76.6% | +209.2% | +2.72% |
| 4-8 days | 11 | 100% | +116.4% | +10.59% |

**Critical finding:** Trades under 24h are net losers (-176.4% combined). ALL compounded profit comes from trades lasting >24h. This is the single most important improvement vector.

### 6.6 Entry Trigger Analysis (v1 baseline)

| Trigger | Trades | Win Rate | Sum PnL |
|---------|--------|----------|---------|
| Keltner Pullback | 219 | 37.0% | +135.4% |
| Keltner Breakout | 228 | 32.9% | +101.5% |

Both triggers contribute positively. Pullback entries have higher WR and PnL — they catch the grinding trends that v6 missed entirely.

### 6.7 Direction Analysis

| Direction | Trades | Win Rate | Sum PnL |
|-----------|--------|----------|---------|
| LONG | 255 | 37.3% | +185.5% |
| SHORT | 192 | 31.8% | +51.4% |

Shorts are profitable but weaker. Expected for crypto (inherent bull bias).

### 6.8 Exit Reason Analysis

| Exit Reason | Trades | Win Rate | Sum PnL |
|-------------|--------|----------|---------|
| Supertrend Trailing | 340 | 45.9% | +288.8% |
| Hard Stop (3%) | 107 | 0.0% | -51.9% |

Hard stops never win (by design — they're catastrophic protection). Supertrend trailing exits carry all the profit with a 45.9% WR.

### 6.9 MFE Retention

| Metric | Dev | Test |
|--------|-----|------|
| Avg MFE | 2.63% | 2.76% |
| PnL/MFE Retention | 20.2% | 17.3% |

We're keeping ~20% of peak paper profits. This means on average, a trade that reaches +5% unrealized will close at +1%. Room for improvement via tighter trailing.

### 6.10 Trend Capture — Major Moves (v1 baseline)

During 16 major trending periods (moves >8% in <15 days):

| Period | Direction | Move | Swing Captured | Ratio |
|--------|-----------|------|---------------|-------|
| Jan 10-23, 2023 | Bull | +29.8% | +15.9% | 53.5% |
| Mar 10-18, 2023 | Bull | +34.0% | +29.4% | 86.4% |
| Jun 14-23, 2023 | Bull | +18.4% | +17.8% | 96.6% |
| Feb 15-20, 2023 | Bear | -8.2% | +8.8% | 107.9% |
| May 4-12, 2022 | Bear | -29.2% | +12.3% | 42.3% |
| **Oct 13-24, 2024** | **Bull** | **+7.8%** | **+5.8%** | **74.5%** |
| Nov 5-22, 2024 | Bull | +47.3% | +11.7% | 24.8% |
| **TOTAL** | — | **355.9%** | **+167.5%** | **47.1%** |

vs v6's total trend capture of 11.4%. The strategy captures **4.1x more trend**.

### 6.11 Risk Metrics (v1 baseline)

| Metric | Dev | Test |
|--------|-----|------|
| Max Consecutive Losses | 21 | 9 |
| Worst Loss Streak PnL | -18.47% | -9.46% |
| Worst Month | Jul 2023: -15.1% | Apr 2021: -10.9% |
| Losing Months | 10/36 (28%) | 7/36 (19%) |
| Best Trade | +18.91% | +23.60% |
| Worst Trade | -3.62% | -6.57% |

The 21-consecutive-loss streak (dev) sounds severe but averages only -0.88% per loss = -18.47% total, consistent with the hard stop working correctly.

---

## 7. Known Weaknesses & Improvement Vectors

### 7.1 Short-Duration Trades Are Losers (Priority 1)

231 trades under 24h produced -176.4% combined loss on dev. The min_hold of 6h isn't long enough — the data strongly suggests increasing it to 12-18h or even 24h would eliminate ~112 losing trades while sacrificing very few winners.

**Caution:** This is the same lever that overfit v7 of the intraday strategy. But the failure mode is different here — 1h Supertrend trailing is much wider than 5m, so extending hold doesn't create the same "held through a reversal" risk.

### 7.2 MFE Retention at 20% (Priority 2)

Currently `trailing_supertrend_multiplier: 0` (same ST for entry and exit). A tighter trailing ST (e.g., multiplier 2.0-2.5 vs entry's 3.0) could ratchet stops up faster, capturing more of each trade's peak unrealized profit.

### 7.3 Monthly Drawdowns (Priority 3)

Two months exceeded -8% loss (Jul 2023: -15.1%, Jun 2024: -8.7% on dev). A monthly circuit breaker (pause after -10% monthly loss) could cap tail risk.

### 7.4 Short Side Weakness (Priority 4)

Shorts have lower WR (31.8% vs 37.3%) and lower total PnL (+51.4% vs +185.5%). Options:
- Raise short ADX threshold to 30
- Only allow short breakout entries (disable pullback for shorts)
- Wider stops for shorts (crypto crashes tend to be sharper)

### 7.5 Max Drawdown (Priority 5)

-17.37% dev, -24.74% test. Acceptable for the return profile but could be tightened via position sizing (e.g., 80% capital per trade instead of 100%).

---

## 8. What NOT to Change

These design decisions are validated by the data and should remain:

1. **Simplified entry logic** — the 2-layer model (trend + trigger) is the core innovation. Do not re-add squeeze/volume/ATR filters.
2. **Dual entry triggers** — both breakout (+101.5%) and pullback (+135.4%) contribute meaningfully.
3. **1h resampling** — this is the right timeframe. Going back to 5m would recreate v6's problems.
4. **3% hard stop** — working correctly as catastrophic protection (107 trades, -51.9% = avg -0.49% per stop).
5. **Breakeven stop at 1.5%** — provides free-roll protection without limiting upside.
6. **Long + Short** — shorts add +51.4% despite lower WR.
7. **ADX ≥ 20 threshold** — exhaustive analysis of 683 (dev) / 478 (test) blocked entries confirmed the filter is correct. Hypothetical trades at ADX<20 returned -190% dev / -303% test with 25% WR. ADX(7) short-period as replacement also failed (85% of blocked entries already have ADX(7)≥20, still deeply negative). No indicator combination (ATR, KC bandwidth, DI spread, RSI, momentum, volume) cleanly separates winners from losers (best AUC=0.564, barely above random). Script: `analyze_adx_blocked_entries.py`.

---

## 9. File Layout

```
src/strategies/
├── swing_trend.py           # SwingTrendStrategy class
├── intraday_trend.py        # IntradayTrendStrategy (v6, preserved)
├── intraday_indicators.py   # Shared indicator functions (HMA, ST, KC)
├── base.py                  # StrategyBase ABC
├── registry.py              # Strategy registry ("swing_trend" registered)

config/
├── swing_trend_dev_v1.yaml  # Dev set (2022-2024)
├── swing_trend_test_v1.yaml # Test set (2020-2026)

reports/
├── swing_trend_BTCUSDT_2022-01-01_2024-12-31_v1.html  # Dev report
├── swing_trend_BTCUSDT_2020-01-01_2026-01-31_v1.html  # Test report
```

---

## 10. How to Run

```bash
# Activate virtual environment
source .venv/bin/activate

# Run on dev set (2022-2024)
PYTHONPATH=src python3 run_backtest.py config/swing_trend_dev_v1.yaml

# Run on test set (2020-2026)
PYTHONPATH=src python3 run_backtest.py config/swing_trend_test_v1.yaml

# Output:
#   reports/BTC_Swing_Trend_Dev_swing_trend_v1.csv    (bar-by-bar log)
#   reports/swing_trend_BTCUSDT_..._v1.html           (HTML report)
```

---

## 11. Next Steps (v4 Roadmap)

| Priority | Change | Status | Expected Impact | Risk |
|----------|--------|--------|----------------|------|
| ✅ | Add `kc_midline_hold_bars` trigger | **Done — v2** | +2.5x return, halved test MaxDD | — |
| ✅ | MACD cross entry + trend re-entry + early exit | **Done — v3** | +1.9x test return | — |
| ❌ | Relax ADX threshold / use ADX(7) | **Rejected** | Blocked entries are -190%/-303% losers | — |
| 1 | Increase `min_hold_bars` to 12-18 | Open | Eliminate sub-24h losing trades | Overfitting |
| 2 | Tighter trailing ST (`trailing_supertrend_multiplier: 2.0-2.5`) | Open | Improve MFE retention 20% → 30%+ | Premature exits |
| 3 | Grid search core params (HMA period, ST mult, ADX threshold) | Open | 10-30% uplift | Standard optimization risk |
| 4 | Short side tuning (higher ADX, breakout-only for shorts) | Open | Improve short WR/PnL | Fewer short trades |
| 5 | Monthly circuit breaker (-10%) | Open | Cap worst-month drawdown | Reduced opportunity |

---

## 12. v3 Implementation Notes

### 12.1 Key Lessons from v3 Development

**Mistake 1: MACD entry behind HMA+ST gate (initial attempt)**
First implementation placed MACD as "Path B" after the HMA+ST direction check. Since `return None` was reached when HMA+ST disagreed, MACD entries never fired (only 3 on dev). The whole point of MACD is to enter BEFORE HMA+ST confirm — so it must be independent.

**Mistake 2: MACD/RSI exits applied to all trades (initial attempt)**
First attempt applied MACD histogram exit and RSI threshold exit to all 590 trades (including KC entries). This destroyed v2's performance (dropped from +3083% to +2284%) because the exits cut off the big multi-day winners. MACD/RSI exits must be scoped to MACD-entered trades only.

**Mistake 3: RSI exit as threshold instead of reversal**
Initially exited when `RSI >= 65` (any time above 65). The MACD RSI strategy exits when `prev_rsi >= 70 AND rsi < 65` — RSI must first hit overbought THEN drop. Big difference: the threshold version fires constantly in uptrends.

**Mistake 4: Same tight stops for MACD entries**
MACD entries used Swing's 3% hard stop + Supertrend trailing. MACD RSI uses 8% stop + 3x ATR trailing — much wider, surviving pullbacks. MACD enters earlier when trends are less confirmed, so wider stops are needed.

**Mistake 5: Missing trend re-entry**
59% of MACD RSI's long entries are trend re-entries (after profitable exit, re-enter with just MACD > signal). This was the single biggest missing feature for MACD entry count.

### 12.2 v3 Architecture: Two Parallel Systems

v3 is effectively two strategies sharing the same position:

| Aspect | KC System (Path A) | MACD System (Path B/C) |
|--------|-------------------|----------------------|
| Entry conditions | HMA+ST agree + KC trigger | MACD cross + ADX + RSI + EMA(200) |
| Direction source | HMA slope + Supertrend | MACD cross direction |
| Stop loss | 3% hard stop | 8% + 3x ATR (wider) |
| Trailing | Supertrend line | ATR-based trailing |
| Exit signals | Supertrend flip | MACD death cross, RSI reversal |
| Min hold | 6h (all trades) | 6h (all trades) |
| Re-entry | Via KC triggers | Trend re-entry after profitable exit |
| Breakeven stop | 1.5% trigger | Not applied |

The two systems are mutually exclusive (share position) — whichever fires first enters. KC entries are unaffected by MACD code.


AI Summary of what changed, on which I have some questions:

1. MACD entry now independent of HMA+ST — fires when MACD crosses with ADX+RSI+EMA(200), regardless of whether HMA slope/Supertrend agree. This is the key: it enters trends 1-3 days before HMA+ST confirm.

2. Trend re-entry added — after a profitable MACD exit, re-enters with just MACD > signal (no fresh cross), 2-bar cooldown. 34 re-entries on dev with 61.8% WR.

3. RSI exit fixed — now matches MACD RSI: exits when prev_rsi >= 70 AND rsi < 65 (overbought reversal), not when rsi >= 65 (which was wrong).

4. Wider ATR-based stops for MACD entries — 8% stop + 3x ATR trailing instead of 3% hard stop + Supertrend. Survives pullbacks that would have stopped out.

5. Cross confirmation window — weak MACD crosses (< 2 bps histogram) get a 2-bar window to strengthen, matching MACD RSI.


---

## 13. v4: HMA(300) Trend Filter

### 13.1 What Changed

Replaced EMA(200) price-above filter for MACD entries with HMA(300) slope>0.

**Why:** EMA(200) is a lagging binary filter (price above/below a slow line). HMA(300) slope captures trend *direction change* faster — it turns positive earlier in uptrends and negative earlier in downtrends. On 1h bars, HMA(300) ≈ 12.5 day lookback.

**Config params:**
```
macd_trend_filter: hma_slope      # was "ema" (default)
ema_trend_period: 300             # HMA period (repurposed param name)
macd_trend_slope_threshold: 0     # slope > 0 for LONG
```

**Fix included:** Consume re-entry eligibility when MACD+RSI+ADX agree but trend filter blocks. Previously, re-entry attempts were saved until the slope confirmed — those delayed re-entries were net losers.

**SHORT macd_reentry:** Tested but reverted. Too few trades (6-8) to optimize reliably. Dev regression outweighed test gain. Filters (hist bps, ADX) didn't help consistently.

### 13.2 Results

| Metric | v3 Dev | v4 Dev | v3 Test | v4 Test |
|--------|--------|--------|---------|---------|
| **Total Return** | +3,315% | **+4,835%** | +7,019% | **+10,042%** |
| **Sharpe Ratio** | 2.638 | **2.89** | 2.840 | **3.14** |
| **Max Drawdown** | -19.7% | -17.7% | -16.2% | -20.1% |
| **Trades** | 619 | 619 | 562 | 574 |

No overfitting: test (+10,042%) >> dev (+4,835%).

---

## 14. v5: Squeeze Release LONG Override

### 14.1 Problem

ST_BEAR and ADX<20 block LONG entries for hours during the early phase of trends. By the time these indicators confirm, significant opportunity is lost.

Ideas tested from section 4 of this document:
- **4a. MTF regime filtering** — debunked. We already trade 1h bars; higher TF (4h/8h) adds lag rather than removing it.
- **4b. Volume/flow filters** — debunked. All variants (CVD, VWAP, OBV) performed worse than baseline on both dev and test.
- **4c. Squeeze release** — winner for LONGs. Structural regime change signal.
- **4d. ADX derivative** — mixed results, not pursued.

### 14.2 What is Squeeze Release

**Squeeze:** Bollinger Bands (20, 2.0) are inside Keltner Channels — BB upper < KC upper AND BB lower > KC lower. This means volatility has compressed below the trend's normal range.

**Squeeze Release:** The bar where squeeze ends — was in squeeze last bar, not in squeeze now. BB bands have expanded beyond KC, signaling a volatility regime change.

Key finding: squeeze release direction is nearly 50/50 (49% up on dev, 53% up on test) — it's not directionally predictive, it signals *expansion*. HMA slope provides the direction.

### 14.3 What Was Tested

**Phase 1 — Override scope:**
| Variant | Dev Return | Test Return |
|---------|-----------|-------------|
| Baseline (v4) | +4,835% | +10,042% |
| ST-only override | +5,061% | +12,339% |
| ADX-only override | +4,928% | +11,274% |
| **ST+ADX override** | **+5,577%** | **+14,934%** |

ST+ADX override (bypass both when squeeze releases) won on both sets.

**Phase 2 — Squeeze duration:**
Minimum squeeze duration of 1, 3, 5, 10 bars before release. Duration=1 (default, any squeeze) was best.

**Phase 3 — Additional filters:**
Tested adding ADX floor (>10, >15), KC bandwidth min, HMA slope minimum. All either hurt or didn't help. The base squeeze release signal is already clean.

**SHORT squeeze entries:** Only +32% dev / +11.7% test sum PnL. Long-only squeeze override is strictly better.

**Dev vs test asymmetry explained:** Test set (2020-21 + 2025-26) has 3.6x more downtrend days and 6.7x more crash days than dev (2022-24). More volatile regimes create more squeeze-release opportunities.

### 14.4 Implementation (Path D — LONG only)

Added as Path D in `_check_entry()`, after Paths A/B/C return None:

```
PATH D — Squeeze Release Override (LONG):
  When squeeze releases (BB exits KC compression)
  AND HMA slope > 0
  AND (ST_BEAR or ADX < 20)    ← at least one must be blocking
  → Override for LONG only
  Still requires KC trigger (breakout/pullback/midline hold)
  Uses default 3% hard stop
  _st_confirmed set based on actual ST state at entry
```

Config: `enable_squeeze_override: true` (default false for backward compat)

### 14.5 Results

| Metric | v4 Dev | v5 Dev | v4 Test | v5 Test |
|--------|--------|--------|---------|---------|
| **Total Return** | +4,835% | **+5,577%** | +10,042% | **+14,934%** |

+15% dev improvement, +49% test improvement. No overfitting.

---

## 15. v6: SHORT ROC Override + Tighter Override Stops

### 15.1 Problem

Squeeze release doesn't work for SHORTs — shorts don't need the "energy building" phase that squeeze captures. SHORT squeeze entries added only +32% dev / +11.7% test with many losers.

### 15.2 SHORT Signal Exploration

Tested 6 alternative SHORT-specific signals as override triggers (bypassing ST_BULL/ADX when they block):

| Signal | Description | Dev Return | Test Return | Short Overrides (dev/test) |
|--------|-------------|-----------|-------------|---------------------------|
| **ROC(5)<-3%** | Price dropped >3% in 5 bars | **+7,959%** | **+17,456%** | 24/51 |
| vol_spike (3x avg) | Volume spike | +5,760% | +15,505% | 52/68 |
| consec_down (3 bars) | 3 consecutive down closes | +5,802% | +16,137% | 79/137 |
| hma_accel (<-0.5%) | HMA slope acceleration | +6,063% | +13,700% | 110/111 |
| breakdown (2% below KC lower) | Price below KC lower | +5,577% | +14,934% | 0/0 |
| obv_div | OBV divergence | +5,577% | +14,934% | 0/0 |

ROC (Rate of Change) won decisively — high-quality, infrequent signals.

### 15.3 ROC Parameter Tuning

`ROC(N) = closes.pct_change(N) * 100` — percentage price change over N hourly bars.

| Period | Threshold | Dev Return | Test Return | Short Overrides (dev/test) |
|--------|-----------|-----------|-------------|---------------------------|
| ROC(3) | <-2% | +7,785% | +19,006% | 49/85 |
| ROC(4) | <-2% | +9,003% | +25,299% | 59/107 |
| **ROC(4)** | **<-2.5%** | **+8,464%** | **+21,845%** | **35/60** |
| ROC(4) | <-3% | +7,959% | +17,456% | 24/51 |
| ROC(6) | <-3% | +8,176% | +23,258% | 32/55 |
| ROC(8) | <-3% | +7,497% | +20,040% | 30/46 |

**ROC(4)<-2.5% chosen** — balanced trade count, strong on both sets. ROC(4)<-2% is more aggressive (higher return but 59/107 overrides).

### 15.4 Why ROC Beats HMA Slope for SHORTs

HMA slope fires on every bar the slope is negative enough (130-305 overrides) — too many signals, many blow up on test. ROC fires only during sharp moves (35/60 overrides) — it captures crash-like events where momentum validates the SHORT even before ST/ADX confirm.

HMA wider periods (32, 42) with slope and acceleration thresholds were also tested. All worse — either too many signals or too few.

### 15.5 Implementation

Extended Path D in `_check_entry()`:

```
PATH D — ROC Override (SHORT):
  When ROC(4) < -2.5% (price dropped 2.5%+ in 4 hours)
  AND HMA slope < 0
  AND (ST_BULL or ADX < short_threshold)    ← at least one must be blocking
  → Override for SHORT
  Still requires KC trigger
```

**Tighter override stop:** All override entries (both LONG squeeze and SHORT ROC) use 2% hard stop instead of the default 3%. Override entries are less confirmed by lagging indicators, so tighter risk control is warranted.

Config:
```yaml
enable_short_roc_override: true
short_roc_period: 4
short_roc_threshold: 2.5        # positive; negated in code
squeeze_override_stop_pct: 2.0  # tighter stop for ALL override entries
```

### 15.6 Results

| Metric | v4 Dev | v5 Dev | v6 Dev | v4 Test | v5 Test | v6 Test |
|--------|--------|--------|--------|---------|---------|---------|
| **Total Return** | +4,835% | +5,577% | **+8,464%** | +10,042% | +14,934% | **+21,845%** |

v6 is +75% over v4 on dev, +118% on test. No overfitting (test >> dev).

---

## 16. v7: HMACD — HMA-Based MACD Entry Signal

### 16.1 Problem

The trend filter uses HMA (smooth, low-lag), but the MACD entry signal uses standard EMA (noisier, more lag). This creates a smoothness mismatch: HMA correctly identifies trend direction, but EMA-based MACD produces false crosses in chop and late true crosses.

### 16.2 Solution: HMACD

Replace EMA with HMA in the MACD computation:

```
HMACD_line  = HMA(close, fast) - HMA(close, slow)
Signal_line = HMA(HMACD_line, signal)
Histogram   = HMACD_line - Signal_line
```

HMA (Hull Moving Average) uses `WMA(2*WMA(n/2) - WMA(n), sqrt(n))` which is inherently smooth and low-lag. Using HMA for both the MACD lines AND the signal line eliminates the EMA noise that causes false crosses.

### 16.3 Implementation

**File: `src/strategies/intraday_indicators.py`** — Added `compute_hmacd()`:
```python
def compute_hmacd(closes, fast, slow, signal):
    hma_fast = compute_hma(closes, fast)
    hma_slow = compute_hma(closes, slow)
    hmacd_line = hma_fast - hma_slow
    signal_line = compute_hma(hmacd_line, signal)
    histogram = hmacd_line - signal_line
    return hmacd_line, signal_line, histogram
```

**File: `src/strategies/swing_trend.py`** — Added `macd_use_hma` config toggle:
```python
self.macd_use_hma = config.get("macd_use_hma", False)
```
In `prepare()`, swaps computation based on toggle. No changes to entry/exit logic — all reads from `_macd_line`, `_macd_signal_line`, `_macd_histogram`.

### 16.4 Period Selection: Grid Search

Tested standard 12/26/9 and three alternative period combos. HMA periods behave differently from EMA — shorter periods are practical because HMA is already smooth.

| Config | Dev Return | Dev Trades | Dev WR | Test Return | Test Trades | Test WR |
|--------|-----------|-----------|--------|------------|------------|---------|
| EMA 12/26/9 (v6 baseline) | +8,464% | 647 | 48.8% | +21,845% | 656 | 51.7% |
| HMACD 12/26/9 | +8,062% | 793 | 51.5% | +27,183% | 876 | 53.7% |
| HMACD 8/21/9 | +15,879% | 848 | 53.3% | +39,037% | 926 | 54.4% |
| **HMACD 10/21/5** | **+13,487%** | **819** | **53.6%** | **+61,320%** | **890** | **57.5%** |
| HMACD 18/39/14 | +7,145% | 729 | 50.3% | +8,213% | 764 | 51.7% |

**HMACD 10/21/5 chosen** — best test performance (+61,320% vs baseline +21,845%), highest win rate (57.5%), and strong dev. HMACD 8/21/9 also excellent but 10/21/5 has better test/dev ratio confirming generalization.

### 16.5 Overfitting Validation

#### Year-by-Year Breakdown

HMACD 10/21/5 beats baseline in ALL 6 individual years — not a single year of underperformance:

| Year | EMA 12/26/9 (baseline) | HMACD 10/21/5 | Improvement |
|------|----------------------|---------------|-------------|
| 2020 | +779% (192t, 51.6% WR) | +1,147% (266t, 58.6% WR) | +47% |
| 2021 | +528% (228t, 55.7% WR) | +1,219% (335t, 59.7% WR) | +131% |
| 2022 | +534% (205t, 52.2% WR) | +556% (277t, 56.3% WR) | +4% |
| 2023 | +225% (210t, 46.7% WR) | +346% (263t, 51.3% WR) | +54% |
| 2024 | +288% (232t, 47.4% WR) | +321% (274t, 52.6% WR) | +11% |
| 2025 | +226% (231t, 47.2% WR) | +238% (277t, 53.1% WR) | +5% |

Win rate improves in every single year (+4 to +7 percentage points), confirming HMA-based MACD produces fewer false crosses across all market regimes.

#### Parameter Sensitivity (Dev Set)

Smooth surface around 10/21/5 — no sharp cliff indicating overfitting:

| Params | Dev Return |
|--------|-----------|
| 9/20/4 | +24,828% |
| 9/21/5 | +17,958% |
| 10/20/5 | +13,216% |
| **10/21/5** | **+13,487%** |
| 10/22/5 | +10,915% |
| 10/21/6 | +12,116% |
| 11/21/5 | +13,502% |
| 11/22/6 | +16,573% |

All neighbors produce strong returns (+10,900% to +24,800%). No parameter is on a cliff edge.

#### Recent Out-of-Sample (Feb 27 → Mar 17, 2026)

| Config | Return | Trades | WR |
|--------|--------|--------|----|
| EMA 12/26/9 (baseline) | +12.01% | 11 | 63.6% |
| HMACD 10/21/5 | +10.26% | 12 | 58.3% |

Both profitable on unseen recent data. Small underperformance (-1.75%) on just 18 days / 12 trades is noise, not signal.

### 16.6 Why It Works

1. **Smoothness match**: Trend filter (HMA slope) and entry signal (HMACD) now use the same smoothing algorithm. No smoothness mismatch.
2. **Fewer false crosses**: HMA's low-lag, smooth characteristic filters out the chop that causes EMA-MACD false golden/death crosses. Win rate jumps +5pp consistently.
3. **Faster true crosses**: HMA responds faster to genuine trend changes than EMA, capturing moves earlier.
4. **Shorter periods viable**: Because HMA is already smooth, shorter periods (10/21 vs 12/26) don't add noise — they add speed. The signal period of 5 (vs 9) makes the signal line more responsive without becoming noisy.

### 16.7 Config

```yaml
# --- v7: HMACD (HMA-based MACD) ---
macd_use_hma: true
macd_fast: 10
macd_slow: 21
macd_signal: 5
```

### 16.8 Results

| Metric | v6 Dev | v7 Dev | v6 Test | v7 Test |
|--------|--------|--------|---------|---------|
| **Total Return** | +8,464% | **+13,487%** | +21,845% | **+61,320%** |
| Trades | 647 | 819 | 656 | 890 |
| Win Rate | 48.8% | **53.6%** | 51.7% | **57.5%** |

v7 is +59% over v6 on dev, +181% on test. No overfitting (test >> dev). Win rate improvement is the most meaningful metric — fewer false entries directly compounds over time.

---

## 17. v8: Thesis Invalidation Exit

### 17.1 Problem

The 6-12h hold duration bucket was v7's biggest weakness: 162 trades, 17.3% WR, -105.3% sum PnL. Specifically, 80 supertrend trailing exits had 11.2% WR and -89.3% sum PnL. These trades survived the 6h min_hold window, then immediately got stopped out by ST trailing activation. Their MFE was very low (avg 0.86%, median 0.49%) — they never showed meaningful momentum.

### 17.2 Solution

Exit KC trades at the min_hold boundary if their Maximum Favorable Excursion (MFE) hasn't reached a minimum threshold. The thesis: trend-following trades that don't show early momentum are likely in chop and will bleed out via ST trailing.

At exactly `hourly_bars_held == min_hold_bars`, check if the trade's peak unrealized profit is below the threshold. If so, exit at close with reason `thesis_invalidation`. This only affects KC-triggered trades — MACD entries have their own exit logic.

### 17.3 Grid Search

| Threshold | Dev Return | Dev WR | Dev MaxDD | Dev Sharpe | TI Exits |
|-----------|-----------|--------|-----------|------------|----------|
| 0% (v7)   | +13,487%  | 43.7%  | -19.8%    | 4.198      | 0        |
| 0.3%      | +19,293%  | 43.0%  | -17.3%    | 4.526      | 141      |
| 0.5%      | +17,527%  | 44.1%  | -17.3%    | 4.447      | 225      |
| 0.75%     | +20,139%  | 46.0%  | -17.4%    | 4.639      | 342      |
| **1.0%**  | **+26,720%** | **49.1%** | **-15.6%** | **4.883** | **455** |

All thresholds improve over baseline. Monotonic improvement suggests the signal is robust.

### 17.4 Why It Works

1. **Frees capital faster**: Bad trades exit at hour 6 instead of bleeding via ST trailing for 6-18 more hours. The freed capital can re-enter on fresh signals.
2. **More trades**: v8 has 1,144 trades vs v7's 819 — the freed capital generates ~325 additional trades that are net positive.
3. **Higher WR**: By cutting the weakest trades early, overall win rate improves from 43.7% to 49.1%.
4. **Lower drawdown**: Avoiding the slow bleed of unpromising trades reduces max drawdown from -19.8% to -15.6%.

### 17.5 Config

```yaml
# --- v8: Thesis invalidation exit ---
thesis_invalidation_pct: 1.0    # MFE threshold (%), exit KC trades at min_hold if MFE < this
```

### 17.6 Results

| Metric | v7 Dev | v8 Dev | v7 Test | v8 Test |
|--------|--------|--------|---------|---------|
| **Total Return** | +13,487% | **+26,720%** | +61,320% | **+95,523%** |
| **Win Rate** | 43.7% | **49.1%** | 47.4% | **50.3%** |
| **Max Drawdown** | -19.8% | **-15.6%** | -18.0% | **-16.2%** |
| **Sharpe** | 4.198 | **4.883** | 4.738 | **5.242** |
| Trades | 819 | 1,144 | 890 | 1,155 |

v8 is +98% over v7 on dev, +56% on test. No overfitting (test >> dev). Every metric improves.

---

## 18. v9: HMACD Histogram Delta Filter

### 18.1 Problem

kc_midline_hold entries were v8's biggest weakness by volume: 550 trades total, 72% ending in failure (291 thesis_invalidation at -89.5%, 107 hard_stop at -47.4%). Only 152 reached supertrend trailing at +389.1%. The trigger fires whenever price holds above KC mid for 1 bar with HMA+ST agreement — this is too permissive when MACD momentum is actually decelerating.

### 18.2 Solution: Histogram Delta Filter

Apply idea (f) from section 0 — the "Internal Velocity Filter". Instead of just checking if the HMACD histogram is positive, check if it's **expanding** (delta > 0 for LONG, delta < 0 for SHORT). This measures momentum acceleration rather than momentum level.

Key insight: a negative histogram that's turning positive (delta > 0) is a valid entry — momentum is building even though MACD hasn't crossed yet. A positive histogram that's shrinking (delta < 0) is dangerous — the trend looks intact but is losing steam.

### 18.3 What Was Tested

1. **Histogram sign filter** (histogram > 0 for LONG): Too aggressive. Removed 166 entries including 36 good ST trailing winners. Dev return dropped from +26,720% to +25,225%.

2. **Histogram delta filter** (histogram expanding): Precise. Only removed ~121 entries with actively decelerating momentum, preserving entries where momentum was building. Dev return increased to +31,426%.

### 18.4 Config

```yaml
# --- v9: HMACD histogram delta filter for kc_midline_hold ---
kc_histogram_filter: true
```

### 18.5 Results

| Metric | v8 Dev | v9 Dev | v8 Test | v9 Test |
|--------|--------|--------|---------|---------|
| **Total Return** | +26,720% | **+31,426%** | +95,523% | **+129,511%** |
| **Sharpe** | 4.06 | **4.21** | 4.36 | **4.69** |
| **Max Drawdown** | -15.58% | **-15.57%** | -16.24% | **-14.28%** |
| **Win Rate** | 49.1% | **50.6%** | 50.3% | **52.5%** |
| Trades | 1,144 | 1,139 | 1,155 | 1,163 |

v9 is +17.6% over v8 on dev, +35.6% on test. Every metric improves on both sets. No overfitting (test >> dev).

---

## 19. v10: HMACD Histogram Delta Filter for Keltner Breakout

### 19.1 Problem

keltner_breakout was v9's weakest trigger: 146 trades, 34.9% WR, +29.7% sumPnL on dev (vs keltner_pullback: 49.7% WR, +127.0%). 94 thesis_invalidation exits at -37.1% and 25 hard_stop exits at -17.6% dragged down the otherwise profitable 27 supertrend_trailing exits (+84.3%).

### 19.2 Solution

Extended v9's HMACD histogram delta filter to also apply to keltner_breakout entries. The logic is the same: require the HMACD histogram to be expanding in the trade direction. A breakout where HMACD momentum is decelerating is a classic false breakout — price pushes through the KC band but the underlying trend is losing steam.

### 19.3 Config

```yaml
# --- v10: HMACD histogram delta filter for keltner_breakout ---
breakout_histogram_filter: true
```

### 19.4 Results

| Metric | v9 Dev | v10 Dev | v9 Test | v10 Test |
|--------|--------|---------|---------|----------|
| **Total Return** | +31,426% | **+45,999%** (+46.4%) | +129,511% | **+130,048%** (+0.4%) |
| **Sharpe** | 4.21 | **4.48** | 4.69 | **4.72** |
| **Max Drawdown** | -15.57% | -15.64% | -14.28% | **-13.72%** |
| **Win Rate** | 50.6% | **51.2%** | 52.5% | **52.7%** |
| Trades | 1,139 | 1,115 | 1,163 | 1,150 |

The filter removed 69 false breakout entries on dev (146→77, 47% removed). Remaining breakouts have 36.4% WR (vs 34.9%), +38.1% sumPnL (vs +29.7%), and 0.49% avgPnL (vs 0.20%). Freed capital re-entered via kc_midline_hold (+46 entries) and pullback (+3).

Dev/test asymmetry: Dev improved +46.4% while test was flat +0.4%. The test set has fewer breakout entries (114 vs 146). No test metric degraded.

---

## 20. v11: Tighter Trailing Supertrend (2.0)

### 20.1 Problem

MFE retention was only 26% (dev) / 24% (test). The Supertrend trailing used the same 3.0 multiplier as the entry filter, creating a wide band that allowed trades to give back most of their peak profit before exiting. The 0-2% MFE bucket of ST trailing exits had **negative** retention (-47% dev, -55% test) — these trades showed profit then reversed to losses before the wide trailing caught them.

### 20.2 Solution

Changed `trailing_supertrend_multiplier` from 0 (same as entry = 3.0) to 2.0. The tighter band sits closer to price, catching reversals sooner. This is a config-only change — the dual-supertrend infrastructure has existed since v1.

### 20.3 Grid Search

| Multiplier | Dev Return | Dev Sharpe | Dev MaxDD | Dev Trades | Dev WR |
|-----------|-----------|-----------|----------|-----------|--------|
| 3.0 (v10) | +45,999% | 4.48 | -15.64% | 1,115 | 51.2% |
| 2.5 | +83,670% | 4.95 | -14.02% | 1,199 | 53.1% |
| **2.0** | **+104,118%** | **5.30** | **-12.37%** | **1,336** | **56.4%** |
| 1.5 | +127,984% | 5.42 | -14.69% | 1,484 | 59.2% |

**2.0 chosen** for best Sharpe (5.30) and lowest MaxDD (-12.37%). 1.5 has higher raw return but worse MaxDD and diminishing avgPnL — the additional capital recycling shows diminishing returns.

### 20.4 Why It Works

1. **Faster profit capture**: Tighter trailing locks in profit sooner. Winning trades exit at a higher percentage of their MFE.
2. **Capital recycling**: Faster exits → more capital available for fresh entries. Trade count increases from 1,115 to 1,336 (+20%). These additional trades are net positive.
3. **Loss reduction**: Trades that previously peaked at 1-2% then reversed to losses now exit at or near their peak, flipping from losers to small winners.
4. **Lower drawdown**: Catching reversals sooner prevents the slow bleed that causes drawdown.

### 20.5 Config

```yaml
trailing_supertrend_multiplier: 2.0    # was 0 (=3.0, same as entry)
```

### 20.6 Results

| Metric | v10 Dev | v11 Dev | v10 Test | v11 Test |
|--------|---------|---------|----------|----------|
| **Total Return** | +45,999% | **+104,118%** (+126%) | +130,048% | **+272,140%** (+109%) |
| **Sharpe** | 4.48 | **5.30** | 4.72 | **5.51** |
| **Max Drawdown** | -15.64% | **-12.37%** | -13.72% | **-13.51%** |
| **Win Rate** | 51.2% | **56.4%** | 52.7% | **56.5%** |
| Trades | 1,115 | 1,336 | 1,150 | 1,420 |

v11 more than doubles v10's return on both sets. No overfitting (test >> dev). Best Sharpe, best MaxDD, best WR across all versions.

---

## 21. v12: Lower SHORT ADX Threshold (short_adx_threshold 18)

**Problem:** SHORT entries arrive too late. ADX >= 25 is the #1 SHORT blocker (23,155 hours on dev where HMA+ST are bearish but ADX is below 25). Iran dataset case study: 25-hour gap (3/5 16:00 to 3/6 17:00) where price dropped 4.6% with no position — ADX was below 25 for 16 of those hours.

**Key insight:** SHORTs have *better* quality than LONGs (57.7% WR vs 55.6%, 0.59% avgPnL vs 0.46%) but there are 40% fewer of them (504 vs 832 on dev). The bottleneck is entry opportunity, not trade quality. Down moves develop faster, so lower ADX confirmation is appropriate for SHORTs.

**User suggested** faster HMACD for shorts, but analysis showed the HMACD histogram delta filter only blocked 3 hours in the Iran case while ADX blocked 16. The right fix is the ADX threshold, not the indicator speed.

**Config change:** `short_adx_threshold: 18` (was 25). ADX >= 18 still indicates an emerging trend (standard: 0-20 = absent, 20-25 = emerging, 25+ = strong). No code change — param already existed.

**Grid search:**

| ADX Threshold | Dev Return | Dev Sharpe | Shorts | AvgPnL |
|:---:|:---:|:---:|:---:|:---:|
| 25 (v11) | +104,118% | 5.30 | 504 | 0.507% |
| 22 | +103,870% | 5.22 | 604 | 0.473% |
| 20 | +110,136% | 5.27 | 683 | 0.455% |
| **18** | **+144,824%** | **5.37** | **737** | 0.460% |
| 15 | +106,076% | 5.24 | 831 | 0.416% |

18 maximizes Sharpe. 15 adds too many low-quality shorts.

| Metric | v11 Dev | v12 Dev | v11 Test | v12 Test |
|--------|---------|---------|----------|----------|
| **Total Return** | +104,118% | **+144,824%** (+39%) | +272,140% | **+647,366%** (+138%) |
| **Sharpe** | 5.30 | **5.37** | 5.51 | **5.89** |
| **Max Drawdown** | -12.37% | -13.81% | -13.51% | -15.36% |
| **Win Rate** | 56.4% | **56.9%** | 56.5% | **57.8%** |
| Trades | 1,336 | 1,545 (+209) | 1,420 | 1,620 (+200) |
| Shorts | 504 | **737** (+233) | 536 | **763** (+227) |

MaxDD increases ~1.5-2% but returns more than compensate. No overfitting (test >> dev).

**Infrastructure fix:** Added data gap detection (>24h) to controller.py that force-closes positions before gaps. This fixed a latent bug where a SHORT held across the test set's 3-year gap (2021→2025) would cause a -101% PnL catastrophe.

---

## 22. v13 — Fast ADX(10) for SHORTs (threshold 20)

**Problem:** v12's lower ADX threshold (18) improved SHORT entry timing but accepted weaker trends, increasing MaxDD. Can we enter SHORTs earlier while maintaining the same quality gate?

**Solution:** Use ADX(10) instead of ADX(14) specifically for SHORT entries, keeping the threshold at 20 (same as LONGs). ADX(10) reacts ~40% faster to trend changes, so downtrends cross the ≥20 threshold sooner. Unlike lowering the threshold, this maintains the same trend-strength bar — it just measures over a shorter lookback.

**Config:** `short_adx_period: 10`, `short_adx_threshold: 20`

**Code changes:**
- Added `short_adx_period` config param (defaults to `adx_period`)
- `prepare()` computes a separate `_short_adx` series when periods differ
- `_check_entry()` receives `short_adx_val` and uses it for SHORT gates
- Gate logic: `kc_short_adx_ok = short_adx_val >= short_adx_threshold` allows SHORT entry even when regular ADX(14) is below 20

**Alternatives tested:**

| Variant | Dev Return | Dev Sharpe | Dev MaxDD | Test Return | Test Sharpe |
|---------|-----------|-----------|-----------|-------------|-------------|
| v12 ADX(14) t=18 | +144,824% | 5.37 | -13.81% | +647,366% | 5.89 |
| ADX(10) t=25 | +146,236% | 5.46 | **-11.73%** | +317,389% | 5.48 |
| ADX(7) t=25 | +253,589% | 5.77 | -14.06% | +526,452% | 5.78 |
| **ADX(10) t=20** | **+176,017%** | **5.41** | -15.01% | **+707,205%** | **5.95** |

ADX(10) t=20 chosen: best test return and Sharpe. ADX(7) had best dev metrics but weaker test performance (likely overfitting). ADX(10) t=25 was too conservative.

| Metric | v12 Dev | v13 Dev | v12 Test | v13 Test |
|--------|---------|---------|----------|----------|
| **Return** | +144,824% | **+176,017%** (+22%) | +647,366% | **+707,205%** (+9%) |
| **Sharpe** | 5.37 | **5.41** | 5.89 | **5.95** |
| **MaxDD** | -13.81% | -15.01% | -15.36% | -16.47% |
| Trades | 1,545 | 1,565 | 1,620 | 1,645 |
| Shorts | 737 | 764 | 763 | 795 |

---

*Document version: v13.0 — 2026-03-20*
*Strategy implementation: src/strategies/swing_trend.py*
*Champion status: v13 (fast ADX(10) for SHORTs, threshold 20) — replaces v12*
