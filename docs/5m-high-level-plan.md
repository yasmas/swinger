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
│  • If regime = CONSOLIDATING → all signals ignored       │
├──────────────────────────────────────────────────────────┤
│  Layer 2: DIRECTIONAL BIAS (require ALL to agree)        │
│  "Which direction should we trade?"                      │
│                                                          │
│  • HMA(21) slope: rising = long, falling = short         │
│  • Supertrend(10,3): above = bullish, below = bearish    │
│  • VWAP position: price above = bullish, below = bearish │
│  • If ANY disagree → "Choppy Zone" → no trade            │
├──────────────────────────────────────────────────────────┤
│  Layer 3: ENTRY TRIGGER                                  │
│  "When exactly do we enter?"                             │
│                                                          │
│  LONG: 5m candle closes above upper Keltner Channel      │
│        with volume > 1.5× average                        │
│  SHORT: 5m candle closes below lower Keltner Channel     │
│        with volume > 1.5× average                        │
│  (Alternative entry: VWAP pullback + bounce in trend)    │
├──────────────────────────────────────────────────────────┤
│  Layer 4: RISK MANAGEMENT / EXIT                         │
│  "How do we manage and exit the trade?"                  │
│                                                          │
│  • Position size: risk 2% of capital per trade           │
│    (size = 2% × capital / distance_to_stop)              │
│  • Trailing stop: Supertrend(10,3) line                  │
│  • Hard stop: initial Supertrend level at entry          │
│  • Daily circuit breaker: 6% max daily drawdown          │
│  • Time exit: max hold period (TBD, e.g., 4–8 hours)    │
│  • Profit target: optional, VWAP band or 2× risk        │
└──────────────────────────────────────────────────────────┘
```

**Signal flow:** Regime OK → Direction unanimous → Entry trigger fires → Enter with calculated position size → Trail with Supertrend → Exit when Supertrend flips or stop hit.

---

## 5. Design Questions & User Answers

1. **Timeframe:** Pure 5m system, or multi-timeframe (1h direction + 5m entries)?
   > A: What is more likely to bring higher win rate?
   > **Resolution:** Research says multi-timeframe (1h direction + 5m entry) typically produces higher win rates. However, our 4-indicator confluence on 5m alone may be sufficient since we have multiple directional filters. **Recommend: start with pure 5m, add 1h overlay as v2 enhancement if needed.**

2. **Trade frequency:** How many trades per day is acceptable?
   > A: 2-5

3. **Position sizing:** All-in per trade or scaled entries?
   > A: Keep it simple for now. All-in. We can use scaled entries later.
   > **Note:** "All-in" will be constrained by the 2% risk rule — position size determined by stop distance, not a fixed percentage of capital. In practice this means position sizes will vary per trade.

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

## 7. Sources

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
