# Intraday Trading Algorithm — High-Level Design

## 1. Overview

A fully automated intraday trend-following system for BTC/USDT on **5-minute bars**. The system uses a **4-layer confluence model** — regime filtering, directional bias, entry triggers, and risk management — to capture short-to-medium intraday trends while avoiding choppy markets.

**Key characteristics:**
- Timeframe: 5-minute bars (primary), no multi-timeframe
- Position sizing: 100% of capital per trade (all-in)
- Direction: Long and Short
- Hold duration: Minutes to days (no forced time exit, carry overnight)
- Stop-loss: 2% of account value (hard stop)
- Daily circuit breaker: 6% max daily drawdown
- Transaction cost assumption: 0.05% per trade (0.10% round trip)
- Target frequency: 1-3 trades per day (may be 0 on flat/choppy days)
- New strategy class — does not modify existing MACD/RSI system

---

## 2. Self-Critique & Design Tensions Resolved

Before presenting the algorithm, here are the tensions identified during research and how we resolve them:

### 2.1 The 2% Stop vs Supertrend Stop Conflict

**Problem:** We go all-in (100% capital) and place a hard stop at 2% loss. But Supertrend (ATR 10, Mult 3.0) on 5m bars may place its trailing stop at a variable distance — sometimes 0.5%, sometimes 4%. If the Supertrend stop is wider than 2%, our hard stop will trigger first, potentially getting whipsawed out before the trend has room to breathe.

**Resolution:** Use a **two-tier stop system:**
- **Hard stop (immovable):** 2% below entry (long) or above (short). This is the max-loss guarantee — never violated.
- **Supertrend trailing stop:** Once the trade moves in our favor and the Supertrend line is tighter than the 2% stop, the Supertrend becomes the active stop. It only gets tighter as the trend progresses.
- **Entry filter:** Only enter when the Supertrend line at entry is within 2% of price. If the Supertrend stop would be wider than 2%, **skip the trade** — the volatility is too high for our risk budget. This naturally filters out chaotic, overly volatile moments.

### 2.2 VWAP for Crypto 24/7

**Problem:** Standard VWAP resets at market open, but crypto has no market open. The 00:00 UTC reset is arbitrary.

**Resolution:** Use **daily VWAP** (reset at 00:00 UTC) as a simple bias indicator. It's not required for entry (per user decision), so its imperfection is acceptable. It's easy to compute, and for BTC (the most liquid crypto), it still reflects meaningful volume distribution within a 24h window. We don't anchor to swing points (too complex to automate reliably in v1).

### 2.3 Trade Frequency Reality Check

**Problem:** User wants 2-5 trades/day, but with strict confluence (regime filter + HMA+Supertrend agreement + Keltner breakout), many days may produce 0-1 signals.

**Resolution:** This is actually fine. Research shows that **fewer, higher-quality trades outperform frequent lower-quality ones** in trend following. The 2-5 target is aspirational. In practice:
- Bull/Bear months: expect 2-4 trades/day
- Choppy months: expect 0-1 trades/day (regime filter blocks most)
- Flat months: expect 0 trades/day (good — avoid over-trading)

### 2.4 Keltner Breakout May Be Too Restrictive

**Problem:** Requiring price to close outside the Keltner Channel might miss trades where the trend starts gradually without a breakout.

**Resolution:** Add a **secondary entry type** — the **Keltner midline bounce.** In an established trend (HMA + Supertrend agree, and Supertrend has been in this direction for at least N bars), a pullback to the Keltner EMA midline with a bounce (close back above/below) is a valid re-entry. This captures trend continuations that don't produce fresh breakouts.

### 2.5 Indicator Redundancy Check

**Concern:** Are any of our indicators measuring the same thing?

**Analysis:**
| Indicator | What it measures | Unique contribution |
|---|---|---|
| HMA | Price momentum / direction | Fastest directional read (low lag) |
| Supertrend | Trend structure via volatility | Flip-based trend + trailing stop |
| Keltner | Volatility envelope | Breakout detection + squeeze |
| VWAP | Volume-weighted fair value | Institutional bias (volume-based) |
| ADX | Trend strength | Regime quality filter |
| BB (squeeze only) | Volatility compression | Combined with Keltner for regime |

**Verdict:** No redundancy. Each indicator provides a unique signal dimension. HMA and Supertrend are both "trend direction" but use completely different math (weighted MA vs ATR-based flip), so they genuinely provide independent confirmation.

---

## 3. The Algorithm — Complete Specification

### 3.1 Indicator Calculations (computed every 5m bar)

```
On every new 5-minute bar:

1. HMA(21):
   WMA_half = WMA(close, 10)
   WMA_full = WMA(close, 21)
   diff = 2 × WMA_half - WMA_full
   HMA = WMA(diff, 4)    # sqrt(21) ≈ 4
   HMA_slope = HMA - HMA[1]   # positive = rising, negative = falling

2. Supertrend(ATR=10, Mult=3.0):
   ATR_10 = ATR(high, low, close, 10)
   median = (high + low) / 2
   upper_band = median + 3.0 × ATR_10
   lower_band = median - 3.0 × ATR_10
   [apply final band logic — see research doc for details]
   supertrend_bullish = (close > supertrend_line)

3. Keltner Channels(EMA=15, ATR=10, Mult=2.0):
   kc_mid = EMA(close, 15)
   kc_atr = ATR(high, low, close, 10)
   kc_upper = kc_mid + 2.0 × kc_atr
   kc_lower = kc_mid - 2.0 × kc_atr

4. Bollinger Bands(Period=20, StdDev=2.0):
   bb_mid = SMA(close, 20)
   bb_std = StdDev(close, 20)
   bb_upper = bb_mid + 2.0 × bb_std
   bb_lower = bb_mid - 2.0 × bb_std

5. TTM Squeeze:
   squeeze_on = (bb_lower > kc_lower) AND (bb_upper < kc_upper)
   squeeze_fired = squeeze_on[1] AND NOT squeeze_on  # transition OFF

6. ADX(14):
   [standard ADX calculation]
   trending = ADX > 25

7. VWAP (daily, reset 00:00 UTC):
   [cumulative TP×Vol / cumulative Vol, reset daily]

8. Volume:
   vol_avg = SMA(volume, 20)
   vol_confirm = (volume > 1.5 × vol_avg)
```

### 3.2 Layer 1: Regime Filter

The regime filter is evaluated every bar. If the regime is unfavorable, **no new entries** are allowed (existing positions are managed normally — they keep their trailing stops).

```
CAN_TRADE = NOT squeeze_on          # BB must be outside Keltner
            AND ADX > 25             # Trend must be strong enough
            AND NOT daily_stop_hit   # Haven't hit 6% daily loss

# Daily circuit breaker
daily_pnl = (portfolio_value - day_start_value) / day_start_value
daily_stop_hit = (daily_pnl <= -0.06)
day_start_value resets at 00:00 UTC
```

### 3.3 Layer 2: Directional Bias

Evaluated only when CAN_TRADE = true.

```
DIRECTION:
  if HMA_slope > 0 AND supertrend_bullish:
      direction = LONG
  elif HMA_slope < 0 AND NOT supertrend_bullish:
      direction = SHORT
  else:
      direction = NONE  # "Choppy Zone" — no trade

VWAP_ALIGNED:
  if direction == LONG:  vwap_aligned = (close > VWAP)
  if direction == SHORT: vwap_aligned = (close < VWAP)
  # Used as a confidence boost in logging, not as a gate
```

### 3.4 Layer 3: Entry Triggers

Evaluated only when CAN_TRADE = true AND direction ≠ NONE AND no position is currently held.

```
ENTRY TRIGGER A — Keltner Breakout:
  LONG:  close > kc_upper AND vol_confirm
  SHORT: close < kc_lower AND vol_confirm

ENTRY TRIGGER B — Keltner Midline Bounce (re-entry in established trend):
  Conditions:
    - Supertrend has been in current direction for >= 6 bars (30 min)
    - Price pulled back to touch or cross the kc_mid line
    - Price bounced: current close is back on the trend side of kc_mid
  LONG:  low <= kc_mid AND close > kc_mid AND vol_confirm
  SHORT: high >= kc_mid AND close < kc_mid AND vol_confirm

ENTRY FILTER — Supertrend Stop Distance:
  For LONG:  stop_distance = (close - supertrend_line) / close
  For SHORT: stop_distance = (supertrend_line - close) / close
  SKIP if stop_distance > 0.02  # Supertrend stop wider than 2% — too volatile
```

### 3.5 Layer 4: Risk Management & Exits

```
ON ENTRY:
  entry_price = close
  hard_stop:
    LONG:  entry_price × (1 - 0.02)  # 2% below entry
    SHORT: entry_price × (1 + 0.02)  # 2% above entry
  initial_supertrend_stop = supertrend_line at entry

EVERY BAR WHILE IN POSITION:
  active_stop = max(hard_stop, supertrend_line)  # for LONG: take the HIGHER of the two
                 min(hard_stop, supertrend_line)  # for SHORT: take the LOWER of the two
  # Note: Supertrend tightens as trend progresses; hard_stop is the floor

EXIT CONDITIONS (any one triggers exit):
  1. STOP HIT:
     LONG:  low <= active_stop  → exit at active_stop price
     SHORT: high >= active_stop → exit at active_stop price

  2. SUPERTREND FLIP:
     Supertrend direction reverses → exit at close
     (This often coincides with stop hit, but catches cases
      where a gap-through occurs)

  3. DIRECTION REVERSAL:
     HMA_slope flips AND Supertrend flips → exit at close
     (Stronger signal than Supertrend flip alone)

  4. DAILY CIRCUIT BREAKER:
     daily_pnl <= -6% → close all positions immediately at close

POSITION SIZING:
  quantity = floor(cash / entry_price)  # 100% all-in, no leverage
  # The 2% risk is managed via stop placement, not position sizing
```

### 3.6 Trade Lifecycle — Complete Flow

```
┌─────────────────────────────────────────────────────────────┐
│  NEW 5-MINUTE BAR ARRIVES                                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. Compute all indicators (HMA, Supertrend, Keltner,       │
│     BB, ADX, VWAP, Volume)                                  │
│                                                             │
│  2. IF in position:                                         │
│     ├─ Check exit conditions (stop, flip, circuit breaker)  │
│     ├─ If exit triggered → SELL/COVER, log trade            │
│     └─ Else → HOLD, update trailing stop                    │
│                                                             │
│  3. IF not in position:                                     │
│     ├─ Check regime filter (squeeze, ADX, daily stop)       │
│     │   └─ If blocked → HOLD                                │
│     ├─ Check directional bias (HMA + Supertrend)            │
│     │   └─ If choppy zone → HOLD                            │
│     ├─ Check entry trigger (Keltner breakout or bounce)     │
│     │   └─ If no trigger → HOLD                             │
│     ├─ Check entry filter (Supertrend within 2%)            │
│     │   └─ If too volatile → HOLD                           │
│     └─ All checks pass → BUY/SHORT, set stops, log trade    │
│                                                             │
│  4. Log bar to CSV with indicator values and decision reason │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Strategy State — What the Strategy Tracks

```python
class IntradayTrendStrategy(StrategyBase):
    # Indicator state (recomputed each bar from data)
    _hma: float
    _hma_slope: float
    _supertrend_line: float
    _supertrend_bullish: bool
    _kc_upper: float
    _kc_mid: float
    _kc_lower: float
    _squeeze_on: bool
    _adx: float
    _vwap: float
    _vol_avg: float

    # Position tracking
    _entry_price: float | None
    _entry_bar: int | None
    _hard_stop: float | None
    _direction: str | None          # "LONG" or "SHORT"

    # Daily tracking
    _day_start_value: float
    _daily_stop_hit: bool
    _current_day: date

    # Supertrend trend duration (for midline bounce entry)
    _supertrend_direction_bars: int  # How many bars in current direction
```

---

## 5. Details JSON (in trade log CSV)

Every BUY/SELL/SHORT/COVER action logs a JSON details field:

```json
{
  "trigger": "keltner_breakout",        // or "keltner_bounce"
  "direction": "LONG",
  "hma_slope": 12.5,
  "supertrend_bullish": true,
  "supertrend_line": 84250.0,
  "adx": 32.4,
  "squeeze_on": false,
  "vwap": 84100.0,
  "vwap_aligned": true,
  "kc_upper": 84500.0,
  "kc_mid": 84200.0,
  "volume_ratio": 1.8,
  "stop_distance_pct": 1.2,
  "hard_stop": 82800.0,
  "entry_price": 84530.0
}
```

Exit actions add:
```json
{
  "exit_reason": "supertrend_flip",     // or "hard_stop", "circuit_breaker", "direction_reversal"
  "bars_held": 45,
  "pnl_pct": 1.8,
  "max_favorable_excursion_pct": 2.3,
  "max_adverse_excursion_pct": -0.7
}
```

---

## 6. Configuration (YAML)

```yaml
backtest:
  name: "BTC Intraday Trend v1"
  initial_cash: 100000
  start_date: "2022-01-01"
  end_date: "2024-12-31"

data_source:
  type: "csv_file"
  parser: "binance_kline"
  params:
    file_path: "data/BTCUSDT-5m-20220101-20241231.csv"
    symbol: "BTCUSDT"

strategies:
  - type: "intraday_trend"
    params:
      # HMA
      hma_period: 21

      # Supertrend
      supertrend_atr_period: 10
      supertrend_multiplier: 3.0

      # Keltner Channels
      keltner_ema_period: 15
      keltner_atr_period: 10
      keltner_atr_multiplier: 2.0

      # Bollinger Bands (for squeeze detection only)
      bb_period: 20
      bb_stddev: 2.0

      # ADX
      adx_period: 14
      adx_threshold: 25

      # Volume
      volume_avg_period: 20
      volume_confirm_multiplier: 1.5

      # Risk management
      stop_loss_pct: 2.0              # Max loss per trade
      daily_max_drawdown_pct: 6.0     # Daily circuit breaker
      max_supertrend_stop_pct: 2.0    # Skip entry if ST stop > this

      # Entry
      enable_keltner_bounce: true     # Allow midline bounce re-entries
      min_trend_bars_for_bounce: 6    # Min bars in trend for bounce entry

      # Costs
      cost_per_trade_pct: 0.05        # Per-side transaction cost

      # Shorts
      enable_short: true
```

---

## 7. Warm-Up Requirements

The strategy requires historical data to initialize indicators before generating signals:

| Indicator | Lookback Needed | In 5m Bars |
|---|---|---|
| HMA(21) | ~21 bars + sqrt(21) ≈ 25 | 25 |
| Supertrend(ATR=10) | 10 bars for ATR | 10 |
| Keltner(EMA=15, ATR=10) | 15 bars | 15 |
| Bollinger(20) | 20 bars | 20 |
| ADX(14) | ~28 bars (needs DI smoothing) | 28 |
| Volume SMA(20) | 20 bars | 20 |

**Minimum warm-up: 30 bars (2.5 hours of 5m data).** Conservative: use 50 bars (4+ hours) to ensure all indicators are stable.

For paper trading: the warm-up is trivially small compared to the MACD/RSI hourly system's 250-hour requirement.

---

## 8. Integration with Existing System

This strategy is a **new class** (`IntradayTrendStrategy`) registered in the strategy registry alongside the existing strategies. No modifications to existing code.

```
src/swinger/strategies/
├── base.py                    # StrategyBase ABC (unchanged)
├── buy_and_hold.py            # (unchanged)
├── ma_crossover_rsi.py        # (unchanged)
├── macd_rsi_advanced.py       # (unchanged)
├── intraday_trend.py          # NEW — this algorithm
├── registry.py                # Add "intraday_trend" entry
```

The strategy implements `on_bar()` as defined by `StrategyBase`, receiving 5m OHLCV bars directly (no resampling needed — unlike the hourly system).

For **comparison reports**, both strategies can be run on the same data period and compared via the existing multi-strategy reporting (Phase 8).

---

## 9. Implementation Phases

Each phase is a small, self-contained unit of work (implementable in a few minutes). Phases build on each other sequentially.

### Workflow Rules
- **Commit after every phase** — each phase ends with a git commit of all changes
- **Fully autonomous** — no pausing for permission between phases; run tests, fix issues, and keep moving
- **Commit message format:** `intraday vN phase I-X: <short description>` (for implementation) or `intraday vN optN: <short description>` (for optimization)
- **If tests fail:** fix and re-commit before moving to the next phase
- **Run backtest after Phase I-6 and after each optimization** — generate CSV + HTML report

### Phase I-1: Indicator Helper Functions

Create `src/strategies/intraday_indicators.py` with pure functions (no strategy state):

- `compute_hma(closes: pd.Series, period: int) -> pd.Series` — Hull Moving Average
- `compute_supertrend(highs, lows, closes, atr_period, multiplier) -> tuple[pd.Series, pd.Series]` — returns (supertrend_line, is_bullish)
- `compute_keltner(highs, lows, closes, ema_period, atr_period, multiplier) -> tuple[pd.Series, pd.Series, pd.Series]` — returns (upper, mid, lower)
- `compute_bollinger(closes, period, stddev) -> tuple[pd.Series, pd.Series, pd.Series]` — returns (upper, mid, lower)
- `compute_squeeze(bb_upper, bb_lower, kc_upper, kc_lower) -> pd.Series` — returns boolean squeeze_on
- `compute_vwap_daily(highs, lows, closes, volumes, dates) -> pd.Series` — daily-reset VWAP

Reuse existing `compute_adx`, `compute_atr`, `compute_ema` from `macd_rsi_advanced.py` (import them).

**Test:** Unit test each function with known inputs/outputs. Verify HMA matches a reference implementation. Verify Supertrend flips at correct bars on synthetic data.

### Phase I-2: Strategy Skeleton — `prepare()` + Indicator Precomputation

Create `src/strategies/intraday_trend.py` with `IntradayTrendStrategy(StrategyBase)`:

- `__init__(self, config)` — read all params from config dict, set defaults
- `prepare(self, full_data)` — precompute ALL indicators on the full 5m DataFrame and store as instance attributes:
  - `self._hma`, `self._hma_slope`
  - `self._st_line`, `self._st_bullish`
  - `self._kc_upper`, `self._kc_mid`, `self._kc_lower`
  - `self._squeeze_on`
  - `self._adx`
  - `self._vwap`
  - `self._vol_avg`
- No trading logic yet — just indicator computation.

Register `"intraday_trend"` in `src/strategies/registry.py`.

**Test:** Load dev data, call `prepare()`, verify indicator arrays have correct length and no NaN after warm-up period. Spot-check a few values.

### Phase I-3: Entry Logic — `on_bar()` for BUY/SHORT

Implement the entry path in `on_bar()`:

- Look up precomputed indicator values at the current bar index
- Layer 1: Check regime (squeeze_on, ADX, daily_stop_hit)
- Layer 2: Check direction (HMA slope + Supertrend agree)
- Layer 3: Check entry trigger (Keltner breakout with volume confirmation)
- Entry filter: Supertrend stop distance ≤ 2%
- If all pass → return `Action(BUY/SHORT, quantity, details)`
- Else → return `Action(HOLD)`

State tracking: `_in_position`, `_direction`, `_entry_price`, `_entry_bar_idx`, `_hard_stop`

**Test:** Run on a small slice of dev data. Verify BUY/SHORT actions fire only when all 4 layers pass. Check details JSON is populated correctly.

### Phase I-4: Exit Logic — Stops, Supertrend Flip, Circuit Breaker

Add exit logic to `on_bar()` (checked before entry logic when in position):

- Supertrend trailing stop: exit when price crosses the Supertrend line
- Hard stop: exit when loss reaches 2% of entry
- Supertrend flip: exit on direction change
- Daily circuit breaker: track `_day_start_value`, exit all if daily P&L ≤ -6%
- On last bar: force liquidate (like existing strategies)
- Return `Action(SELL/COVER, quantity, details_with_exit_reason)`

**Test:** Synthetic data with known stop-hit scenario. Verify exit fires at correct bar. Verify trailing stop tightens correctly.

### Phase I-5: Keltner Midline Bounce Entry (Secondary Entry)

Add the bounce re-entry logic (configurable, `enable_keltner_bounce` param):

- Check `_supertrend_direction_bars >= min_trend_bars_for_bounce` (trend is established)
- Check price touched kc_mid and bounced back
- Same regime and direction filters apply
- Track `_supertrend_direction_bars` counter (increment each bar while direction holds, reset on flip)

**Test:** Verify bounce entry fires only in established trends. Verify it doesn't fire on fresh trend starts.

### Phase I-6: Backtest Config YAML + Run Script

Create `config/intraday_trend_dev.yaml` for running the strategy on the dev set:

```yaml
backtest:
  name: "BTC Intraday Trend"
  version: "v1"
  initial_cash: 100000
  start_date: "2022-01-01"
  end_date: "2024-12-31"

data_source:
  type: "csv_file"
  parser: "binance_kline"
  params:
    file_path: "data/BTCUSDT-5m-20240101-20260131.csv"
    symbol: "BTCUSDT"

strategies:
  - type: "intraday_trend"
    params: { ... all defaults from Section 6 ... }
```

Also create `config/intraday_trend_test.yaml` for the test set.

Create `tmp/run_intraday_backtest.py`:
```python
# Loads config, runs Controller, generates CSV trade log + HTML report
# Prints summary stats to stdout
```

**Test:** Run the backtest end-to-end on dev set. Verify CSV trade log is generated with correct columns. Verify HTML report renders with price chart + trade markers.

### Phase I-7: Report Enhancement — Intraday-Specific Metrics

Extend the reporter (or add a wrapper script) to compute and display:
- Trades per day average
- Win rate, Profit factor
- Average bars held per trade
- Performance breakdown by regime month (Bull/Bear/Choppy/Flat)
- Max consecutive losses
- Transaction cost impact (gross vs net return)

Output as a summary table in the HTML report and as printed console output.

**Test:** Run on dev set, verify metrics appear correctly in report.

---

## 10. Optimization Phases (v1 → v5)

Each optimization phase follows the same cycle:
1. **Run** the current version on the dev set
2. **Analyze** the trade log: what works, what doesn't, where do we lose money
3. **Critique** the algorithm — identify the #1 improvement opportunity
4. **Implement** the change (bump version)
5. **Re-run** and compare metrics before/after

### Optimization v1 → v2: Tune Core Parameters

**Run v1** on dev set (2022-2024) with default parameters.

**Analyze:**
- Which parameter has the biggest impact on Sharpe? Run a focused grid search:
  - HMA period: [9, 15, 21]
  - Supertrend multiplier: [2.0, 3.0, 4.0]
  - Keltner ATR multiplier: [1.5, 2.0, 2.5]
  - ADX threshold: [20, 25, 30]
- For each combination, record: total return, max drawdown, Sharpe, win rate, trades/day
- Identify which regime types generate losses — are we losing in Bear months? Choppy months?

**Expected findings:** Likely the ADX threshold and Supertrend multiplier have the biggest impact. Too-tight Supertrend (mult=2.0) causes whipsaws; too-loose (mult=4.0) gives back too much profit.

**Critique & Change:** Lock in the best parameter set. Update config with optimized values. Bump to `version: "v2"`.

**Deliverable:** `config/intraday_trend_dev_v2.yaml` + comparison table (v1 vs v2 metrics)

### Optimization v2 → v3: Fix the Biggest Loser Pattern

**Run v2** on dev set.

**Analyze:**
- Sort trades by P&L. Examine the 10 worst losses:
  - What time of day did they occur?
  - What was the regime? (Were they in "borderline" ADX zones?)
  - Were they stopped out by hard stop (2%) or Supertrend flip?
  - How many bars were they held?
  - Was volume confirmation real or barely above threshold?
- Examine the 10 best wins — what made them work?
- Calculate: what % of losses are from short trades vs long trades?

**Expected findings:** Most losses likely cluster in one pattern — either:
  (a) entries right before a Supertrend flip (caught the tail end of a move), or
  (b) false Keltner breakouts that immediately reverse, or
  (c) specific regime transitions (market shifting from trending to choppy mid-trade)

**Critique & Change:** Address the dominant loss pattern. Possible changes:
- Add a **confirmation delay**: require 2 consecutive bars outside Keltner (not just 1)
- Add **momentum filter**: HMA slope must exceed a minimum threshold (not just positive)
- Tighten the **volume confirmation** multiplier if false breakouts are the problem
- Add a **cooldown period** after a losing trade (prevent revenge trading)

Bump to `version: "v3"`.

**Deliverable:** `config/intraday_trend_dev_v3.yaml` + analysis of what changed + before/after metrics

### Optimization v3 → v4: Improve Exit Quality

**Run v3** on dev set.

**Analyze:**
- For winning trades: how much profit was given back before exit? Calculate:
  - `max_favorable_excursion` (MFE) — the peak unrealized profit during the trade
  - `actual_profit / MFE` — "capture ratio" (how much of the available move we kept)
- For losing trades: how quickly did we recognize the loss?
  - `max_adverse_excursion` (MAE) — the worst drawdown during the trade
  - Time from entry to stop-hit
- Compare Supertrend exit vs hard stop exit — which produces better outcomes?

**Expected findings:** Winners probably give back 30-50% of MFE before Supertrend catches the exit. Some trades would have been better served by a profit target.

**Critique & Change:** Possible improvements:
- Add an **optional profit target** at 2× the risk (2:1 reward/risk) — take partial or full profit
- Add a **breakeven stop**: once trade is 1% in profit, move hard stop to entry price (zero-loss guarantee)
- Experiment with **tighter Supertrend for exits** than for entries (e.g., use ATR mult 2.5 for trailing stop vs 3.0 for direction filter)
- Add **time decay**: if trade hasn't moved meaningfully after N bars, tighten the stop

Bump to `version: "v4"`.

**Deliverable:** `config/intraday_trend_dev_v4.yaml` + MFE/MAE analysis + before/after metrics

### Optimization v4 → v5: Regime-Specific Tuning

**Run v4** on dev set.

**Analyze:**
- Break down performance by regime month:
  - Bull months: return, win rate, trades/day
  - Bear months: return, win rate, trades/day
  - Choppy months: return, win rate, trades/day
  - Flat months: return, win rate, trades/day
- Are shorts profitable in Bear months? Or are they a net drag?
- Is the regime filter (squeeze + ADX) catching the choppy periods effectively?
- How many trades happen in Flat months? (Should be near zero)

**Expected findings:** The system likely performs well in Bull and Bear months but still loses in Choppy months. Flat months may have a few unnecessary trades that slightly erode capital.

**Critique & Change:** Possible improvements:
- **Raise ADX threshold for shorts** (shorts need stronger trend confirmation than longs, since crypto is bull-biased)
- **Add a volatility floor**: if ATR is below Nth percentile of recent history, don't trade (market too quiet)
- **Tighten squeeze detection**: require squeeze to have been on for at least N bars before accepting a "squeeze fired" signal (avoid false squeeze-offs)
- **Per-regime parameter overrides** if the data supports it (though this risks overfitting)

Bump to `version: "v5"`.

**Deliverable:** `config/intraday_trend_dev_v5.yaml` + regime breakdown analysis + before/after metrics

### Final Validation: Run v5 on Test Set

**Run v5** on the test set (2020, 2021, 2025, 2026-01):
- Compare all metrics to dev set performance
- If Sharpe degrades by > 50%, we have overfit — roll back to last stable version
- Generate comparison report: v5 vs Buy-and-Hold vs MACD/RSI hourly on same test periods
- Report per-regime performance on test set to verify generalization

**Deliverable:** Final HTML report + summary table comparing all strategies + go/no-go decision

---

## 11. How to Run

### Quick Start (after implementation)

```bash
# Activate virtual environment
source .venv/bin/activate

# Run backtest on dev set
python -m controller config/intraday_trend_dev.yaml

# Output:
#   reports/BTC_Intraday_Trend_intraday_trend_v1.csv   (trade log)
#   reports/BTC_Intraday_Trend_intraday_trend_v1.html  (report)

# Run on test set
python -m controller config/intraday_trend_test.yaml

# Compare strategies (when multi-strategy comparison is ready)
# Both strategies in one config → side-by-side report
```

### Trade Log CSV

Same format as all other strategies (standardized by the controller):

```
date,action,symbol,quantity,price,cash_balance,portfolio_value,details
2022-01-03 14:30:00,BUY,BTCUSDT,2.15,46500.00,0.00,100075.00,"{""trigger"":""keltner_breakout"",...}"
2022-01-03 16:45:00,SELL,BTCUSDT,2.15,46800.00,100645.00,100645.00,"{""exit_reason"":""supertrend_flip"",...}"
```

### HTML Report

Generated by the existing `Reporter` class — includes:
- Price chart with BUY/SELL/SHORT/COVER markers
- % invested over time
- Stats summary table (return, Sharpe, drawdown, win rate, etc.)

---

## 12. Backtesting Plan Summary

| Phase | Version | What | Dev Set | Test Set |
|---|---|---|---|---|
| I-1 to I-7 | v1 | Build & verify the system works | ✅ Run | — |
| Opt 1 | v1→v2 | Tune core parameters (grid search) | ✅ Run | — |
| Opt 2 | v2→v3 | Fix biggest loser pattern | ✅ Run | — |
| Opt 3 | v3→v4 | Improve exit quality (MFE/MAE analysis) | ✅ Run | — |
| Opt 4 | v4→v5 | Regime-specific tuning | ✅ Run | — |
| Validate | v5 | Out-of-sample validation | — | ✅ Run |
| Compare | v5 | vs Buy-and-Hold, vs MACD/RSI hourly | — | ✅ Run |

---

## 13. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Over-trading in choppy markets** | Medium | High (death by 1000 cuts) | Regime filter (squeeze + ADX) blocks entries |
| **Whipsaw from 5m noise** | High | Medium | HMA+Supertrend confluence reduces false signals |
| **2% stop too tight for BTC volatility** | Medium | Medium | Entry filter: skip when Supertrend > 2% away |
| **Parameter overfitting on dev set** | Medium | High | Out-of-sample test on 37 months of unseen data |
| **Transaction costs erode profits** | Low | Medium | 0.05% assumption; at 2 trades/day = 0.1%/day = ~25%/year drag |
| **Flash crash / black swan** | Low | Very High | 2% hard stop limits single-event loss; 6% daily stop limits daily damage |
| **Carry overnight risk** | Low | Medium | Supertrend widens during low-vol overnight → natural wider stop |

---

## 14. Success Criteria

The strategy is considered viable if it achieves on the **test set**:

1. **Positive total return** after transaction costs
2. **Sharpe ratio > 1.0**
3. **Max drawdown < 20%**
4. **Profit factor > 1.3** (winners 30% bigger than losers on average)
5. **Win rate > 40%** (trend-following systems are typically 35-50% win rate)
6. **Does not lose money in Bear months** (should be flat or slightly positive via shorts)
7. **Outperforms Buy-and-Hold on risk-adjusted basis** (better Sharpe, lower drawdown)

---

*Document version: v1.0 — 2026-03-06*
*Based on research in docs/5m-high-level-plan.md*
