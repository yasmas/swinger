# MACD-Vortex-ADX QQQ 2026 Long-Only Slow Sweep

**Branch**: `macd-vortex-adx`  
**Strategy**: `macd_vortex_adx`  
**Asset / Window**: `QQQ`, `2026-01-01` through the local file cutoff in `data/QQQ-5m-2026.csv` (`2026-04-16 15:15 UTC`)  
**Question**: After disabling shorts, do slower MACD/Vortex/ADX settings reduce churn and improve net performance?

## Progress Log

### Step 1 — Disable shorts

Baseline control:

- keep the original `30min` signal timeframe
- disable shorts only
- measure how much of the previous weakness came from the short side

Result:

- **Yes, shorts were a major part of the problem.**
- Earlier long+short baseline on the same file: **-1.00% gross**, **-3.09% after cost**, **42 trades**
- Long-only baseline: **+0.34% gross**, **-0.66% after cost**, **20 trades**
- Win rate improved to **50.0% gross** and gross profit factor to **1.86**
- Drawdown also tightened to **-0.77%**

### Step 2 — Slow the indicator family

First slow preset to test:

- `MACD 18 / 39 / 9`
- `Vortex 21`
- `Vortex baseline bars 5`
- `Vortex strong spread threshold 1.35`
- `ADX period 20`
- `ADX floor 25`
- `ATR period 20`

Result:

- The first manual slow preset was **directionally better** than the original long+short version, but it was **not** the winner of the focused sweep.
- The best family turned out to be **less slow than expected**:
  - `MACD 15 / 33 / 9` beat both `18 / 39 / 9` and `21 / 45 / 9`

### Step 3 — Dwell into periods and thresholds

Focused sweep around the slow preset:

- MACD families: `15/33`, `18/39`, `21/45`
- Vortex periods: `18`, `21`, `24`
- Vortex strong spread thresholds: `1.25`, `1.35`, `1.50`
- ADX floors: `20`, `25`, `30`

Constants in this sweep:

- `resample_interval: 30min`
- `enable_short: false`
- `macd_signal: 9`
- `vortex_baseline_bars: 5`
- `adx_period: 20`
- `atr_period: 20`
- `atr_stop_multiplier: 2.0`
- `atr_trailing_multiplier: 1.5`

Run count:

- `82` total runs
- `1` control (`baseline_long_only`)
- `81` slow-family variants

## Artifacts

- Sweep script: `scripts/grid_search_macd_vortex_adx_qqq_2026_long_only.py`
- Results CSV: `reports/macd-vortex-adx-qqq-2026-long-only/summary.csv`

## Results

### Best Config In This First Step

**Winner by after-cost return**

- `MACD 15 / 33 / 9`
- `Vortex 21`
- `Vortex baseline bars 5`
- `Vortex strong spread threshold 1.25`
- `ADX period 20`
- `ADX floor 30`
- `ATR period 20`
- `Shorts disabled`

Metrics:

- **+0.43% gross**
- **+0.13% after cost**
- Sharpe **+0.85**
- Max drawdown **-0.60%**
- **6 trades**
- Gross win rate **33.3%**
- Gross profit factor **2.12**

### Baseline vs Best

| Config | Gross Return | After-Cost Return | Trades | WR | PF | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| Long+short baseline | -1.00% | -3.09% | 42 | 42.9% | 0.59 | -1.76% |
| `baseline_long_only` | +0.34% | -0.66% | 20 | 50.0% | 1.86 | -0.77% |
| **best slow long-only** | **+0.43%** | **+0.13%** | **6** | **33.3%** | **2.12** | **-0.60%** |

### What The Sweep Says

1. **Disabling shorts was the biggest improvement.**
   - It cut turnover roughly in half and removed the weakest side of the book.

2. **We were too sensitive, but not in the way I first guessed.**
   - Slower helped, but **very slow** did not.
   - `15/33` worked best.
   - `18/39` was mostly mediocre.
   - `21/45` was too slow and frequently bad.

3. **ADX floor was the cleanest recurring lever.**
   - Mean after-cost return by floor:
     - `ADX 20`: **-0.78%**
     - `ADX 25`: **-0.40%**
     - `ADX 30`: **-0.19%**
   - Higher ADX floors consistently reduced trade count and improved quality.

4. **Vortex 24 was usually too slow for QQQ on this strategy.**
   - Mean after-cost return by Vortex period:
     - `18`: **-0.23%**
     - `21`: **-0.47%**
     - `24`: **-0.67%**
   - There were a few tied top configs using `24`, but on average it was worse.

5. **The winner is profitable after cost, but only barely.**
   - This is a positive first step, not a finished strategy.
   - The top config only traded `6` times in the window, so we should treat it as a promising reduction in overtrading, not as proof of robustness.

### Configs That Clearly Failed

The weakest pocket was the slower MACD families combined with the slowest Vortex and looser ADX floors:

- `18/39` + `Vortex 24` + `ADX 20/25`
- `21/45` across most combinations, especially with `Vortex 21/24`

Worst run:

- `m21_45_v24_s1p5_adx20`
- **-1.06% gross**
- **-1.56% after cost**
- `10` trades

### Decision For This Step

For the next iteration, the current promoted candidate is:

- `enable_short: false`
- `resample_interval: 30min`
- `macd_fast: 15`
- `macd_slow: 33`
- `macd_signal: 9`
- `vortex_period: 21`
- `vortex_baseline_bars: 5`
- `vortex_strong_spread_mult: 1.25`
- `adx_period: 20`
- `adx_floor: 30`
- `atr_period: 20`

This is the config to carry forward into the next single-step improvement.

### Step 4 — Add A MACD Zero-Line Filter

Change implemented:

- Added `require_macd_above_zero_for_long` to `macd_vortex_adx`
- When enabled, long setups may alert below zero, but they cannot confirm or enter until the current MACD value is strictly above zero
- Parameter is exposed in the strategy YAMLs for future sweeps

Comparison on the promoted candidate:

| Config | Gross Return | After-Cost Return | Trades | Sharpe | Max DD |
|---|---:|---:|---:|---:|---:|
| promoted candidate, zero-line **off** | +0.43% | +0.13% | 6 | +0.85 | -0.60% |
| promoted candidate, zero-line **on** | +0.43% | +0.13% | 6 | +0.85 | -0.60% |

Trade logs:

- Control: `reports/macd_vortex_adx_zero_line_compare/best_long_only_control/MACD_Vortex_ADX_best_long_only_control_macd_vortex_adx_step2.csv`
- Zero-line filter: `reports/macd_vortex_adx_zero_line_compare/best_long_only_zero_line/MACD_Vortex_ADX_best_long_only_zero_line_macd_vortex_adx_step2.csv`

What happened:

- **No change at all** on this promoted config / date window.
- All 3 long entries in the control run already had MACD comfortably above zero:
  - `2026-04-01 16:25` → MACD `3.97`
  - `2026-04-14 14:55` → MACD `2.50`
  - `2026-04-15 14:55` → MACD `2.01`

Interpretation:

1. The filter is now available and tested.
2. It is **not the active bottleneck** for the current promoted long-only setup.
3. The prior improvements (`disable shorts`, `faster-than-expected slow family`, `higher ADX floor`) already pushed the strategy into trades that naturally satisfy the zero-line condition.
4. Because it did not bind, this step is **complete but non-promotional**: keep the parameter, but do not treat it as the next edge source for this QQQ 2026 slice.

### Step 5 — Relax The Entry Filters

Question:

- The promoted candidate is profitable, but it often enters well after the initial move.
- Can we relax the confirmation gates enough to enter earlier without breaking win rate?

Focused sweep around the promoted candidate:

- Keep the current long-only `30min` / `15-33-9` core.
- Relax only the gates most likely to delay entries:
  - `adx_floor`: `20`, `24`, `30`
  - `require_adx_rising`: `true`, `false`
  - `require_macd_above_zero_for_long`: `true`, `false`
  - `vortex_strong_spread_mult`: `1.10`, `1.25`
  - `vortex_hugging_spread_mult`: `0.90`, `1.05`
  - `macd_fresh_bars`: `2`, `4`

Run count:

- `96` total runs
- `1` promoted control
- `95` relaxed-entry variants

Artifacts:

- Sweep script: `scripts/grid_search_macd_vortex_adx_qqq_2026_relaxed_entry.py`
- Results CSV: `reports/macd-vortex-adx-qqq-2026-relaxed-entry/summary.csv`

#### Best Relaxed Config

**Winner by after-cost return**

- `macd_fast: 15`
- `macd_slow: 33`
- `macd_signal: 9`
- `vortex_period: 21`
- `vortex_baseline_bars: 5`
- `vortex_strong_spread_mult: 1.25`
- `vortex_hugging_spread_mult: 1.05`
- `adx_period: 20`
- `adx_floor: 20`
- `require_adx_rising: false`
- `require_macd_above_zero_for_long: false`
- `macd_fresh_bars: 2`
- `enable_short: false`

Metrics:

- **+2.08% gross**
- **+1.78% after cost**
- **54.5% gross win rate**
- Gross profit factor **4.80**
- `24` trades
- Max drawdown **-1.18%**

Equivalent tied winner:

- `adx20_rise0_zero0_strong1p25_hug0p9_fresh2`
- `adx20_rise0_zero0_strong1p25_hug1p05_fresh2`

#### Promoted Control vs Best Relaxed

| Config | Gross Return | After-Cost Return | Trades | WR | PF | Max DD | Earlier Entry? |
|---|---:|---:|---:|---:|---:|---:|:---:|
| promoted control | +0.43% | +0.13% | 6 | 33.3% | 2.12 | -0.60% | No |
| **best relaxed** | **+2.08%** | **+1.78%** | **24** | **54.5%** | **4.80** | **-1.18%** | **Yes** |

#### What This Sweep Says

1. **Yes, we can enter earlier and improve WR at the same time.**
   - The current promoted config was not just “safer”; it was **too restrictive**.
   - Relaxing the right gates improved both timing and trade quality on this QQQ 2026 slice.

2. **The big lever was ADX confirmation, not Vortex tuning.**
   - The winning pocket consistently had:
     - `adx_floor = 20`
     - `require_adx_rising = false`
   - The Vortex threshold tweaks had much smaller effect than the ADX gate.

3. **The MACD zero-line filter became harmful once timing was the priority.**
   - With the relaxed ADX gate, keeping the zero-line filter usually suppressed otherwise good earlier longs.
   - The top pocket always had `require_macd_above_zero_for_long = false`.

4. **Extending alert freshness to `4` bars usually made things worse.**
   - `macd_fresh_bars = 2` consistently beat `4`.
   - Longer freshness mostly added stale follow-through entries rather than truly better early entries.

5. **This does not solve the March 31 / April 1 case by itself.**
   - I checked the winning relaxed config directly.
   - It still **did not** buy the `2026-03-31 08:25` setup or any time before the existing `2026-04-01 16:25` entry.
   - Reason: on that whole March 31 alert sequence, Vortex still read:
     - direction = `short`
     - classification = `hugging`
   - So relaxing ADX and zero-line filters improved the strategy overall, but **that specific missed wave is still blocked by the Vortex gate**, not by ADX or MACD-zero.

#### Decision For This Step

Two conclusions are both true:

- The current promoted control is **too late** and should not remain our default “best” config.
- But the March 31 miss shows the next bottleneck is now the **Vortex interpretation**, not the ADX or zero-line filter.

So the best current candidate for “earlier entry v1.5” is:

- `enable_short: false`
- `resample_interval: 30min`
- `macd_fast: 15`
- `macd_slow: 33`
- `macd_signal: 9`
- `macd_fresh_bars: 2`
- `vortex_period: 21`
- `vortex_baseline_bars: 5`
- `vortex_strong_spread_mult: 1.25`
- `vortex_hugging_spread_mult: 1.05`
- `adx_period: 20`
- `adx_floor: 20`
- `require_adx_rising: false`
- `require_macd_above_zero_for_long: false`
- `atr_period: 20`

This is the config to carry into the next step if the goal is “earlier while still profitable.”

## Optional Next Steps

- [x] Disable shorts and rerun baseline.
- [x] Sweep slower 30m periods and thresholds around the first promising preset.
- [x] Add a MACD zero-line filter so long entries only fire when MACD is already above zero.
- [x] Relax the entry filters and check whether earlier entry improves WR.
- [x] Change trailing-stop ratchet to use bar close instead of intrabar high/low.
- [x] Restrict equity trailing stops to regular trading hours and switch report display to local time.
- [ ] Add a higher-timeframe bias filter, e.g. only take longs when price is above a long EMA.
- [ ] Replace or redesign the current Vortex gate. The relaxed-entry sweep shows this is now the main blocker for missed early moves like `2026-03-31`.
- [ ] Test StockCharts-style Vortex thresholds around `0.90 / 1.10` instead of the current spread-vs-baseline rule.
- [ ] Add breakout volume confirmation or OBV confirmation.
- [ ] Tighten the “strong” classification so fewer setups bypass the armed-breakout path.
- [ ] Revisit the trailing stop so tiny winners are not harvested too early.
- [ ] Test `macd_fresh_bars=1` to reduce stale entries.
- [ ] Test `1h` signal bars only after the best `30min` long-only family is established.
- [ ] Compare the best long-only preset on `QLD 2026`.
- [ ] Compare the best long-only preset on `QQQ 2025` to check for regime dependence.

## Step: Close-Based Trailing Ratchet

### Why This Step

While reviewing the `2026-04-13` long, the exit looked too eager because a single extreme upper wick inside the `19:55` five-minute bar pushed the trailing stop unrealistically high. The strategy was ratcheting the long-side peak using bar `high`, then immediately checking that same bar's `low` against the tighter trail. With OHLC data, that can overstate how quickly a stop would really tighten.

The requested change was to anchor the trailing ratchet to bar `close` instead of bar `high` for longs, and symmetrically to bar `close` instead of bar `low` for shorts.

### Code Change

- Long trailing peak now updates from the current bar `close`.
- Short trailing trough now updates from the current bar `close`.
- Stop-hit checks remain unchanged:
  - long exits still trigger when `low <= trailing_stop`
  - short exits still trigger when `high >= trailing_stop`

This keeps the stop execution conservative while removing the same-bar wick inflation in the trail anchor.

### Regression Coverage

Added a focused regression test for the long side:

- prior peak = `110`
- fake spike high = `120`
- bar close = `111`
- bar low = `109.8`

Old behavior would have exited because the wick-based trail jumped too high. New behavior correctly keeps the trade open because the close-based trail only ratchets to `111`.

### Backtest Result

Re-ran the best relaxed-entry config:

- Config: `config/strategies/macd_vortex_adx/qqq_2026_best_relaxed_entry.yaml`
- Period: `2026-01-01` to `2026-04-16`

| Run | Gross Return | Final Value |
|---|---:|---:|
| prior wick-based trail | +3.00% | $102,998.99 |
| **new close-based trail** | **+2.68%** | **$102,676.30** |

### Trade-Level Impact

The `2026-04-13 13:55` buy is no longer forced out by the `19:55` fake upper wick.

- Before: exited on `2026-04-13 19:55` at `617.32`
- After: remained open through the evening trend and exited later on `2026-04-14 12:00` at `620.79`

So the stop logic is now more faithful to the intention, even though the overall backtest result came down slightly elsewhere in the sample.

### Interpretation

This was a modeling-quality improvement, not a pure optimization step.

- It fixed a stop behavior that looked artificially sensitive to wick noise.
- It improved the specific April 13 trade we inspected.
- But it did **not** improve the whole backtest; total return dipped from `+3.00%` to `+2.68%`.

That tradeoff is acceptable in my view because the new trailing logic is more realistic and easier to trust when visually inspecting charts.

## Step: Local-Time Report + RTH-Only Equity Trailing Stops

### Why This Step

After converting the suspicious `2026-04-14 12:00:00` backtest exit into local time, it turned out to be a premarket `05:00 PDT / 08:00 EDT` bar. That made the behavior much easier to interpret: the strategy was still honoring trailing stops in thin extended-hours conditions even though the move later normalized.

Two targeted changes were made:

- The HTML report now displays times in local time (`America/Los_Angeles`) so visual inspection matches the user’s trading context.
- For equity configs, trailing stops can now be restricted to regular hours only, while the initial hard stop remains active at all times.

### Config Change

The promoted relaxed-entry config now includes:

- `asset_class: equity`
- `trailing_stop_rth_only_for_equities: true`
- `equity_session_timezone: America/New_York`
- `equity_regular_session_start: 09:30`
- `equity_regular_session_end: 16:00`
- `report_timezone: America/Los_Angeles`

### Result

Re-ran:

- `config/strategies/macd_vortex_adx/qqq_2026_best_relaxed_entry.yaml`

Backtest outcome:

- Final value: `$102,006.90`
- Gross return: `+2.01%`

Compared with the prior close-based-trail run:

| Run | Gross Return | Final Value |
|---|---:|---:|
| close-based trail, all sessions | +2.68% | $102,676.30 |
| **close-based trail, RTH-only for equities** | **+2.01%** | **$102,006.90** |

### Trade-Level Impact

The `2026-04-13 13:55` long now stays alive much longer:

- prior close-based-trail run: exited `2026-04-14 12:00`
- RTH-only trailing run: exited `2026-04-15 16:40`

That is directionally consistent with the goal: avoid premarket stop-outs caused by thinner liquidity.

### Interpretation

This change improved interpretability and session realism, but it did not improve headline performance on this sample.

- The report is now much easier to read because chart times line up with the user’s local clock.
- Equity trailing stops no longer fire in premarket noise.
- Even so, the overall return fell from `+2.68%` to `+2.01%`, so this should be treated as a modeling choice, not a proven optimization.
