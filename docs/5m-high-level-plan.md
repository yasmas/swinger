# Intraday Trading System — Research & High-Level Plan

## Status: RESEARCH COMPLETE — Ready for Design Questions & Architecture

---

## 1. Context & Problem Statement

Our current system uses a **MACD/RSI strategy on hourly bars** — a slow swing trader that requires hours/days to generate signals. We want to build a **fast intraday system on 5-minute bars** that can capture shorter-term moves within the same extensible architecture.

The ideas document proposes four indicators: **Anchored VWAP, Hull Moving Average (HMA), Keltner Channels, and Supertrend**. This document captures research findings and evolving architectural thinking.

---

## 2. Research Completed

### 2.1 VWAP / Anchored VWAP (✅ Deep Research Done)

**What it is:** Volume-Weighted Average Price — the cumulative average price weighted by volume. Shows "fair value" based on where most volume transacted.

**Formula:**
```
VWAP = Σ(Typical_Price_i × Volume_i) / Σ(Volume_i)
Typical_Price = (High + Low + Close) / 3
```

**Key Findings for Crypto:**
- **Session boundary problem:** Standard VWAP resets at market open, but crypto trades 24/7. The 00:00 UTC reset is arbitrary and considered by many practitioners as "almost useless" for crypto.
- **Anchored VWAP is more valuable:** Anchoring to swing highs/lows or breakout candles avoids the arbitrary reset problem entirely.
- **Reliable for liquid pairs:** Works well for BTC/ETH during active hours; unreliable for thin pairs.
- **Best used as a FILTER, not a signal generator:** VWAP-only strategies show ~49% win rates. Profitability comes from combining VWAP with other confluence factors.
- **VWAP bands (1σ, 2σ, 3σ):** Provide dynamic overbought/oversold zones. For crypto, bands should be ~10-15% wider than stock defaults due to higher volatility.

**Practical Algorithm Role:**
- **Directional bias filter:** Only long above VWAP, only short below VWAP
- **Mean reversion target:** Price tends to revert to VWAP after extreme moves to outer bands
- **Dynamic support/resistance:** Anchored VWAP from swing points acts as institutional cost basis levels
- **Multiple anchors for confluence:** Where 2-3 AVWAPs cluster = strong zone

**Limitations:**
- Lagging (cumulative, gets sluggish as session progresses)
- Not predictive — shows where value WAS, not where it's going
- Fragmented exchange volume in crypto can distort calculations
- Fails in choppy/sideways markets (whipsaws above/below)

**Quantitative Results Found:**
- VWAP pullback strategy on SPY: win rate <50% but Profit Factor 1.692 (winners bigger than losers)
- Crypto VWAP strategy backtest: 713% return over 3 years, but 49% win rate and 115% volatility
- Deep learning VWAP execution on BTC: 24.6% slippage improvement over naive approach

---

### 2.2 Hull Moving Average (HMA) (✅ Research Done)

**What it is:** A moving average designed to eliminate lag while remaining smooth. Uses weighted moving averages and the square root of the period.

**Formula:**
```
Step 1: WMA_half = WMA(close, period/2)
Step 2: WMA_full = WMA(close, period)
Step 3: diff = 2 × WMA_half - WMA_full
Step 4: HMA = WMA(diff, sqrt(period))
```

**Optimal Parameters for BTC 5-Minute Charts:**

| Style | HMA Period | Notes |
|---|---|---|
| Scalping | **9** | Very fast, ideal for quick entries |
| Fast Day Trading | **12–15** | Good balance of speed & smoothness |
| Standard Intraday | **16** (default) | Creator's recommended default |
| Slightly Smoother | **21** | Popular for crypto; filters more noise |

- Alan Hull (creator) recommends default period of **16**
- For crypto 5m charts, consensus is **periods 9–21** as the sweet spot
- Most frequently cited values: **9, 15, and 21**
- For our target frequency (2-5 trades/day), **HMA 21** is likely the right starting point — smooths out noise while staying responsive

**Dual-HMA Approach:** Some traders use two HMAs (fast + slow) for crossover confirmation, but this adds complexity. Single HMA slope is simpler and aligns with our confluence model.

**Proposed Role in System:**
- **Directional filter on 5m chart:** HMA slope up = long bias, slope down = short bias
- Much faster than EMA at detecting trend changes
- Combined with Supertrend: when HMA and Supertrend disagree = "choppy zone" → no trades

**Known Concerns:**
- Can overshoot in volatile markets (the lag-removal math can cause it to "predict" moves that don't materialize)
- More prone to whipsaws in ranging markets than slower MAs — mitigated by our regime filter layer
- Should NOT be used alone — combine with Supertrend for confirmation

---

### 2.3 Keltner Channels (✅ Research Done)

**What it is:** Volatility envelope around an EMA, using ATR (not standard deviation like Bollinger Bands) for band width.

**Formula:**
```
Middle = EMA(close, period)
Upper = Middle + multiplier × ATR(period)
Lower = Middle - multiplier × ATR(period)
```

**Optimal Parameters for BTC 5-Minute Charts:**

| Parameter | Recommended Range | Starting Value |
|---|---|---|
| EMA Period | 10–15 (shorter for responsiveness) | **15** |
| ATR Period | 10–14 | **10** |
| ATR Multiplier | 2.0–2.5 (tension between fast signals and crypto noise) | **2.0** |

**Key tension for crypto on short timeframes:**
- Short timeframes push toward **lower** multiplier (1.5) for faster signals
- Crypto's high volatility pushes toward **higher** multiplier (2.5–3.0) to filter noise
- **Starting at 2.0** is the balanced recommendation, widening to 2.5 if false breakouts are excessive

**Proposed Role in System:**
- **Breakout detection:** Close outside upper channel with expanding volume = trend breakout entry
- **"Riding the band":** In strong trends, price hugs the outer band — this is STRENGTH not overbought
- **Squeeze detection (with Bollinger Bands):** When BB moves inside Keltner = volatility compressed → breakout imminent

**Key Advantage Over Bollinger Bands:**
- ATR-based bands expand more smoothly during volatility, avoiding the extreme "ballooning" of Bollinger Bands
- Better for trend-following (Bollinger better for mean-reversion)
- We use BOTH: Keltner for breakout entries + BB/Keltner squeeze for regime detection

---

### 2.4 Supertrend (✅ Research Done)

**What it is:** A trend-following overlay that uses ATR to create a dynamic trailing stop line above or below price.

**Formula (detailed):**
```
Basic Upper Band = (High + Low) / 2 + multiplier × ATR(period)
Basic Lower Band = (High + Low) / 2 - multiplier × ATR(period)

Final Upper Band:
  if Basic Upper Band < prev Final Upper Band OR prev Close > prev Final Upper Band:
    Final Upper Band = Basic Upper Band
  else:
    Final Upper Band = prev Final Upper Band

Final Lower Band:
  if Basic Lower Band > prev Final Lower Band OR prev Close < prev Final Lower Band:
    Final Lower Band = Basic Lower Band
  else:
    Final Lower Band = prev Final Lower Band

Supertrend Direction:
  if prev Supertrend == prev Final Upper Band:
    if Close > Final Upper Band → flip to BULLISH (Supertrend = Final Lower Band)
    else → stay BEARISH (Supertrend = Final Upper Band)
  else:
    if Close < Final Lower Band → flip to BEARISH (Supertrend = Final Upper Band)
    else → stay BULLISH (Supertrend = Final Lower Band)
```

**Optimal Parameters for BTC 5-Minute Charts:**

| Style | ATR Period | Multiplier | Notes |
|---|---|---|---|
| Scalping | 7 | 2.0 | Fast, more signals, more false positives |
| **Intraday (Classic)** | **10** | **3.0** | **Balanced default — our starting point** |
| Conservative | 14 | 4.0 | Fewer, stronger signals, wider stops |

**Dual Supertrend Strategy (advanced):**
- Fast: ATR 10, Multiplier 3.0 (for entry timing & trailing stop)
- Slow: ATR 25, Multiplier 5.0 (for higher-level trend confirmation)
- Enter only when both agree; use the fast one for stop management

**Proposed Role in System:**
- **Automated trailing stop:** Once in a trade, exit when price closes beyond the Supertrend line
- **Trend filter:** Only trade in the direction the Supertrend indicates
- **Choppy zone detection:** When HMA and Supertrend disagree, market is indecisive → stand aside

**Key Advantages:**
- Only flips when price structurally breaks through the volatility threshold
- Acts as both directional filter AND exit mechanism
- Simple, binary signal — reduces decision complexity
- Replaces our fixed % trailing stop with a volatility-adaptive one

**Limitations:**
- Cleaner on 15m–4H charts than on 5m; more noise on lower timeframes
- In range-bound conditions, flips colors repeatedly → whipsaw losses
- **Must combine with ADX or regime filter** to avoid choppy markets

---

### 2.5 Regime Detection: TTM Squeeze + ADX (✅ Research Done)

This is a NEW component not in the original ideas doc, discovered during research. Critical for avoiding the #1 killer of intraday systems: trading in choppy/ranging markets.

**TTM Squeeze (Bollinger Band / Keltner Channel Squeeze):**
- When Bollinger Bands contract and go **completely inside** Keltner Channels = **squeeze ON** (market consolidating, low volatility)
- When Bollinger Bands expand and move **outside** Keltner Channels = **squeeze OFF / "fired"** (breakout beginning)
- Squeeze state is binary: ON (red dots) or OFF (green dots)
- A momentum histogram shows expected breakout direction

**ADX (Average Directional Index):**
- Already in our existing MACD/RSI system (ADX period 14, threshold 20)
- ADX > 20–25 = trending market, signals are more reliable
- ADX < 20 = ranging/choppy market, avoid trading
- For intraday, ADX at level 25–30 recommended as filter

**Combined Regime Detection Logic:**
```
TRENDING REGIME (trade):
  Squeeze OFF (BB outside Keltner) AND ADX > 25

CONSOLIDATING REGIME (don't trade):
  Squeeze ON (BB inside Keltner) OR ADX < 20

TRANSITIONING (watch closely):
  Squeeze just fired (OFF) but ADX still building
  → Wait for ADX to confirm before entering
```

**Why this matters:** Research consistently shows that Supertrend, HMA, and VWAP all produce excessive false signals in ranging markets. The regime filter is not optional — it's essential for profitability.

---

### 2.6 Combined Multi-Indicator Systems (✅ Research Done)

**Key finding:** Existing combined systems in the wild validate our layered approach. The most successful pattern is:

**"HMA + Supertrend Sniper" System:**
- Bullish confluence: HMA rising (slope > 0) **AND** price above Supertrend (uptrend)
- Bearish confluence: HMA falling (slope < 0) **AND** price below Supertrend (downtrend)
- **"Choppy Zone":** HMA and Supertrend disagree → exit all positions, go flat, paint candles gray
- This directly validates our Layer 2 (Directional Bias) architecture

**"SuperTrend + VWAP + EMA + ADX" System:**
- Entry requires: SuperTrend direction + price above/below VWAP and EMA + ADX confirming strength
- Designed for 5m, 15m, and 1h timeframes
- "Multiple indicator cross-validation improves trading signal accuracy"
- Risk: "may generate frequent false signals in oscillating markets" → confirms need for regime filter

**Best Practice — Don't Duplicate:**
- Select indicators that **complement** each other rather than duplicate
- Recommended mix: one trend-direction (Supertrend), one speed/momentum (HMA), one volatility (Keltner), one volume (VWAP)
- Our four indicators cover four distinct dimensions — this is good architecture

---

## 3. Research Checklist

- [x] **VWAP/AVWAP deep dive:** Formula, crypto considerations, best practices
- [x] **HMA deep dive:** Optimal periods (9–21 for crypto 5m), dual-HMA approach
- [x] **Supertrend deep dive:** Best parameters (ATR 10 / Mult 3.0), dual Supertrend, crypto caveats
- [x] **Keltner Channels deep dive:** Optimal settings (EMA 15, ATR mult 2.0), squeeze strategy
- [x] **Combined system architecture:** HMA+Supertrend sniper, VWAP+SuperTrend+ADX systems
- [x] **Regime detection:** TTM Squeeze (BB/Keltner) + ADX as critical choppy-market filter
- [x] **Role of existing indicators:** ADX confirmed valuable; RSI/MACD may be secondary/optional
- [ ] **Multi-timeframe analysis:** Decision pending (see Section 5 Q&A)
- [ ] **Risk management details:** 2% rule + 6% daily stop defined (see Section 5 Q&A)

---

## 4. Refined Architecture Concept

Based on all research, the system uses a **4-layer confluence model**:

```
┌──────────────────────────────────────────────────────────┐
│  Layer 1: REGIME FILTER                                  │
│  "Should we be trading at all right now?"                │
│                                                          │
│  • TTM Squeeze: BB inside Keltner = NO TRADE             │
│  • ADX(14) > 25 required for entry                       │
│  • Volume above minimum threshold                        │
│  • Daily circuit breaker: 6% max daily drawdown          │
│  • If regime = CONSOLIDATING → all signals ignored       │
├──────────────────────────────────────────────────────────┤
│  Layer 2: DIRECTIONAL BIAS                               │
│  "Which direction should we trade?"                      │
│                                                          │
│  REQUIRED (both must agree):                             │
│  • HMA(21) slope: rising = long, falling = short         │
│  • Supertrend(10,3): above = bullish, below = bearish    │
│  If HMA & Supertrend disagree → "Choppy Zone" → no trade│
│                                                          │
│  OPTIONAL BOOST (not required, adds confidence):         │
│  • VWAP position: price above = bullish, below = bearish │
├──────────────────────────────────────────────────────────┤
│  Layer 3: ENTRY TRIGGER                                  │
│  "When exactly do we enter?"                             │
│                                                          │
│  LONG: 5m candle closes above upper Keltner Channel      │
│        with volume > 1.5× average                        │
│  SHORT: 5m candle closes below lower Keltner Channel     │
│        with volume > 1.5× average                        │
├──────────────────────────────────────────────────────────┤
│  Layer 4: RISK MANAGEMENT / EXIT                         │
│  "How do we manage and exit the trade?"                  │
│                                                          │
│  • Position size: 100% of capital (all-in)               │
│  • Stop-loss: placed where loss = 2% of account          │
│  • Trailing stop: Supertrend(10,3) line                  │
│  • No forced time exit — hold until stop hit             │
│  • Can carry overnight                                   │
│  • Tx cost: 0.05% per trade (0.10% round trip)           │
└──────────────────────────────────────────────────────────┘
```

**Signal flow:** Regime OK → HMA+Supertrend agree on direction → Keltner breakout fires → Enter 100% of capital → Trail with Supertrend → Exit when Supertrend flips or 2% stop hit.

---

## 5. Design Questions & User Answers

1. **Timeframe:** Pure 5m system, or multi-timeframe (1h direction + 5m entries)?
   > A: What is more likely to bring higher win rate?
   > **Resolution:** Research says multi-timeframe (1h direction + 5m entry) typically produces higher win rates. However, our 4-indicator confluence on 5m alone may be sufficient since we have multiple directional filters. **Recommend: start with pure 5m, add 1h overlay as v2 enhancement if needed.**

2. **Trade frequency:** How many trades per day is acceptable?
   > A: 2-5

3. **Position sizing:** All-in per trade or scaled entries?
   > A: Keep it simple. Always invest 100% of capital. Place the stop-loss at the level where loss = 2% of account. No leverage, no variable sizing.

4. **Short selling:** Include short positions for intraday, or long-only?
   > A: Yes (include shorts)

5. **Session awareness:** Should the system behave differently during Asia/Europe/US sessions?
   > A: No

6. **Integration with existing system:** Separate strategy class, or extension of MACD/RSI?
   > A: No, a new trader. Don't break the existing trader. We still want to baseline performance against it.

7. **Risk budget:**
   > A: Max Single-Trade Risk: 2% of capital (distance to stop × position size = 2% of account). Max Daily Drawdown: 6% (3× single-trade risk).

8. **Backtesting period:**
   > A: Divide the years into Bears, Bulls, Flat, Choppy periods. For each type, include 50% in development vs. 50% in test.

9. **Overnight holds:** Can trades carry overnight?
   > A: Yes — hold until Supertrend stop is hit. A trade could last hours or days.

10. **Directional confluence strictness:** All 3 indicators or 2-of-3?
    > A: HMA + Supertrend must agree (core pair). VWAP adds confidence but is not required for entry.

11. **Transaction costs for backtesting:**
    > A: 0.05% per trade (0.10% round trip). Assumes VIP tier / futures fees.

---

## 6. Indicator Parameter Summary (Starting Values)

| Indicator | Parameter | Starting Value | Range to Test |
|---|---|---|---|
| HMA | Period | 21 | 9, 15, 16, 21 |
| Supertrend | ATR Period | 10 | 7, 10, 14 |
| Supertrend | Multiplier | 3.0 | 2.0, 3.0, 4.0 |
| Keltner | EMA Period | 15 | 10, 15, 20 |
| Keltner | ATR Period | 10 | 10, 14 |
| Keltner | ATR Multiplier | 2.0 | 1.5, 2.0, 2.5 |
| VWAP | Reset | 00:00 UTC daily | Daily vs Anchored |
| ADX | Period | 14 | 10, 14, 20 |
| ADX | Threshold | 25 | 20, 25, 30 |
| Bollinger (squeeze) | Period | 20 | 15, 20 |
| Bollinger (squeeze) | StdDev | 2.0 | 1.5, 2.0 |
| Volume | Confirmation multiplier | 1.5× avg | 1.2, 1.5, 2.0 |

---

## 7. BTC Market Regime Periods & Dev/Test Split

Our 5m data covers **2020-01 through 2026-01** (73 months). Below each month is classified by regime based on return % and range %. Classification rules:
- **Bull:** return > +10%
- **Bear:** return < -10%
- **Choppy:** |return| < 10% but range > 20% (big swings, no clear direction)
- **Flat:** |return| < 10% and range ≤ 20%

### Dev/Test Split Strategy

Split is on **year boundaries** to keep contiguous periods together (avoids data leakage from nearby months). We ran an exhaustive search across all possible year-groupings to find the split that best balances all four regime types at ~50/50.

**Winner: Dev = 2022, 2023, 2024 (36 months) | Test = 2020, 2021, 2025, 2026-01 (37 months)**

| Regime | Dev | Test | Total | Dev % | Balance |
|---|---|---|---|---|---|
| **Bull** | 12 | 14 | 26 | 46% | ✅ Good |
| **Bear** | 8 | 6 | 14 | 57% | ✅ Good |
| **Choppy** | 7 | 9 | 16 | 44% | ✅ Good |
| **Flat** | 9 | 8 | 17 | 53% | ✅ Good |
| **Total** | **36** | **37** | **73** | 49% | ✅ |

All regimes land within **44–57%** — the best achievable balance with year boundaries.

### Why This Split Works

- **Dev (2022–2024)** covers the full bear market, recovery, and new bull — the algorithm is tuned across all conditions
- **Test (2020–2021, 2025–2026)** includes the COVID crash/recovery, the 2021 mega-bull/crash cycle, and recent 2025–2026 action — genuinely different market structure
- No temporal overlap — Dev and Test periods are fully separated

### Full Monthly Breakdown

#### Dev Set: 2022, 2023, 2024

| Month | Open | Close | Return% | Range% | Regime |
|---|---|---|---|---|---|
| 2022-01 | 46,217 | 38,467 | -16.8% | 32.6% | **Bear** |
| 2022-02 | 38,467 | 43,160 | +12.2% | 29.9% | **Bull** |
| 2022-03 | 43,160 | 45,510 | +5.4% | 25.6% | **Choppy** |
| 2022-04 | 45,510 | 37,631 | -17.3% | 21.7% | **Bear** |
| 2022-05 | 37,631 | 31,801 | -15.5% | 35.4% | **Bear** |
| 2022-06 | 31,801 | 19,942 | -37.3% | 45.2% | **Bear** |
| 2022-07 | 19,942 | 23,293 | +16.8% | 29.5% | **Bull** |
| 2022-08 | 23,296 | 20,050 | -13.9% | 24.4% | **Bear** |
| 2022-09 | 20,048 | 19,423 | -3.1% | 23.3% | **Choppy** |
| 2022-10 | 19,423 | 20,491 | +5.5% | 14.9% | **Flat** |
| 2022-11 | 20,491 | 17,164 | -16.2% | 29.3% | **Bear** |
| 2022-12 | 17,166 | 16,542 | -3.6% | 12.4% | **Flat** |
| 2023-01 | 16,542 | 23,125 | +39.8% | 45.1% | **Bull** |
| 2023-02 | 23,125 | 23,142 | +0.1% | 16.9% | **Flat** |
| 2023-03 | 23,142 | 28,465 | +23.0% | 41.6% | **Bull** |
| 2023-04 | 28,465 | 29,233 | +2.7% | 14.3% | **Flat** |
| 2023-05 | 29,233 | 27,210 | -6.9% | 13.7% | **Flat** |
| 2023-06 | 27,210 | 30,472 | +12.0% | 24.4% | **Bull** |
| 2023-07 | 30,472 | 29,232 | -4.1% | 9.7% | **Flat** |
| 2023-08 | 29,232 | 25,941 | -11.3% | 17.4% | **Bear** |
| 2023-09 | 25,941 | 26,963 | +3.9% | 10.0% | **Flat** |
| 2023-10 | 26,963 | 34,640 | +28.5% | 32.4% | **Bull** |
| 2023-11 | 34,640 | 37,724 | +8.9% | 12.6% | **Flat** |
| 2023-12 | 37,724 | 42,284 | +12.1% | 18.8% | **Bull** |
| 2024-01 | 42,284 | 42,580 | +0.7% | 24.6% | **Choppy** |
| 2024-02 | 42,580 | 61,131 | +43.6% | 51.9% | **Bull** |
| 2024-03 | 61,131 | 71,280 | +16.6% | 24.2% | **Bull** |
| 2024-04 | 71,280 | 60,672 | -14.9% | 19.1% | **Bear** |
| 2024-05 | 60,672 | 67,540 | +11.3% | 25.4% | **Bull** |
| 2024-06 | 67,540 | 62,772 | -7.1% | 20.1% | **Choppy** |
| 2024-07 | 62,772 | 64,628 | +3.0% | 26.4% | **Choppy** |
| 2024-08 | 64,628 | 58,974 | -8.7% | 25.8% | **Choppy** |
| 2024-09 | 58,974 | 63,328 | +7.4% | 23.7% | **Choppy** |
| 2024-10 | 63,328 | 70,292 | +11.0% | 23.2% | **Bull** |
| 2024-11 | 70,292 | 96,408 | +37.2% | 46.6% | **Bull** |
| 2024-12 | 96,408 | 93,576 | -2.9% | 18.5% | **Flat** |

#### Test Set: 2020, 2021, 2025, 2026-01

| Month | Open | Close | Return% | Range% | Regime |
|---|---|---|---|---|---|
| 2020-01 | 7,195 | 9,353 | +30.0% | 37.6% | **Bull** |
| 2020-02 | 9,352 | 8,524 | -8.9% | 22.0% | **Choppy** |
| 2020-03 | 8,524 | 6,410 | -24.8% | 63.4% | **Bear** |
| 2020-04 | 6,412 | 8,620 | +34.4% | 51.6% | **Bull** |
| 2020-05 | 8,620 | 9,448 | +9.6% | 22.6% | **Choppy** |
| 2020-06 | 9,448 | 9,139 | -3.3% | 16.4% | **Flat** |
| 2020-07 | 9,138 | 11,335 | +24.0% | 27.9% | **Bull** |
| 2020-08 | 11,335 | 11,650 | +2.8% | 17.2% | **Flat** |
| 2020-09 | 11,650 | 10,777 | -7.5% | 19.1% | **Flat** |
| 2020-10 | 10,777 | 13,791 | +28.0% | 34.6% | **Bull** |
| 2020-11 | 13,791 | 19,696 | +42.8% | 48.4% | **Bull** |
| 2020-12 | 19,696 | 28,924 | +46.9% | 59.5% | **Bull** |
| 2021-01 | 28,924 | 33,093 | +14.4% | 47.8% | **Bull** |
| 2021-02 | 33,093 | 45,136 | +36.4% | 78.7% | **Bull** |
| 2021-03 | 45,134 | 58,741 | +30.1% | 37.4% | **Bull** |
| 2021-04 | 58,739 | 57,694 | -1.8% | 30.5% | **Choppy** |
| 2021-05 | 57,697 | 37,254 | -35.4% | 51.1% | **Bear** |
| 2021-06 | 37,254 | 35,045 | -5.9% | 33.6% | **Choppy** |
| 2021-07 | 35,045 | 41,462 | +18.3% | 37.6% | **Bull** |
| 2021-08 | 41,462 | 47,101 | +13.6% | 31.8% | **Bull** |
| 2021-09 | 47,101 | 43,824 | -7.0% | 28.3% | **Choppy** |
| 2021-10 | 43,820 | 61,300 | +39.9% | 54.1% | **Bull** |
| 2021-11 | 61,300 | 56,951 | -7.1% | 25.7% | **Choppy** |
| 2021-12 | 56,951 | 46,217 | -18.8% | 29.9% | **Bear** |
| 2025-01 | 93,576 | 102,430 | +9.5% | 21.7% | **Choppy** |
| 2025-02 | 102,430 | 84,350 | -17.7% | 23.9% | **Bear** |
| 2025-03 | 84,350 | 82,550 | -2.1% | 21.8% | **Choppy** |
| 2025-04 | 82,550 | 94,172 | +14.1% | 25.7% | **Bull** |
| 2025-05 | 94,172 | 104,592 | +11.1% | 19.8% | **Bull** |
| 2025-06 | 104,592 | 107,146 | +2.4% | 11.8% | **Flat** |
| 2025-07 | 107,147 | 115,764 | +8.0% | 16.9% | **Flat** |
| 2025-08 | 115,764 | 108,246 | -6.5% | 14.8% | **Flat** |
| 2025-09 | 108,246 | 114,049 | +5.4% | 9.8% | **Flat** |
| 2025-10 | 114,049 | 109,608 | -3.9% | 21.2% | **Choppy** |
| 2025-11 | 109,608 | 90,360 | -17.6% | 28.0% | **Bear** |
| 2025-12 | 90,360 | 87,648 | -3.0% | 11.9% | **Flat** |
| 2026-01 | 87,648 | 78,741 | -10.2% | 25.3% | **Bear** |

### Regime Counts Per Year

| Year | Bull | Bear | Choppy | Flat | Dominant Character |
|---|---|---|---|---|---|
| 2020 | 6 | 1 | 2 | 3 | Bull rally (COVID crash → recovery) |
| 2021 | 6 | 2 | 4 | 0 | Bull → crash (volatile year) |
| 2022 | 2 | 6 | 2 | 2 | Bear market (Luna/FTX) |
| 2023 | 5 | 1 | 0 | 6 | Recovery + consolidation |
| 2024 | 5 | 1 | 5 | 1 | ETF rally + choppy ranges |
| 2025 | 2 | 2 | 3 | 5 | Mixed / uncertain |
| 2026 | 0 | 1 | 0 | 0 | Bear (1 month only) |

### Key Observations
- BTC is **bull-biased** (26/73 months = 36%) — the algorithm must capture uptrends effectively
- Bear + Choppy = 30 months (41%) — regime detection is critical to avoid losses in these periods
- Flat months (17) are where the system should mostly sit idle — over-trading in flat markets is a common failure mode
- The Dev/Test split keeps year boundaries intact and achieves 44–57% balance across all regime types

---

## 8. Sources

- [Capital.com — Hull Moving Average Strategy](https://capital.com/en-int/learn/technical-analysis/hull-moving-average)
- [HullMovingAverage.com — Best Settings for Day Trading](https://hullmovingaverage.com/hull-moving-average-settings-day-trading/)
- [Mudrex — Supertrend Indicator: Formula, Best Settings](https://mudrex.com/learn/supertrend-indicator/)
- [MQL5 — Best SuperTrend Settings for 5-Minute Chart](https://www.mql5.com/en/blogs/post/763975)
- [FXOpen — Supertrend Indicator for Crypto Day Trading](https://fxopen.com/blog/en/how-to-use-the-supertrend-indicator-to-day-trade-crypto/)
- [GoodCrypto — Supertrend Indicator Setup](https://goodcrypto.app/supertrend-indicator-how-to-set-up-use-and-create-profitable-crypto-trading-strategy/)
- [QuantifiedStrategies — Keltner Channel Trading Strategy (77% WinRate)](https://www.quantifiedstrategies.com/keltner-bands-trading-strategies/)
- [LuxAlgo — Keltner Channel Strategy](https://www.luxalgo.com/blog/keltner-channel-strategy-surf-volatility-bands/)
- [KeltnerChannel.com — Multiplier Settings](https://keltnerchannel.com/keltner-channel-multiplier-settings/)
- [StockCharts — TTM Squeeze](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ttm-squeeze)
- [Medium — Multi-Indicator Trend Momentum Strategy (SuperTrend)](https://medium.com/@redsword_23261/multi-indicator-trend-momentum-trading-strategy-based-on-supertrend-1ed8ab458032)
- [TradingView — HMA 50 + Supertrend Sniper](https://www.tradingview.com/script/3SHJZytF/)
- [PyQuantLab — Regime Filtered Trend Strategy](https://pyquantlab.medium.com/regime-filtered-trend-strategy-a-market-adaptive-trend-following-system-fa933e001237)

---

*Last updated: 2026-03-06 — Research complete, ready for design*
