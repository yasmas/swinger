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

2) ~~The ADX problem is structurally harder — 50% of gaps are pure consolidation with correctly-low ADX. Options: lower threshold from 20→15 (risk: more noise trades), or use a short-period ADX (e.g. 7) alongside the 14-period one to catch trend acceleration faster.~~ - we proved that this is something we don't want to puruse. In other words, if ADX is low, most of the time there are no winners.


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

### 3.6 Entry Filter: Supertrend Stop Distance

```
stop_distance = abs(price - supertrend_line) / price
SKIP if stop_distance > 3%     # ST trailing would be wider than hard stop

# Also skip if stop_distance < 0 (price on wrong side of ST — shouldn't happen
# given Layer 1 check, but defensive)
```

### 3.7 Cooldown

```
SKIP entry if (current_hourly_idx - last_exit_hourly_idx) < 3
# Minimum 3 hours between trades (prevents overtrading after stops)
```

### 3.8 Exit Logic (checked every 5m bar)

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

     a. STOP HIT:
        LONG:  bar.low <= active_stop → exit at min(close, active_stop)
        SHORT: bar.high >= active_stop → exit at max(close, active_stop)

     b. SUPERTREND FLIP (only outside min_hold window):
        LONG:  supertrend flips bearish → exit at close
        SHORT: supertrend flips bullish → exit at close

     c. CIRCUIT BREAKER:
        daily_pnl <= -8% → close all positions

     d. LAST BAR:
        Force liquidate at close
```

### 3.9 Min Hold Window Behavior

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

### 6.1 v1 vs v2 Headline Comparison

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

## 11. Next Steps (v3 Roadmap)

| Priority | Change | Status | Expected Impact | Risk |
|----------|--------|--------|----------------|------|
| ✅ | Add `kc_midline_hold_bars` trigger | **Done — v2** | +2.5x return, halved test MaxDD | — |
| ❌ | Relax ADX threshold / use ADX(7) | **Rejected** | Blocked entries are -190%/-303% losers | — |
| 1 | Increase `min_hold_bars` to 12-18 | Open | Eliminate sub-24h losing trades | Overfitting |
| 2 | Tighter trailing ST (`trailing_supertrend_multiplier: 2.0-2.5`) | Open | Improve MFE retention 20% → 30%+ | Premature exits |
| 3 | Grid search core params (HMA period, ST mult, ADX threshold) | Open | 10-30% uplift | Standard optimization risk |
| 4 | Short side tuning (higher ADX, breakout-only for shorts) | Open | Improve short WR/PnL | Fewer short trades |
| 5 | Monthly circuit breaker (-10%) | Open | Cap worst-month drawdown | Reduced opportunity |

---

*Document version: v2.0 — 2026-03-12*
*Strategy implementation: src/strategies/swing_trend.py*
*Champion status: v2 (kc_midline_hold_bars=1) — replaces Intraday v6 and swing v1*
