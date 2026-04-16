# QQQ & QLD — Optimized LazySwing Strategy

## Summary

Ran a two-stage Supertrend parameter optimization for the LazySwing strategy on QQQ and QLD, testing both 1h and 30m resampled bars.

**Data:** Split-adjusted 5m bars from Massive (Polygon), resampled internally to 1h or 30m.
- Dev set: 2024-01-01 → 2025-12-31
- Live set: 2026-01-01 → 2026-04-16

**Process:**
1. Downloaded adjusted data via `MassiveRestClient` (Databento was rejected — unadjusted, QLD 2-for-1 split on Nov 20 2025 caused a fake -50% gap that corrupted results)
2. Stage 1 broad sweep: ATR [5,10,15,20] × Mult [1.5, 2.0, 2.5] — identified mult=1.5 cluster as dominant
3. Stage 2 narrow sweep: ATR ±2 around winner, mult [1.25, 1.50, 1.75] — found mult=1.25 consistently outperforms; 30m resample beat 1h by ~2-3×
4. Extended ATR upward in both timeframes to find the ceiling

**Key findings:**
- `mult=1.25` dominates across all ATR values on both tickers — tighter bands compound better on an always-in-market strategy
- 30m resample produces ~2-3× the dev return of 1h, at the cost of ~2× trade count
- Longer ATR smooths signal (fewer trades) while maintaining or improving return and WR — the trend continued until ATR=20 (QQQ) and ATR=25 (QLD) on 30m

---

## QQQ

### 30m Resample — Top 5

| | ATR | Mult | Return% | Sharpe | MaxDD% | WR% | #Trades |
|--|-----|------|---------|--------|--------|-----|---------|
| | 9 | 1.25 | +8,951% | 13.03 | -6.38 | 58% | 1,174 |
| | 10 | 1.25 | +9,153% | 12.97 | -6.38 | 58% | 1,180 |
| | 11 | 1.25 | +9,196% | 12.93 | -6.38 | 58% | 1,171 |
| | 15 | 1.25 | +10,261% | 12.97 | -6.38 | 60% | 1,161 |
| **★** | **20** | **1.25** | **+10,868%** | **13.02** | **-6.38** | **61%** | **1,160** |

**Winner: ATR=20, Mult=1.25** — highest return and WR, same MaxDD as all others, fewest trades.

Live set (2026-01-01 → 2026-04-16): **+117.0%** | Sharpe 14.87 | MaxDD -2.03% | WR 62% | 158 trades

---

### 1h Resample — Top 5

| | ATR | Mult | Return% | Sharpe | MaxDD% | WR% | #Trades |
|--|-----|------|---------|--------|--------|-----|---------|
| | 5 | 1.25 | +5,777% | 12.11 | -4.92 | 69% | 592 |
| | 9 | 1.25 | +5,547% | 11.82 | -4.92 | 70% | 581 |
| | 10 | 1.25 | +5,556% | 11.79 | -4.92 | 70% | 585 |
| **★** | **12** | **1.25** | **+5,955%** | **11.53** | **-4.43** | **72%** | **575** |
| | 15 | 1.25 | +5,772% | 11.33 | -4.43 | 71% | 571 |

**Winner: ATR=12, Mult=1.25** — best return, best WR, best MaxDD tier (-4.43 vs -4.92), fewest trades among this group.

Live set (2026-01-01 → 2026-04-16): **+90.0%** | Sharpe 12.83 | MaxDD -3.34% | WR 70% | 79 trades

---

## QLD

### 30m Resample — Top 5

| | ATR | Mult | Return% | Sharpe | MaxDD% | WR% | #Trades |
|--|-----|------|---------|--------|--------|-----|---------|
| | 16 | 1.25 | +1,604,401% | 13.36 | -12.09 | 62% | 1,332 |
| | 17 | 1.25 | +1,704,142% | 13.41 | -12.09 | 63% | 1,324 |
| | 20 | 1.25 | +1,732,275% | 13.37 | -12.09 | 63% | 1,319 |
| | 20 | 1.50 | +564,489% | 12.33 | -13.08 | 62% | 1,091 |
| **★** | **25** | **1.25** | **+1,900,723%** | **13.38** | **-12.09** | **63%** | **1,322** |

**Winner: ATR=25, Mult=1.25** — highest return, same best MaxDD tier, tied WR. The MaxDD of -12.09% reflects 2× leverage on the underlying — not a strategy failure. `mult=1.50` row included to show the return gap between tight and medium bands.

Live set (2026-01-01 → 2026-04-16): **+456.6%** | Sharpe 15.87 | MaxDD -3.92% | WR 68% | 159 trades

---

### 1h Resample — Top 5

| | ATR | Mult | Return% | Sharpe | MaxDD% | WR% | #Trades |
|--|-----|------|---------|--------|--------|-----|---------|
| | 5 | 1.25 | +459,547% | 13.04 | -9.48 | 72% | 683 |
| | 7 | 1.25 | +463,846% | 13.03 | -9.48 | 72% | 677 |
| | 9 | 1.25 | +469,151% | 12.86 | -9.48 | 72% | 675 |
| | 10 | 1.25 | +513,896% | 12.77 | -9.48 | 72% | 665 |
| **★** | **12** | **1.25** | **+551,207%** | **12.81** | **-9.48** | **73%** | **662** |

**Winner: ATR=12, Mult=1.25** — highest return, best WR, all combos share the same MaxDD tier (-9.48). ATR=15 and ATR=20 were tested but MaxDD jumps to -13.56 and -14.60 respectively — not worth it.

Live set (2026-01-01 → 2026-04-16): **+327.0%** | Sharpe 13.51 | MaxDD -4.53% | WR 76% | 86 trades

---

## Summary Comparison

| Ticker | Resample | ATR | Mult | Dev Return | Dev WR | Live Return | Live WR |
|--------|----------|-----|------|-----------|--------|------------|---------|
| QQQ | 30m | 20 | 1.25 | +10,868% | 61% | +117% | 62% |
| QQQ | 1h | 12 | 1.25 | +5,955% | 72% | +90% | 70% |
| QLD | 30m | 25 | 1.25 | +1,900,723% | 63% | +457% | 68% |
| QLD | 1h | 12 | 1.25 | +551,207% | 73% | +327% | 76% |

30m wins on return; 1h wins on WR and MaxDD. The choice depends on risk tolerance and acceptable trade frequency.

---

## Config Reference

**QQQ 30m winner:**
```yaml
strategies:
  - type: lazy_swing
    params:
      supertrend_atr_period: 20
      supertrend_multiplier: 1.25
      resample_interval: "30min"
      cost_per_trade_pct: 0.05
```

**QQQ 1h winner:**
```yaml
strategies:
  - type: lazy_swing
    params:
      supertrend_atr_period: 12
      supertrend_multiplier: 1.25
      resample_interval: "1h"
      cost_per_trade_pct: 0.05
```

**QLD 30m winner:**
```yaml
strategies:
  - type: lazy_swing
    params:
      supertrend_atr_period: 25
      supertrend_multiplier: 1.25
      resample_interval: "30min"
      cost_per_trade_pct: 0.05
```

**QLD 1h winner:**
```yaml
strategies:
  - type: lazy_swing
    params:
      supertrend_atr_period: 12
      supertrend_multiplier: 1.25
      resample_interval: "1h"
      cost_per_trade_pct: 0.05
```

**Data source** (all): `MassiveRestClient` with `adjusted=true` — do not substitute with Databento or other unadjusted feeds.

---

## Wall of Fame Files

All winning artifacts are in `data/wall-of-fame/lazyswing/`.

| Ticker | Resample | Report | Trade Log | Config |
|--------|----------|--------|-----------|--------|
| QQQ | 30m | [HTML](../data/wall-of-fame/lazyswing/QQQ-lazyswing-30m-atr20-mult1.25.html) | [CSV](../data/wall-of-fame/lazyswing/QQQ-lazyswing-30m-atr20-mult1.25.csv) | [YAML](../data/wall-of-fame/lazyswing/QQQ-lazyswing-30m-atr20-mult1.25.yaml) |
| QQQ | 1h | [HTML](../data/wall-of-fame/lazyswing/QQQ-lazyswing-1h-atr12-mult1.25.html) | [CSV](../data/wall-of-fame/lazyswing/QQQ-lazyswing-1h-atr12-mult1.25.csv) | [YAML](../data/wall-of-fame/lazyswing/QQQ-lazyswing-1h-atr12-mult1.25.yaml) |
| QLD | 30m | [HTML](../data/wall-of-fame/lazyswing/QLD-lazyswing-30m-atr25-mult1.25.html) | [CSV](../data/wall-of-fame/lazyswing/QLD-lazyswing-30m-atr25-mult1.25.csv) | [YAML](../data/wall-of-fame/lazyswing/QLD-lazyswing-30m-atr25-mult1.25.yaml) |
| QLD | 1h | [HTML](../data/wall-of-fame/lazyswing/QLD-lazyswing-1h-atr12-mult1.25.html) | [CSV](../data/wall-of-fame/lazyswing/QLD-lazyswing-1h-atr12-mult1.25.csv) | [YAML](../data/wall-of-fame/lazyswing/QLD-lazyswing-1h-atr12-mult1.25.yaml) |
