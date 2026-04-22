# LazySwing ETH Volatility-Regime Experiments

**Branch**: `codex/rvol-ratio-stage1`

**Base strategy**: LazySwing ETH 30m HOF baseline  
`config/strategies/lazy_swing/eth_30m_hof.yaml`

**Base ST parameters**:
- resample: `30min`
- Supertrend: `atr=25`, `mult=1.75`
- symbol: `ETH-PERP-INTX`

**Question**: Can we use volatility information to improve the quality of ST flips, especially the weak/whipsaw flips that hurt ETH 2024 H2?

## Current Best Candidate

If we had to carry one volatility-regime version forward right now, it would be:

1. Keep ST fixed at HOF:
   - `atr=25`
   - `mult=1.75`
2. Use the slow volatility-regime function only to move the flip gate and held-flip stop:
   - mode: `squared`
   - low `r`: `0.70`
   - high `r`: `1.00`
   - low stop: `1.0%`
   - high stop: `2.5%`
   - power: `1.5`

Why this is the current best candidate:

- it was the cleanest compromise across **2024 H1**, **2024 H2**, **2025**, and **2026 YTD**
- it materially improved the weak **2024 H2** regime without collapsing **2024 H1**
- it was clearly the best choice inside the final squared fixed-ST family
- **2025** strongly preferred it versus the nearby squared alternatives

Status:

- **current preferred fixed-ST volatility-regime candidate**
- still not the single best setup in every regime, but the most attractive overall compromise so far

## Motivation

The original observation was:

- ETH 2024 backtest had poor performance because many ST flips reversed quickly
- current logic always exits and immediately reverses
- in choppy regimes this compounds losses

The initial goal was to decide whether a flip is strong enough before taking it. We explored three broad families:

1. **Filter the flip** after ST fires
2. **Adapt the stop / hold logic** after a rejected flip
3. **Adapt the ST itself** so bad flips happen less often

## Repro / Entry Points

Primary config / data references:

- Base YAML: `config/strategies/lazy_swing/eth_30m_hof.yaml`
- 2024 backtest data: `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv`
- 2025 backtest data: `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv`
- 2026 forward data: `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv`

Main scripts created / used in this experiment family:

- Absolute RVOL flip filter:
  - `scripts/grid_search_lazyswing_rvol_flip_filter.py`
- Ratio-hold:
  - `scripts/grid_search_lazyswing_ratio_hold_stage1.py`
  - `scripts/grid_search_lazyswing_ratio_hold_2025_2026.py`
- Adaptive ST step-function:
  - `scripts/grid_search_lazyswing_adaptive_st_step_2024.py`
- Adaptive ST with hysteresis + dwell:
  - `scripts/grid_search_lazyswing_adaptive_st_hysteresis_2024.py`
- Adaptive ST + ratio-hold:
  - `scripts/grid_search_lazyswing_adaptive_plus_ratio_2024.py`

Core strategy files touched:

- `src/strategies/lazy_swing.py`
- `src/strategies/intraday_indicators.py`
- tests:
  - `src/tests/test_lazy_swing_entry_persist.py`
  - `src/tests/test_intraday_indicators.py`

## Diagnostic Baseline

Before designing more filters, we checked whether H2 2024 was really a win-rate collapse.

Trade-level baseline stats:

| Period | WR | Avg win | Avg loss | Payoff ratio |
|---|---:|---:|---:|---:|
| H1 2024 | 34.78% | +2.07% | -1.02% | 2.04 |
| H2 2024 | 34.83% | +1.98% | -1.15% | 1.73 |

Main finding:

- **H2 was not mainly a win-rate problem**
- H1 and H2 had almost identical WR
- the real damage in H2 was **worse payoff quality**: smaller winners and larger losers

This became important later because it meant “just reject more flips” was not obviously the whole answer.

---

## Step 1 — Absolute RVOL Gate on ST Flips

### Design

First version used what we called `RVOL`, but in practice this was **realised volatility**, not relative volume:

```text
RVOL_t = annualized rolling std(log returns)
```

At each ST flip:

- if RVOL above threshold, allow the reverse
- if below threshold, one of:
  - `hold`: keep current position
  - `flat`: exit but do not reverse
  - `watch`: wait N bars and re-check

### Findings

**H1 2024 stage-1**

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `hold_t40` | +49.48% | 37.8% | -31.4% |
| `watch_t40_n2` | +45.98% | 35.6% | -30.6% |
| `flat_t40` | +44.40% | 35.3% | -30.7% |
| baseline | +39.39% | 34.9% | -30.4% |

**H2 2024**

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `hold_t60` | +66.63% | 39.0% | -29.9% |
| `hold_t70` | +49.93% | 40.4% | -34.9% |
| baseline | -20.34% | 34.9% | -49.6% |

Main findings:

- `hold` clearly beat `flat` and `watch`
- H1 liked a **low threshold** (`40%`)
- H2 liked a **higher threshold** (`60-70%`)
- no single absolute threshold generalized across both halves

Conclusion:

- absolute RVOL gating helped, but was clearly **regime-sensitive**

Artifacts:
- `reports/rvol-flip-filter-eth-h1-2024-stage1/`
- `reports/rvol-flip-filter-eth-h2-2024/`

Repro:

- Base YAML / strategy: `config/strategies/lazy_swing/eth_30m_hof.yaml`
- Runner script: `scripts/grid_search_lazyswing_rvol_flip_filter.py`
- Core implementation: `src/strategies/lazy_swing.py`

---

## Step 2 — Ratio-Hold Filter

### Design

To remove the fixed-threshold regime dependence, we switched to:

```text
ratio_t = short_realized_vol_t / mean(short_realized_vol over prior week)
```

Implementation details:

- short vol window: initially `n=4` or `8` bars on 30m data
- long baseline: `336` bars = 1 week
- baseline mean was **shifted**, so current value was not included

Behavior:

- if `ratio_t` below threshold, reject the flip and **keep holding**
- add a safety stop so we still exit if price keeps moving further against us

### 2024 Stage-1 Findings

**H1 2024**

Top results:

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `n4_r0.5_s3.0` | +89.42% | 38.4% | -26.1% |
| `n4_r0.75_s1.0` | +89.19% | 38.3% | -23.4% |
| `n4_r0.75_s0.5` | +89.02% | 38.1% | -22.7% |
| baseline | +39.39% | 34.9% | -30.4% |

**H2 2024**

Top results:

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `n4_r1.0_s3.0` | +52.03% | 40.5% | -34.0% |
| `n4_r1.0_s1.5` | +47.59% | 38.7% | -30.9% |
| `n4_r1.0_s2.0` | +42.50% | 38.8% | -30.5% |
| baseline | -20.34% | 34.9% | -49.6% |

Main findings:

- H1 liked **looser** ratios (`0.5-0.75`)
- H2 liked **tighter** ratios (`1.0+`)
- ratio-hold was the first method that strongly improved both halves, even though the best thresholds differed

Artifacts:
- `reports/ratio-hold-stage1-eth-2024/`

Repro:

- Base YAML / strategy: `config/strategies/lazy_swing/eth_30m_hof.yaml`
- Stage-1 runner: `scripts/grid_search_lazyswing_ratio_hold_stage1.py`
- 2025 / 2026 validation runner: `scripts/grid_search_lazyswing_ratio_hold_2025_2026.py`
- Core implementation: `src/strategies/lazy_swing.py`

---

## Step 3 — Dynamic Safety Stop as a Function of Volatility

### Design

We then tried scaling the safety stop between `1%` and `2%` depending on volatility, instead of hard-coding a fixed stop.

### Findings

This did **not** generalize well.

Example:

| Period | Best dynamic config | Return | WR | DD |
|---|---|---:|---:|---:|
| H1 | `n4, r=0.8, dyn 1%-2%` | +69.83% | 39.2% | -24.3% |
| H2 | `n8, r=0.9, dyn 1%-2%` | +2.24% | 38.9% | -34.8% |

Compared with the best fixed-stop ratio-hold configs, dynamic stop scaling was weaker.

Conclusion:

- “make `X` a function of vol” sounded sensible
- but in practice it did **not** solve the cross-regime problem by itself

Repro:

- Strategy implementation: `src/strategies/lazy_swing.py`
- Results came from the ratio-hold branch of the same base setup above

---

## Step 4 — Check Whether Bad H2 Flips Were Just Low Absolute Vol

### Question

If bad H2 flips were mainly low-absolute-vol events, perhaps ratio was unreliable and we needed an absolute vol floor.

### Findings

They were **not** mainly low-absolute-vol flips.

Trade-entry annualized vol:

| Measure | Winners mean | Losers mean | Winners median | Losers median |
|---|---:|---:|---:|---:|
| `vol4_ann` | 68.7% | 66.0% | 51.7% | 49.3% |
| `vol8_ann` | 69.1% | 64.5% | 58.7% | 54.3% |

The worst-loss quartile actually had **high vol**, not low vol.

Conclusion:

- bad H2 flips were **not** simply “low-vol fakeouts”
- an absolute floor might still help as a safety check
- but it was **not** the core explanation

---

## Step 5 — ER / Flip-Density Diagnostics

### Design

We tested:

- `ER_t` = efficiency ratio
- `flip_count_t` = recent ST flip density

Goal:

- see if H2 was clearly a “chop” regime in a way that could drive thresholds

### Findings

Results were weak / mixed:

- `flip_count_t` had very little explanatory power
- `ER_t` had some H2 usefulness, but did **not** produce robust thresholds that worked in both H1 and H2

Conclusion:

- ER / flip-count were useful diagnostics
- but not a clean production rule for this strategy

Artifact:
- `reports/ratio-hold-er-flip-analysis-2024.csv`

Repro:

- Analysis used baseline trade logs from:
  - `reports/ratio-hold-stage1-eth-2024/h1_2024/baseline/`
  - `reports/ratio-hold-stage1-eth-2024/h2_2024/baseline/`
- Base data file: `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv`

---

## Step 6 — Adaptive ST (Initial Step-Function Version)

### Design

Instead of filtering flips after ST fired, we asked:

- can we make ST itself less reactive in high-vol regimes?

Initial adaptive ST:

- base ST stayed `25 / 1.75`
- high-vol ST switched to another pair such as `30/2.0`, `35/2.0`, `40/2.0`
- switch was driven by short vol ratio

This version used:

- short vol window `4`
- single threshold only
- no hysteresis
- no minimum dwell

### Findings

This version was **too twitchy**.

For H2, depending on threshold, ST-regime switching happened roughly:

- `0.9` threshold: ~1420 switches
- `1.0`: ~1302
- `1.1`: ~1188
- `1.25`: ~990

It helped H1 a lot, but H2 remained negative.

Best examples:

| Config | H1 Return | H2 Return |
|---|---:|---:|
| `40/2.0 @ 1.0` | +109.51% | -11.92% |
| `40/2.0 @ 0.9` | +104.44% | -7.86% |
| baseline | +39.39% | -20.34% |

Important parameter insight:

- longer ATR paired better with **bigger multiplier**
- `2.0` consistently beat `1.5`
- so “higher vol -> longer + wider” was the correct direction

Artifacts:
- `reports/adaptive-st-step-eth-2024/`

Repro:

- Runner script: `scripts/grid_search_lazyswing_adaptive_st_step_2024.py`
- Core implementation:
  - `src/strategies/lazy_swing.py`
  - `src/strategies/intraday_indicators.py`
- Base YAML / strategy anchor: `config/strategies/lazy_swing/eth_30m_hof.yaml`

---

## Step 7 — Adaptive ST with Hysteresis, Longer Vol Window, and Minimum Dwell

### Design

We then fixed the twitchiness:

- longer vol windows: `24` and `48`
- hysteresis:
  - enter high-vol mode above upper threshold
  - exit only below lower threshold
- minimum dwell:
  - stay in high-vol mode at least **24 hours**

Focused high-vol ST pairs:

- `35 / 2.0`
- `40 / 2.0`

Threshold bands tested:

- `>0.8 / <0.7`
- `>0.9 / <0.8`
- `>1.0 / <0.85`

### Findings

This version was much better.

**Best H2 adaptive-ST-only**

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `vp24, >1.0/<0.85, 40/2.0` | -0.97% | 35.5% | -44.3% |
| `vp24, >0.8/<0.7, 40/2.0` | -5.04% | 35.8% | -44.3% |
| baseline | -20.34% | 34.9% | -49.6% |

**Same configs on H1**

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `vp24, >1.0/<0.85, 40/2.0` | +56.10% | 36.7% | -22.9% |
| `vp24, >0.8/<0.7, 40/2.0` | +54.05% | 38.3% | -22.1% |
| baseline | +39.39% | 34.9% | -30.4% |

Main findings:

- adaptive ST now **helped H2 without killing H1**
- `vol_period=24` beat `48` for H2
- `40/2.0` beat `35/2.0`
- best adaptive-ST-only compromise:
  - `vol_period=24`
  - `enter >1.0`
  - `exit <0.85`
  - `min dwell = 24h`
  - high-vol ST = `40 / 2.0`

For this best H2 adaptive-ST configuration:

- total regime switches: **149**
- entries to high-vol mode: **75**
- exits back to base mode: **74**
- high-vol mode active: **57.95%** of H2
- average high-vol spell: **68.24 bars** (~34.1 hours)

Artifacts:
- `reports/adaptive-st-hysteresis-eth-2024/`

Repro:

- Runner script: `scripts/grid_search_lazyswing_adaptive_st_hysteresis_2024.py`
- Core implementation:
  - `src/strategies/lazy_swing.py`
  - `src/strategies/intraday_indicators.py`
- Base YAML / strategy anchor: `config/strategies/lazy_swing/eth_30m_hof.yaml`

---

## Step 8 — Combine Adaptive ST + Ratio-Hold

### Design

Final combined model:

- use the best adaptive ST found above
  - `vp24`
  - `>1.0 / <0.85`
  - min dwell `24h`
  - high-vol ST `40 / 2.0`
- add ratio-hold flip filtering on top

Ratio grid:

- `r = 0.75, 0.8, 0.9, 1.0`
- stop = `0.5%, 1.0%, 2.0%, 3.0%`

Important note:

- in this combined experiment the safety stop was still **hard-coded per run**
- it was **not** a function of volatility

### Findings

This was the strongest result set of the whole project so far.

**H1 2024**

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `adaptive + r=0.75 + stop=1.0%` | +96.32% | 40.4% | -20.3% |
| `adaptive + r=0.8 + stop=1.0%` | +93.22% | 40.6% | -20.3% |
| `adaptive only` | +56.10% | 36.7% | -22.9% |
| baseline | +39.39% | 34.9% | -30.4% |

**H2 2024**

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `adaptive + r=1.0 + stop=3.0%` | +119.48% | 41.4% | -22.2% |
| `adaptive + r=1.0 + stop=2.0%` | +95.92% | 37.4% | -20.5% |
| `adaptive + r=0.9 + stop=3.0%` | +75.45% | 40.0% | -28.0% |
| `adaptive only` | -0.97% | 35.5% | -44.3% |
| baseline | -20.34% | 34.9% | -49.6% |

### Stop-width observation

This combined model changed the stop story materially:

- **H1** still preferred tighter / medium stops (`0.5%-1.0%`)
- **H2** preferred much **wider** stops (`2.0%-3.0%`)

Examples:

**H2**

| Ratio | 0.5% | 1.0% | 2.0% | 3.0% |
|---|---:|---:|---:|---:|
| `r=0.75` | +20.32% | +31.00% | +49.08% | +49.68% |
| `r=0.80` | +23.80% | +37.09% | +55.45% | +57.08% |
| `r=0.90` | +41.42% | +49.05% | +69.12% | +75.45% |
| `r=1.00` | +63.17% | +72.93% | +95.92% | +119.48% |

This is one of the strongest signs that **stop width should probably depend on regime**, not be fixed.

Artifacts:
- `reports/adaptive-plus-ratio-eth-2024/`

Repro:

- Runner script: `scripts/grid_search_lazyswing_adaptive_plus_ratio_2024.py`
- Core implementation:
  - `src/strategies/lazy_swing.py`
  - `src/strategies/intraday_indicators.py`
- Base YAML / strategy anchor: `config/strategies/lazy_swing/eth_30m_hof.yaml`

---

## Out-of-Sample Validation Beyond 2024

To check whether the ratio-hold idea was just a 2024 artifact, we also ran compact validation on:

- full **2025**
- **2026 YTD**

These used the 30m HOF baseline and the simpler ratio-hold version.

### 2025

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `n4_r0.75_s1.0` | +660.21% | 40.4% | -33.2% |
| `n4_r0.75_s0.5` | +592.29% | 40.1% | -32.3% |
| baseline | +390.53% | 38.7% | -28.3% |

### 2026 YTD

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `n4_r0.9_s0.5` | +44.29% | 38.5% | -23.5% |
| `n4_r0.75_s0.5` | +39.65% | 39.2% | -23.6% |
| baseline | +38.16% | 38.6% | -23.7% |

Main finding:

- the ratio-hold idea clearly **held up outside 2024**
- 2025 liked the looser `0.75` zone
- 2026 YTD liked `0.9`, but `0.75` was still competitive

Artifacts:
- `reports/ratio-hold-validation-eth/`

Repro:

- Validation runner: `scripts/grid_search_lazyswing_ratio_hold_2025_2026.py`
- Base YAML / strategy anchor: `config/strategies/lazy_swing/eth_30m_hof.yaml`

---

## Step 9 — Dynamic `r` and Stop on Top of Adaptive ST

### Design

We implemented a regime-driven version of the flip-vol gate:

- keep the adaptive ST regime classifier
- let that same regime state drive:
  - `r`
  - held-flip safety stop width

Three interpolation styles were tested:

- `step`
- `linear`
- `occupancy`

### Findings

On **2024 only**, this looked strong.

Best balanced 2024 result:

- `linear`, low `r=0.78`, high `r=0.95`, low stop `1.0%`, high stop `2.5%`

2024 results:

- H1: **+93.65%**, WR **40.1%**, DD **-18.8%**
- H2: **+95.04%**, WR **37.6%**, DD **-24.9%**

But it **did not generalize**.

2025 / 2026 validation showed:

- 2025 dynamic returns fell to roughly **+140% to +165%**
- baseline was **+390.53%**
- old fixed winner `n4_r0.75_s1.0` was **+660.21%**
- 2026 YTD also degraded badly

Main finding:

- the problem was mostly **adaptive ST itself**
- letting ST change by regime was too expensive in good trend regimes like 2025
- dynamic `r/stop` on top of adaptive ST was **not** the right production direction

Artifacts:

- `reports/adaptive-regime-ratio-stop-eth-2024/`
- `reports/adaptive-regime-ratio-stop-eth/validation_2025_2026/`
- `reports/adaptive-regime-ratio-stop-eth/validation_2025_2026_extra/`

Repro:

- Runner script: `scripts/grid_search_lazyswing_adaptive_regime_ratio_stop_2024.py`

---

## Step 10 — Keep ST Fixed, Make Only `r` and Stop Regime-Dependent

### Design

This was the cleaner follow-up:

- keep HOF ST fixed at `25 / 1.75`
- still compute the same slow volatility-regime state
- let only these change by regime:
  - flip-vol ratio threshold `r`
  - held-flip safety stop

### Findings

This was materially better than Step 9.

Best broad fixed-ST regime result on 2024:

- `linear`, `r: 0.75 -> 1.0`, stop: `1.0% -> 2.5%`

2024 results:

- H1: **+84.22%**
- H2: **+56.46%**

This did **not** beat the best specialized H1/H2 settings, but it unified them much better than:

- fixed loose rule (`r=0.75`, stop `1.0%`)
- fixed tight rule (`r=1.0`, stop `3.0%`)

### Narrow 2025-first follow-up

We then narrowed the regime search and optimized on **2025 first**:

- low `r`: `0.70, 0.75`
- high `r`: `0.80, 0.90, 1.00`
- low stop: `1.0%`
- high stop: `1.5%, 2.0%, 2.5%`

Top 2025 dynamic results:

| Config | Return | WR | DD |
|---|---:|---:|---:|
| `step 0.75 -> 0.80, 1.0% -> 2.5%` | +691.87% | 40.3% | -34.2% |
| `linear 0.75 -> 0.80, 1.0% -> 2.5%` | +667.18% | 39.7% | -32.3% |
| `linear 0.70 -> 0.80, 1.0% -> 2.5%` | +626.60% | 39.3% | -31.6% |

These were then replayed on **2024 H1**, **2024 H2**, and **2026 YTD**.

Main finding:

- these loose `high r = 0.8` winners were excellent for **2025**
- excellent for **2024 H1**
- but they still **did not fix 2024 H2**
- and they underperformed baseline on **2026 YTD**

Concrete replay results:

| Config | 2024 H1 | 2024 H2 | 2026 YTD |
|---|---:|---:|---:|
| `step 0.75 -> 0.80, 1.0% -> 2.5%` | +72.47% | -16.63% | +23.28% |
| `linear 0.75 -> 0.80, 1.0% -> 2.5%` | +101.32% | -11.44% | +19.76% |
| `linear 0.70 -> 0.80, 1.0% -> 2.5%` | +112.39% | -13.56% | +20.13% |
| baseline | +39.39% | -20.34% | +38.16% |

So the final conclusion from this step was:

- **keeping ST fixed is the right structural choice**
- but one loose regime mapping is still not enough
- 2024 H2 still needs a meaningfully tighter high-vol rule than `0.8`

### Squared-mode follow-up

We then tested a curved regime mapping instead of plain `linear` / `step`:

- keep ST fixed
- let `r` and stop ramp nonlinearly with the same slow volatility state
- idea: calm regimes stay looser, but stressed regimes move faster toward tighter H2-style settings

Targeted family:

- `squared 0.7 -> 1.0`
- high stop in the `2.0% - 2.5%` range
- power around `1.4 - 2.5`

Best overall squared candidate:

- `squared 0.7 -> 1.0, 1.0% -> 2.5%, p=1.5`

Why this one stood out:

- on **2024 H1 / H2 / 2026**, it stayed within roughly `1%-2%` return of the nearby best squared alternatives
- on **2025**, it was clearly best inside the final squared family, by about `20%` versus the next-best nearby candidate
- so this became the preferred fixed-ST compromise rule

Key comparisons:

| Period | Preferred squared | Nearby best reference |
|---|---:|---:|
| 2024 H1 | +94.26% | +96.11% |
| 2024 H2 | +53.68% | +59.18% |
| 2025 | +323.69% | +295.10% |
| 2026 YTD | +33.57% | +35.56% |

Interpretation:

- this is **not** the absolute winner in every period
- but it is the first fixed-ST regime rule that feels consistently competitive without overfitting one specific year
- among the squared candidates, this is the one we would carry forward first

Artifacts:

- `reports/fixed-st-regime-ratio-stop-eth/h1_2024_focused/`
- `reports/fixed-st-regime-ratio-stop-eth/h2_2024_focused/`
- `reports/fixed-st-regime-ratio-stop-eth/combined_h1_h2_focused.csv`
- `reports/fixed-st-regime-ratio-stop-eth/validation_2025_2026/`
- `reports/fixed-st-regime-ratio-stop-eth/2025_narrow/`
- `reports/fixed-st-regime-ratio-stop-eth/top3_validation/`

Repro:

- Runner scripts:
  - `scripts/grid_search_lazyswing_fixed_st_regime_ratio_stop_2024.py`
  - `scripts/grid_search_lazyswing_ratio_hold_2025_2026.py`

---

## Overall Conclusions

1. **Absolute RVOL thresholding helped, but was not stable across regimes.**
2. **Ratio-hold was the first strong general improvement.**
3. **Dynamic stop scaling by vol did not solve the problem on its own.**
4. **Absolute-vol floors and ER/flip-count diagnostics did not explain H2 well enough to become production rules.**
5. **Adaptive ST needed hysteresis, longer vol windows, and minimum dwell to become usable.**
6. **Adaptive ST alone improved H2 a lot, but not enough.**
7. **Adaptive ST + ratio-hold was the strongest 2024-only result set, but it did not generalize cleanly.**
8. **Keeping ST fixed and making only `r`/stop regime-dependent was the better structural direction.**
9. **Inside the fixed-ST family, `squared 0.7 -> 1.0, 1.0% -> 2.5%, p=1.5` is the current preferred compromise.**
10. The remaining instability is now mostly about:
   - the right `r` by regime
   - the right safety stop width by regime

---

## What We Should Try Next

### 1. Make stop width depend on the same regime state

This is the clearest next step from the combined results.

Proposed:

- base regime stop: `0.5%-1.0%`
- high-vol regime stop: `2.0%-3.0%`

Rationale:

- H1 strongly preferred tight / medium stops
- H2 strongly preferred wider stops
- the adaptive ST already defines a regime state we can reuse

### 2. Make `r` depend on the same regime function

This is the user idea raised after the combined test, and it fits the data very well.

Instead of one fixed `r`, let the adaptive volatility-regime state choose it:

- base regime -> looser `r`
- high-vol regime -> tighter `r`

Example:

- base regime: `r = 0.75`
- high-vol regime: `r = 1.0`

This is attractive because:

- we already have a slower, more stable volatility-regime classifier
- it can drive **both** the ST pair and the flip gate
- it is much cleaner than letting the fast 4-bar ratio directly control everything

### 3. Move the ratio filter itself to a slower / regime-aware form

Right now:

- adaptive ST uses a slower regime function
- ratio-hold still uses the fast immediate flip ratio

A clean next experiment is:

- keep the fast ratio for “flip strength now”
- but normalize or offset its threshold using the slower regime state

This would separate:

- **regime state** = slow, stable
- **flip strength** = fast, immediate

### 4. Re-test `watch` mode on top of the calmer adaptive ST

`watch` mode was weak in the earliest experiments, but that was before we had:

- adaptive ST hysteresis
- reduced switch thrash

With the calmer base signal, `watch` may be worth revisiting in a small experiment.

### 5. Full-year / forward validation for the combined model

Before promoting anything:

- run the best combined candidates on full **2024**
- then run on **2025** and **2026 YTD**

The current best 2024 winners are:

- H1-style: `adaptive + r=0.75 + stop=1.0%`
- H2-style: `adaptive + r=1.0 + stop=3.0%`

So the real challenge now is to unify those under one regime-dependent rule.

---

## Failed / Weak Ideas To Avoid Repeating Blindly

These were explored and were not strong enough to become standalone rules.

### 1. Recent flip count (`flip_count_t`)

Idea:

- measure how many recent ST flips occurred
- use high flip density as a “chop” warning

What we found:

- it had **weak explanatory power**
- it did not create a robust threshold that improved both H1 and H2
- useful as a descriptive feature, but not a reliable trading rule

Status:

- **Do not repeat as a standalone filter** unless paired with a genuinely new model

### 2. Efficiency ratio (`ER_t`)

Idea:

- use efficiency ratio to distinguish directional vs choppy movement
- require more confirmation when ER is low

What we found:

- `ER_t` had some local signal in H2
- but the thresholds that helped H2 tended to hurt H1
- no stable threshold generalized across both halves

Status:

- **Do not repeat as a standalone gate** unless the setup changes materially

### 3. Absolute-vol-only explanation

Idea:

- maybe bad H2 flips were mostly low-absolute-vol flips
- so an absolute vol floor would solve the issue

What we found:

- bad H2 flips were **not** predominantly low-vol
- some of the worst losses happened in high-vol conditions too

Status:

- absolute vol floor may still be a secondary guardrail
- **not** the main answer by itself

### 4. Fast adaptive ST without hysteresis / dwell

Idea:

- switch ST ATR / multiplier directly off a fast volatility trigger

What we found:

- the regime switched far too often
- behavior was too twitchy to trust

Status:

- **Do not repeat** without:
  - longer vol windows
  - hysteresis
  - minimum dwell time

This section is here mainly so future work starts from the stronger versions and not from the already-rejected simpler versions.

---

## Suggested Next Experiment

If we want one concrete next experiment, I would do this:

1. Keep adaptive ST fixed at:
   - `vol_period=24`
   - enter `>1.0`
   - exit `<0.85`
   - min dwell `24h`
   - high-vol ST `40 / 2.0`
2. Make both `r` and stop width depend on that regime:
   - base regime: `r=0.75`, stop `1.0%`
   - high-vol regime: `r=1.0`, stop `3.0%`
3. Test on:
   - H1 2024
   - H2 2024
   - full 2025
   - 2026 YTD

That would be the cleanest synthesis of everything learned so far.

## Artifacts

- Absolute RVOL filter:
  - `reports/rvol-flip-filter-eth-h1-2024-stage1/`
  - `reports/rvol-flip-filter-eth-h2-2024/`
  - Script: `scripts/grid_search_lazyswing_rvol_flip_filter.py`
- Ratio-hold:
  - `reports/ratio-hold-stage1-eth-2024/`
  - `reports/ratio-hold-validation-eth/`
  - Scripts:
    - `scripts/grid_search_lazyswing_ratio_hold_stage1.py`
    - `scripts/grid_search_lazyswing_ratio_hold_2025_2026.py`
- ER / flip-density diagnostics:
  - `reports/ratio-hold-er-flip-analysis-2024.csv`
- Adaptive ST (initial step-function):
  - `reports/adaptive-st-step-eth-2024/`
  - Script: `scripts/grid_search_lazyswing_adaptive_st_step_2024.py`
- Adaptive ST (hysteresis + dwell):
  - `reports/adaptive-st-hysteresis-eth-2024/`
  - Script: `scripts/grid_search_lazyswing_adaptive_st_hysteresis_2024.py`
- Adaptive ST + ratio-hold:
- `reports/adaptive-plus-ratio-eth-2024/`
- Script: `scripts/grid_search_lazyswing_adaptive_plus_ratio_2024.py`
- Adaptive ST + dynamic regime `r/stop`:
  - `reports/adaptive-regime-ratio-stop-eth-2024/`
  - `reports/adaptive-regime-ratio-stop-eth/`
  - Script: `scripts/grid_search_lazyswing_adaptive_regime_ratio_stop_2024.py`
- Fixed ST + dynamic regime `r/stop`:
  - `reports/fixed-st-regime-ratio-stop-eth/`
  - Script: `scripts/grid_search_lazyswing_fixed_st_regime_ratio_stop_2024.py`
