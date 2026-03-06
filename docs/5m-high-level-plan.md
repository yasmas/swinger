# Intraday Trading System — Research & High-Level Plan

## Status: RESEARCH IN PROGRESS

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

### 2.2 Hull Moving Average (HMA) (🔬 Partial — From Ideas Doc + General Knowledge)

**What it is:** A moving average designed to eliminate lag while remaining smooth. Uses weighted moving averages and the square root of the period.

**Formula:**
```
Step 1: WMA_half = WMA(close, period/2)
Step 2: WMA_full = WMA(close, period)
Step 3: diff = 2 × WMA_half - WMA_full
Step 4: HMA = WMA(diff, sqrt(period))
```

**Proposed Role in System:**
- **Directional filter on 5m chart:** HMA slope up = long bias, slope down = short bias
- Much faster than EMA at detecting trend changes
- Typical periods for 5m chart: 20-period HMA

**Known Concerns:**
- Can overshoot in volatile markets (the lag-removal math can cause it to "predict" moves that don't materialize)
- More prone to whipsaws in ranging markets than slower MAs
- Needs web research for optimal crypto-specific parameters

---

### 2.3 Keltner Channels (🔬 Partial — From Ideas Doc + General Knowledge)

**What it is:** Volatility envelope around an EMA, using ATR (not standard deviation like Bollinger Bands) for band width.

**Formula:**
```
Middle = EMA(close, period)
Upper = Middle + multiplier × ATR(period)
Lower = Middle - multiplier × ATR(period)
```

**Proposed Role in System:**
- **Breakout detection:** Close outside upper channel with expanding volume = trend breakout
- **"Riding the band":** In strong trends, price hugs the outer band — this is STRENGTH not overbought
- **Squeeze detection:** When Bollinger Bands move inside Keltner Channels, volatility is compressed → breakout imminent

**Key Advantage Over Bollinger Bands:**
- ATR-based bands expand more smoothly during volatility, avoiding the extreme "ballooning" of Bollinger Bands
- Better for trend-following (Bollinger better for mean-reversion)

**Needs Further Research:**
- Optimal period and ATR multiplier for BTC 5m bars
- Backtest results in crypto context

---

### 2.4 Supertrend (🔬 Partial — From Ideas Doc + General Knowledge)

**What it is:** A trend-following overlay that uses ATR to create a dynamic trailing stop line above or below price.

**Formula:**
```
Basic Upper Band = (High + Low) / 2 + multiplier × ATR(period)
Basic Lower Band = (High + Low) / 2 - multiplier × ATR(period)

If price closes above Upper Band → Supertrend flips to BULLISH (line below price)
If price closes below Lower Band → Supertrend flips to BEARISH (line above price)
```

**Proposed Role in System:**
- **Automated trailing stop:** Once in a trade, exit when price closes beyond the Supertrend line
- **Trend filter:** Only trade in the direction the Supertrend indicates
- Typical parameters: ATR period 10, multiplier 3.0

**Key Advantage:**
- Only flips when price structurally breaks through the volatility threshold
- Acts as both directional filter AND exit mechanism
- Simple, binary signal — reduces decision complexity

**Needs Further Research:**
- Optimal ATR period and multiplier for BTC 5m bars
- Performance in crypto choppy markets
- Comparison with our current ATR-based trailing stop

---

## 3. Research Still Needed

- [ ] **HMA deep dive:** Optimal periods for crypto 5m, backtest results, failure modes
- [ ] **Supertrend deep dive:** Best parameters for BTC, comparison with fixed % stops, crypto backtests
- [ ] **Keltner Channels deep dive:** Optimal settings, squeeze strategy backtests
- [ ] **Combined system architecture:** How to layer all indicators into a coherent entry/exit system
- [ ] **Multi-timeframe analysis:** Using 1h for direction + 5m for entry timing
- [ ] **Regime detection:** How to avoid trading in choppy/ranging markets
- [ ] **Session-based patterns:** BTC liquidity patterns by time of day (Asia/Europe/US)
- [ ] **Risk management:** Intraday position sizing, max daily loss, time exits
- [ ] **Role of existing indicators:** Does RSI/MACD/ADX still add value alongside these newer ones?

---

## 4. Emerging Architecture Concept

Based on research so far, the system is shaping up as a **layered confluence model**:

```
┌─────────────────────────────────────────────────┐
│  Layer 1: REGIME FILTER                         │
│  "Should we be trading at all right now?"       │
│  • ADX for trend strength                       │
│  • Keltner/Bollinger squeeze for consolidation  │
│  • Volume threshold                             │
│  • Time-of-day filter (avoid dead hours)        │
├─────────────────────────────────────────────────┤
│  Layer 2: DIRECTIONAL BIAS                      │
│  "Which direction should we trade?"             │
│  • HMA slope on 5m (fast directional read)      │
│  • VWAP position (above = bullish bias)         │
│  • Supertrend direction                         │
│  • Higher timeframe trend (1h EMA/MACD)         │
├─────────────────────────────────────────────────┤
│  Layer 3: ENTRY TRIGGER                         │
│  "When exactly do we enter?"                    │
│  • Keltner Channel breakout                     │
│  • VWAP pullback + bounce                       │
│  • Volume confirmation                          │
├─────────────────────────────────────────────────┤
│  Layer 4: RISK MANAGEMENT / EXIT                │
│  "How do we manage and exit the trade?"         │
│  • Supertrend as trailing stop                  │
│  • ATR-based position sizing                    │
│  • Time-based exit (max hold period)            │
│  • VWAP band targets                            │
│  • Max daily loss circuit breaker               │
└─────────────────────────────────────────────────┘
```

This is a **preliminary sketch** — will be refined after completing all research and user Q&A.

---

## 5. Key Design Questions (To Discuss With User)

1. **Timeframe:** Pure 5m system, or multi-timeframe (1h direction + 5m entries)?
2. **Trade frequency:** How many trades per day is acceptable? (Affects transaction cost impact)
3. **Position sizing:** All-in per trade (like current system) or scaled entries?
4. **Short selling:** Include short positions for intraday, or long-only?
5. **Session awareness:** Should the system behave differently during Asia/Europe/US sessions?
6. **Integration with existing system:** Separate strategy class, or extension of MACD/RSI?
7. **Risk budget:** Max daily drawdown tolerance? Max single-trade risk?
8. **Backtesting period:** What data range to use for development vs. out-of-sample testing?

---

*Last updated: 2026-03-05 — Research ongoing*
