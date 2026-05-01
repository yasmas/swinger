# Experiment: Supertrend Vortex ADX for QQQ 2026

## Goal

Design and test a Supertrend-led QQQ trading system using the indicators already
available in the MACD/Vortex report:

- Supertrend as the primary direction trigger.
- EMA-smoothed Vortex as the directional confirmation.
- ADX as the trend-strength confirmation.
- Optional realised-volatility ratio gate inspired by LazySwing's RVOL work.

The target is ambitious by design: beat QQQ buy-and-hold by at least 2x on the
available local 2026 QQQ slice after realistic trading costs.

## Local Data Window

- Symbol: `QQQ`
- Source file: `data/QQQ-5m-2026.csv`
- Requested window: `2026-01-01` to `2026-12-31`
- Local file currently ends in April 2026, so every result in this experiment is
  partial-year 2026 unless explicitly noted otherwise.

## Research Sources

- StockCharts ADX guidance: ADX measures trend strength, not direction, and
  common interpretation treats the 20-25 area as emerging-to-strong trend.
- TradingView and common Supertrend documentation: Supertrend is ATR-based,
  lower multipliers react faster but increase flip-flops.
- Vortex guidance: Vortex crossovers are useful direction signals but should be
  filtered because shorter/faster settings can whipsaw.

Source links:

- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx
- https://www.tradingview.com/support/solutions/43000634738-supertrend/
- https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/vortex-indicator

## Initial Thesis

The MACD/Vortex strategy has been entering late because MACD alert freshness and
Vortex confirmation can fall out of sync. Supertrend is a better state machine
for this problem: it already declares the market direction and trails price.

The core rule should not be "ST flipped, blindly reverse." That is exactly where
fast ST settings get chopped up. Instead:

1. When Supertrend flips bullish, reverse long only if Vortex EMA and ADX agree.
2. When Supertrend flips bearish, reverse short only if Vortex EMA and ADX agree.
3. If they do not agree, keep the existing position and mark the trade as
   contradicting Supertrend.
4. While contradicting, keep checking the same filters on each completed signal
   bar. If they confirm, take the delayed flip.
5. If Supertrend flips back before confirmation, cancel the contradiction and
   keep riding the existing side.

This keeps the always-in-market compounding style from LazySwing, while trying
to reject the fast ST flip-flops that do not have directional pressure behind
them.

## Candidate Filter Definitions

### Supertrend

- Default candidate: `ST(12, 1.5)` on completed `30min` bars.
- This is intentionally fast, matching the current report overlay and the user's
  chart review.

### Vortex EMA Direction

- Compute `Vortex(period)` on completed signal bars.
- Smooth `V+` and `V-` with a very short EMA.
- Bull confirmation passes when `EMA(V+) >= EMA(V-)`, or the one-bar projected
  difference is about to cross bullish.
- Bear confirmation is mirrored.

### ADX Confirmation

- Compute `ADX(period)` on completed signal bars.
- Confirmation passes when `ADX >= floor`.
- A near-pass can also pass when ADX is close to the floor and rising quickly.

### RVOL Ratio Gate

- Optional gate based on LazySwing's realised-volatility ratio idea:
  short realised volatility divided by its shifted longer baseline.
- The intent is to accept flips when volatility expansion says the move has
  enough energy, and reject flips during dead chop.

## Progress Log

### Standing Research Rule - Trade Review After Every Run

After every completed strategy run, do not judge the system only by summary
metrics. Inspect the actual trade sequence:

- BUY entries
- SELL exits
- SHORT entries
- COVER exits

For each representative winner, loser, and missed major move, compare the
action against the price chart and indicator state. Ask whether the action was
optimal, early, late, missing, or a whipsaw. The next version must be motivated
by those observed trade failures, not by metric-chasing alone.

### Step 1 - Research Harness

Added a dedicated script:

- `scripts/grid_search_st_vortex_adx_qqq_2026.py`

The first harness is deliberately independent of the backtest controller so it
can test many state-machine variants quickly. Once a candidate clears the bar,
it should be promoted into a proper strategy class and config.

### Step 2 - Compact Candidate Family

Added a smaller, inspectable runner:

- `scripts/research_st_vortex_adx_candidate.py`

This tests a handful of trader-designed candidates instead of a giant grid. The
first corrected research pass found that shorting QQQ hurt the system. The best
standalone candidate was long-only:

- `ST(12, 1.5)` on completed 30m bars
- `Vortex(21)` with `EMA(3)` on V+ and V-
- `ADX(12)` floor 20, with near-floor recovery allowed at 18 plus slope >= 0.6
- RTH-only flip decisions
- no shorts

Standalone research result:

- QQQ B&H: `+4.23%`
- Candidate after costs: `+9.80%`
- Round trips: `17`
- Max DD: `-6.46%`

The trade review showed the edge came mostly from catching the March 31 to April
16 upside wave while avoiding most short-side damage.

### Step 3 - Promote to Official Strategy

Added a registered strategy:

- `src/strategies/st_vortex_adx.py`
- strategy key: `st_vortex_adx`
- config: `config/strategies/st_vortex_adx/qqq_2026_candidate.yaml`

The first official controller run differed from the standalone harness because
the controller loads pre-start warmup history. That is the correct model, and it
reduced the candidate after-cost result:

- Gross: `+9.69%`
- After costs: `+7.40%`
- Trades: `44`
- Max DD: `-5.45%`
- B&H: `+4.23%`
- 2x B&H target: `+8.45%`

This beat B&H, but did not clear the 2x target after costs.

### Step 4 - Trade Review and Cooldown Rule

Trade review of the official run showed a recurring failure pattern: the system
often bought again too soon after a confirmed bearish exit. These were mostly
flip-flop zones where ST went back bullish before a cleaner trend had formed,
causing extra fees and several small losses.

Added:

- `entry_cooldown_signal_bars`

This blocks new entries for N completed signal bars after an exit. It does not
block exits, so risk can still come off immediately.

Official cooldown results:

| Cooldown | Gross | After Cost | Trades | Max DD |
| --- | ---: | ---: | ---: | ---: |
| 0 | `+9.69%` | `+7.40%` | 44 | `-5.45%` |
| 8 | `+10.28%` | `+8.10%` | 42 | `-5.02%` |
| 10 | `+10.93%` | `+8.84%` | 40 | `-5.02%` |
| 12 | `+10.20%` | `+8.12%` | 40 | `-5.14%` |

The promoted candidate is cooldown 10 because it is the first version that
clears the after-cost 2x B&H target on the official controller run.

### Current Champion

- Strategy: `st_vortex_adx`
- Config: `config/strategies/st_vortex_adx/qqq_2026_candidate.yaml`
- Signal timeframe: completed `30min`
- Execution bars: native `5min`
- Direction: long-only
- ST: `12, 1.5`
- Vortex: `21`, EMA smoothing `3`
- ADX: `12`, floor `20`, near floor `18`, slope `0.6`
- RTH-only flip decisions: enabled
- Entry cooldown after exit: `10` signal bars

Current official result:

- Gross: `+10.93%`
- After costs: `+8.84%`
- QQQ B&H: `+4.23%`
- Multiple of B&H after costs: `2.09x`
- Trades: `40`
- Max DD: `-5.02%`
- Sharpe: `2.75`

Artifacts:

- Trade log: `reports/ST_Vortex_ADX_QQQ_2026_Candidate_st_vortex_adx_v1-long-only.csv`
- HTML report: `reports/st_vortex_adx_QQQ_2026-01-01_2026-04-16_v1-long-only.html`

## Trade Review - Current Champion

The action stream has 20 round trips. The main wins:

- Jan 21 -> Jan 28: bought the bullish ST recovery and sold into the later
  confirmed bear phase, `+3.20%`.
- Mar 9 -> Mar 10: caught a sharp recovery, `+2.47%`.
- Mar 31 -> Apr 7: caught the first leg of the big April recovery, `+2.45%`.
- Apr 8 -> Apr 16: re-entered after cooldown and rode the stronger second leg,
  `+5.17%`.

The remaining weak spots:

- Mar 2 -> Mar 3: `-2.00%`, a failed long during a volatile transition.
- Mar 19 -> Mar 20: `-1.22%`, a late long in a still-fragile recovery.
- Mar 13 and Mar 11: short-lived failed longs, both exited within two hours.
- Jan 30 and Feb 11: small re-entry losses after prior exits.

Interpretation:

- The strategy is working because it lets Supertrend own the directional state
  while Vortex EMA and ADX prevent some false flips.
- Shorts are not helping QQQ in this sample. Long-only is better.
- RTH-only decisions matter. All-hours flip handling creates too many noisy
  reactions.
- The cooldown rule is the key refinement from trade review: it reduces
  immediate re-entry churn after a bearish exit without weakening exits.

Next candidate improvements should focus on the remaining March failed longs:

- Require stronger Vortex EMA alignment for re-entry after large down days.
- Add a daily trend or 4h Supertrend bias filter for longs after bearish regimes.
- Add a max adverse move or structure stop for failed long entries.
- Test whether the existing MACD/Vortex report can be extended to show
  `st_vortex_adx` trades directly for faster visual reviews.

### Step 5 - Volatility Expansion and Selective Shorts

The next review looked at the flat periods after SELL actions. Many of those
flat periods continued downward before the next BUY, so the long-only system was
leaving short-side opportunity on the table. A naive short system was bad, but
selective shorts looked promising if they required higher-timeframe bearish
context.

Implemented direction-specific filters:

- `long_vol_ratio_enabled`
- `short_vol_ratio_enabled`
- `long_vol_ratio_min`
- `short_vol_ratio_min`
- `short_entry_cooldown_signal_bars`
- `short_require_context_bearish`
- `short_context_interval`
- `short_context_supertrend_atr_period`
- `short_context_supertrend_multiplier`
- `short_context_adx_period`
- `short_context_adx_floor`

The best v2 candidate:

- Long entries require realised-volatility ratio >= `2.0`.
- Short entries require realised-volatility ratio >= `1.5`.
- Short entries require bearish `4h` Supertrend context.
- Short context ADX must be >= `20`.
- Long cooldown remains `10` signal bars after a long exit.
- Short cooldown is `0`, so confirmed bearish phases can be monetized quickly.

Official v2 result:

- Gross: `+22.86%`
- After costs: `+22.34%`
- QQQ B&H: `+4.23%`
- Multiple of B&H after costs: `5.29x`
- Trades: `10`
- Max DD: `-4.48%`
- Sharpe: `3.99`

Artifacts:

- Config: `config/strategies/st_vortex_adx/qqq_2026_candidate.yaml`
- Trade log: `reports/ST_Vortex_ADX_QQQ_2026_Candidate_st_vortex_adx_v2-short-context.csv`
- HTML report: `reports/st_vortex_adx_QQQ_2026-01-01_2026-04-16_v2-short-context.html`

Trade review:

| Side | Entry | Exit | PnL |
| --- | --- | --- | ---: |
| Long | Jan 6 | Feb 3 | `-0.22%` |
| Short | Feb 3 | Feb 18 | `+1.58%` |
| Long | Feb 20 | Feb 26 | `+0.16%` |
| Short | Feb 26 | Mar 31 | `+7.74%` |
| Long | Mar 31 | Apr 16 | `+12.94%` |

Interpretation:

- The higher volatility-ratio requirement intentionally trades less. It removes
  much of the March long chop that hurt v1.
- Selective shorts worked only when the 30m bearish phase agreed with bearish
  4h context. Lower context thresholds took too many shorts and gave back
  performance.
- The system now behaves more like a regime strategy than a frequent intraday
  trader: it waits for high-energy ST transitions, rides the regime, and ignores
  most flip noise.

Risk note:

- This result clears the 4x target on the local 2026 QQQ slice, but the slice is
  short and the champion uses only five round trips. The next serious validation
  step is out-of-sample testing on older QQQ/QLD data and a walk-forward split.

### Step 6 - Exit-Only Outside-RTH Core Flip Test

Hypothesis from visual review:

- Feb 17/18 showed a short that could have been covered earlier outside regular
  hours when Supertrend flipped bullish and Vortex/ADX core agreement was
  already present.
- To avoid over-relaxing the system, test only risk-reducing exits outside RTH.
  Do not open a new opposite position outside RTH.

Implemented optional parameter:

- `exit_outside_rth_on_core_flip`

Rule:

- If `rth_only_flips=true` and the current signal bar is outside RTH, still
  allow an existing short to `COVER` on a bullish ST flip when Vortex+ADX agree.
- Symmetrically, allow an existing long to `SELL` on a bearish ST flip when
  Vortex+ADX agree.
- Do not `BUY` or `SHORT` from flat outside RTH.

Result on the same 2026 QQQ slice:

- Gross: `+4.36%`
- After costs: `+3.17%`
- Trades: `24`
- Round trips: `12`
- Win rate: `50.0%`
- Max DD: `-7.04%`
- Sharpe: `1.02`
- The baseline was `+22.86%` gross / `+22.34%` after costs.

Trade review:

- The rule did reduce exposure earlier in some places, but it fired too often.
- It cut good positions early on Jan 23, Feb 11, Feb 23, Mar 13, Mar 23,
  Mar 30, Mar 31, and Apr 7.
- The Feb 17/18 case was not enough to offset the extra whipsaw exits.

Decision:

- Do not promote this rule into the champion config.
- Keep the implementation behind `exit_outside_rth_on_core_flip=false` so it can
  be reused for narrower variants later.

Artifacts:

- Experiment config:
  `config/strategies/st_vortex_adx/qqq_2026_exit_only_core.yaml`
- Experiment report:
  `reports/st_vortex_adx_QQQ_2026-01-01_2026-04-16_v3-exit-only-core.html`
- Champion report restored:
  `reports/st_vortex_adx_QQQ_2026-01-01_2026-04-16_v2-short-context.html`

Next narrower variants worth testing:

- Short-cover only, not long-sell symmetry.
- Outside-RTH exit only if the existing trade is already profitable by at least
  an ATR or percent threshold.
- Outside-RTH exit only during premarket after 7:00 ET, not overnight.
- Outside-RTH exit only if the next RTH open gaps against the active position.

### Step 7 - Out-of-Sample Check on QQQ 2025 H2

Purpose:

- Test the champion `v2-short-context` parameters on a different QQQ regime.
- Check whether the strong 2026 result was dependent on one large March/April
  trade.

Dataset:

- Source: `data/QQQ-5m-2024-2025.csv`
- Backtest window: `2025-07-01` through `2025-12-31`
- H2 buy-and-hold return from first/last available 5m closes: `+11.62%`

Champion parameters were unchanged:

- Config: `config/strategies/st_vortex_adx/qqq_2025_h2_champion.yaml`
- `exit_outside_rth_on_core_flip=false`
- Long RVOL gate `>= 2.0`
- Short RVOL gate `>= 1.5`
- Short requires bearish `4h` Supertrend context and context ADX `>= 20`

Result:

- Gross: `+2.79%`
- After costs: `+1.84%`
- QQQ B&H: `+11.62%`
- After-cost multiple of B&H: `0.16x`
- Trades: `18`
- Round trips: `9`
- Win rate: `44.4%`
- Max DD: `-11.18%`
- Sharpe: `0.43`

Round-trip review:

| Side | Entry | Exit | PnL |
| --- | --- | --- | ---: |
| Long | Jul 2 | Sep 2 | `+2.28%` |
| Long | Sep 3 | Sep 25 | `+3.50%` |
| Short | Sep 25 | Oct 8 | `-2.51%` |
| Long | Oct 15 | Oct 30 | `+4.33%` |
| Short | Oct 30 | Nov 14 | `+3.51%` |
| Short | Nov 17 | Dec 5 | `-3.74%` |
| Long | Dec 5 | Dec 15 | `-2.55%` |
| Short | Dec 16 | Dec 19 | `-1.27%` |
| Long | Dec 19 | Dec 31 | `-0.08%` |

Interpretation:

- This is a failed out-of-sample validation. The strategy made money, but badly
  underperformed buy-and-hold.
- It was not a single-trade sample in 2025 H2; the system took nine round trips.
- The largest issue was short-side regime handling in an upward market. Three of
  four shorts lost money, and the profitable Oct/Nov short was not enough to
  offset the damage.
- The long filters were also too selective for an H2 uptrend; the system missed
  a large amount of passive QQQ upside while trying to avoid chop.

Next implication:

- Do not call `v2-short-context` robust yet.
- The next serious improvement should separate bull-market and bear-market
  regimes, likely with a daily/4h market bias that either disables shorts or
  requires much stronger short evidence during broad uptrends.

### Step 8 - Generalization Review Across 2025 H2 and 2026 YTD

Purpose:

- Stop optimizing around the 2026 March/April winner.
- Compare variants across both `2025H2` and `2026YTD`.
- Identify general rule failures: late trend entry, bad trades, and late exits.

Variant table:

| Variant | 2025H2 after cost | 2026YTD after cost | Notes |
| --- | ---: | ---: | --- |
| `champion` | `+1.84%` | `+22.34%` | Best 2026, weak 2025. |
| `no_long_rvol` | `+5.44%` | `+6.77%` | Best minimum return across both periods. |
| `long_only` | `+4.89%` | `+9.21%` | More stable than champion, fewer bad shorts. |
| `long_only_no_long_rvol` | `+3.86%` | `+5.66%` | Stable but not strong. |
| `long_rvol_1_0` | `+3.74%` | `+5.84%` | Relaxed long RVOL helps 2025 but hurts 2026. |
| `no_rvol` | `-5.09%` | `+10.93%` | Too much noise. |
| `no_short_context` | `-14.43%` | `+2.87%` | 4h short context is necessary but not sufficient. |
| `no_short_rvol` | `-14.70%` | `+21.00%` | Overfit-like: works in 2026, collapses in 2025. |

Trade-quality observations:

- `2025H2` longs were not terrible, but exits gave back a lot of favorable
  movement. Example MFE capture: Jul/Sep long captured `37%` of its max favorable
  excursion, Sep long captured `59%`, Oct long captured `73%`.
- `2025H2` shorts were the major drag. Three of four shorts lost money, and the
  losing shorts had poor follow-through: Sep 25 short had only `0.21%` MFE before
  losing `-2.51%`; Dec 16 short had only `1.21%` MFE before losing `-1.27%`.
- `2026YTD` shorts were excellent, especially Feb 26 to Mar 31, which captured
  `7.74%` with low adverse excursion. That one trade is doing a lot of work in
  the champion result.
- Removing or lowering the long RVOL gate improves 2025 but gives back much of
  2026. This means the current long RVOL gate is likely over-specialized for
  volatility-shock entries, not a general bull-market participation rule.
- A simple daily Supertrend filter did not cleanly separate good and bad shorts.
  Good and bad shorts appeared under both daily bullish and daily bearish states.
  So the next short filter needs more than just "daily ST is bearish".

General conclusions:

- The strategy is probably too optimized around high-volatility regime turns.
- The system is too willing to short in broad upward markets after local 30m/4h
  weakness.
- The system is too selective on long entries during steady bull trends.
- Exits are too binary: wait for Supertrend/Vortex confirmation or final bar.
  This helps in 2026's big trend, but gives back too much in 2025.

Most promising next design:

- Split behavior by market regime.
- In bull/neutral regimes, prefer long-only or long-biased behavior:
  disable shorts or require very strong breakdown evidence.
- In bear/high-volatility breakdown regimes, allow shorts with the current
  short filters.
- Relax the long RVOL requirement in bull regimes, but keep it in choppy/bear
  regimes.
- Add profit-protection exits after a trade has meaningful MFE, instead of
  waiting for full ST/Vortex reversal.

Candidate next tests:

- `regime_long_bias`: when daily/4h trend is not clearly bearish, treat bearish
  ST flips as long exits only, not short entries.
- `short_followthrough`: require short entries to break a recent 30m/4h swing low
  after ST/Vortex/ADX confirmation.
- `adaptive_long_rvol`: require `long_vol_ratio >= 2.0` only in bearish/choppy
  regimes; lower or disable it in clear bull regimes.
- `mfe_guard`: after MFE exceeds `2%` or `1 ATR`, exit on close below a faster
  guide such as 30m EMA/Vortex EMA cross instead of waiting for full ST reversal.
