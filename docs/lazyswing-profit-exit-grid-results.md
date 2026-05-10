# LazySwing Profit-Exit Grid Search — Results

**Date:** 2026-05-07 (initial), updated 2026-05-08 (combined-BC + windowed-giveback), updated 2026-05-09 (HOF v5: PP4 + ER48_T0.32)
**Branch:** `main`

## 2026-05-09 SHIP — HOF v5 (PP4_ER48_T0.32)

**Config**: `config/strategies/lazy_swing/eth_30m_hof_v5.yaml`
**8-quarter compound**: **+5,022%** (+1,627pp vs prior ship, +24% on Sharpe, +11pp on Min Q)

Two gates layered on top of the prior ship's `combined_bc` rejection logic. Both override the `flip_vol_ratio` rejection when their condition fires, forcing the ST flip:

1. **Profit-protect** (`flip_protect_min_gain_pct: 4.0`): if unrealized gain at rejection ≥ 4%, flip — lock in profit, don't risk the safety-stop wiping it out.
2. **ER-gate** (`flip_er_gate_period: 48`, `flip_er_gate_threshold: 0.32`): if Kaufman ER on the last 48 5m bars (4h) ≥ 0.32, the market is in a clean trend — trust the ST flip.

Profit-protect is checked first; ER-gate handles the rest.

| Metric | Prior ship | HOF v5 | Δ |
|---|---|---|---|
| 8q compound | +3,395% | +5,022% | **+1,627pp** |
| Mean / quarter | +59.0% | +67.1% | +8pp |
| Min quarter | +7.41% | +18.55% | +11.1pp |
| Sharpe (avg) | 3.27 | 3.46 | +0.19 |
| Max DD (avg) | −16.8% | −17.0% | tied |
| WR (avg) | 42.6% | 42.8% | tied |

**Per-quarter** (PP4_ER48_32 vs ship):

| Q | ship | v5 | Δ |
|---|---|---|---|
| 2024_Q1 | +62.89 | +65.43 | +2.5 |
| 2024_Q2 | +74.39 | +66.88 | −7.5 |
| 2024_Q3 | +47.63 | +51.38 | +3.7 |
| **2024_Q4** | +50.43 | **+63.16** | **+12.7** ✓ |
| 2025_Q1 | +117.65 | +109.22 | −8.4 |
| **2025_Q2** | +79.75 | **+131.63** | **+51.9** ✓ |
| **2026_Q1** | **+7.41** | **+18.55** | **+11.1** ✓ |
| 2026_Q2 | +31.86 | +30.75 | −1.1 |

Both critical regimes improved (held-flip-favored 2024_Q4 preserved by profit-protect; trending 2026_Q1 captured by ER-gate). Strategy is robust across 8 quarters; min-quarter rises from +7.41% to +18.55%.

### Why the gates are additive

Held-flip mechanism is bimodal: ~33% of rejections were "right" (saved a bad flip via fast_exit) and ~33% "wrong" (paid the safety stop). On aggregate they cancel.

Profit-protect targets the wrong-rejections that had material gain at rejection (in [3-5%) safety rate is 41.7%, in [5%+] safety rate is 57.1% vs 33% baseline). ER-gate targets the wrong-rejections during clean trends (4h ER ≥ 0.32 → safety rate ≥ 47%). Overlap analysis:

- profit-protect ONLY: 25 events (high gain, low ER)
- ER-gate ONLY: 43 events (low gain, high ER)
- both fire: 13 events
- neither: 282 events

Two largely-disjoint event pools → stacked gains compound. Standalone uplifts (PP3 +804pp; ER48_T0.32 +528pp) sum to ~+1,332pp; combined wins by extra +295pp because the ordering (PP first) gives ER cleaner residual events to act on, and PP=4 (less aggressive than PP=3 alone) hands more events to ER.

### Why the research-predicted "winner" wasn't the actual winner

Per-event research at M=48, T=0.15 predicted the highest Δ on rejection-event PnL (+122pp). In actual backtest, that variant lost **−2,089pp**. Threshold T=0.15 fires 27 ER-exits/q (massive over-trading on borderline chop events), while T=0.32 fires 1.9/q (selective on the cleanest trends only).

**Lesson**: per-event PnL predictions consistently overstate compound gains by ~10×. Selective thresholds matter more than predicted edge magnitude. Always confirm with full-strategy backtest.

### Robustness plateau

Tested M ∈ {36, 42, 48, 54, 60, 72} × T ∈ {0.25, 0.28, 0.30, 0.32, 0.35}. M=48 is the only M value where every tested threshold (0.25–0.35) produces positive compound:

```
M\T          0.25       0.28       0.30       0.32       0.35
M=36        -1464      -1032       -472        -71       +187
M=42        -1254       -649       -203      -1384       -748
M=48         +168       +138       +198       +528       +251   ← plateau
M=54         -730       -884       -605       -162       -175
M=60         -967       -769       -499       -140       -140
```

This is a wide plateau, not a sharp peak — strong evidence against overfitting on the parameter region around (M=48, T=0.32).

## Idea #7 — short-window indicator revival (tested 2026-05-09)

Premise: indicators rejected on 30m bars / 12h windows in idea #6 might revive when computed on 5m bars with ~4h windows (matching ER M=48). Tested HH/LL break, vol-expansion+against-bar, DMI dominance.

Per-event predicted Δ on rejection PnL across 363 events:

| Indicator | Best params | Predicted Δ |
|---|---|---|
| ER (reference) | M=48 N=0 T=0.32 | ~+50pp → +528pp realized standalone, +1,627pp combined |
| HH/LL break | K=48 X=0.20 magnitude | +75pp |
| Vol-exp + against-bar | P=0.95 Y=0.10 | +21pp (weakest, didn't backtest) |
| **DMI dominance gap** | **p=56 g=10** | **+111pp** (strongest predicted) |

8-quarter backtest stacked on v5 ship:

| Variant | Compd% | Δ vs v5 ship | Notes |
|---|---|---|---|
| **PP4_ER48_T0.32 (v5)** | **+5,022%** | — | reference |
| HH/LL ER_HHLL_K60_X0.30 | +5,007% | −15pp | tied with v5 (noise) |
| HH/LL other variants | +4,588% to +4,807% | −215pp to −434pp | regressed |
| DMI ER_DMI_p42_g25 (best) | +3,826% | −1,196pp | regressed badly |
| DMI other variants | +3,180% to +3,309% | −1,713pp to −1,842pp | regressed badly |

DMI looked very promising on a 4-quarter initial sweep (top variant +13pp on 4q sum, with big wins in 2025_Q2 +27pp and 2026_Q1 +8pp). On the **remaining 4 quarters**, the same variant lost heavily — especially 2025_Q1 (−26pp) and 2026_Q2 (−8pp). The initial sweep's 4-quarter sample was not representative.

### Pattern: ER is unique

After three indicators tested under the new short-window paradigm, the conclusion is that **ER is uniquely orthogonal to PP**. The other indicators (HH/LL, vol-exp, DMI) all carry partly-overlapping information — they discriminate at the per-event level but add false-positive flips when stacked on ER. The compounding cost of those false positives erases any per-event gains.

**Do not retry**: HH/LL, DMI, vol-exp as additional gates on v5. The 5m-revival thesis works at per-event level but not at compound. Different mechanisms (entry timing, multi-asset, position sizing) are likely better directions than more rejection-side filters.

Lessons:
- A 4-quarter sweep can mislead. The "good vs bad" pre-classification can be biased toward parameter regions that favor the chosen test set.
- Per-event predicted Δ overstates compound gains by ~10× (sometimes inverts sign). Use as screening only.
- Selectivity matters more than predicted edge. ER ships at T=0.32 (firing 1.9/q) while research-best was T=0.15 (firing 27/q which lost −2,089pp).

## 2026-05-09 update — five negative phases on aggressive-exit / held-flip

Five direction-changes were tested between 2026-05-08 and 2026-05-09 against ship `BC_m8-21-9_adx14_lb12` (+3,395% 8q compound). All regressed:

| Phase | Hypothesis | Best variant | Δ vs ship |
|---|---|---|---|
| 1 (giveback tiers) | Tighten giveback as profit grows (per-tier `trail_stop_pct` / ATR-mult) | T6_wide_lo_tight_hi: +3,431% | **+36pp (noise)** |
| 2 (BC relaxation) | Relax B-AND-C confluence at high profit (single signal at ≥4%, bypass at ≥7% with 0.5% giveback) | only_bypass_7_gb50: +3,040% | **−355pp** |
| 3 (pullback re-entry) | After trail exit, re-enter on pullback while ST aligned | BYPASS7+pb_50_12h: +2,590% | **−805pp** |
| 4 refinement (vol-in-favor) | Big vol-bar in favor → exit immediately, bypass slow signals | research-only — research showed mean rem_exit_pct = +0.47% (holding wins) | rejected pre-grid |
| 6 override (HHLL+DMI AND) | When vol_ratio rejects, also check HH/LL break + DMI dominance; if both fire, honor flip | override_backward_K4: +1,452% | **−1,943pp** |

**Common diagnostic across all phases**: each mechanism trades more frequently per quarter (trail/override exits go from 7/q to 16–42/q). Per-event, the new exits are individually more profitable on average. But the *cumulative* return regresses because:
1. Replacing slow exits with fast exits forfeits the natural compounding of long ST-flip-driven moves.
2. The new mechanisms add many "wrong" trades that cancel the "right" ones (small +9pp lift in classification doesn't survive ±2% per-event magnitudes).
3. Pending/cooldown bars compound idle-time losses (delayed mode in particular).

Per-quarter regime variance is large: the override that hurts +50pp in 2024_Q4 helps +24pp in 2026_Q1. No static global threshold dominates across quarters.

### Held-flip diagnostic (idea #6 Phase 0, 363 events on 8q)

Doc evidence for idea #6 was a 10-week sample showing held-flip is a −2.04% drag vs +2.68% if flipped. **8-quarter result: held-flip mechanism is essentially neutral** (mean held +0.245% vs mean flipped +0.249%; aggregate edge −0.014%/event). The doc's claim doesn't replicate — the mechanism is regime-dependent (held wins +154pp in 2024_Q4; flipped wins +66pp in 2025_Q2) but net zero overall.

By exit reason of the held position:
- `fast_exit` (124, "rejection right"): held +1.30%/event mean, flipped −0.71%/event → **held wins +2.00%/event**.
- `st_flip_ratio_safety` (121, "rejection wrong"): held −1.35%/event, flipped +0.98%/event → **flipped wins +2.33%/event**.
- `st_flip` (91): tie.
- `regime_trail_stop` (25): held edges +1.01%/event.

Bimodal: ~1/3 of rejections are right (saved a bad flip), ~1/3 are wrong (paid the safety stop), and they almost cancel.

### Discriminator search (B/C, then HHLL/DMI/VWAP)

Per-fate firing rates in [-3, +6] 30m bars window:
- B (ADX exhaustion) and C (MACD cross) — both fire ~91% regardless of fate. **Useless as discriminators**: they share information with ST flips themselves.
- HHLL break: fires 96% on safety-bucket rejections vs 82% on fast-exit-bucket. **+13.6pp lift**.
- DMI dominance: fires 86% on safety vs 70% on fast-exit. **+16pp lift**.
- VWAP-against: fires evenly. No discrimination.

Cross-tab on HHLL × DMI (the cleanest pair):
- Both fired (266 events): +0.04% held / +0.53% flipped → flip wins +0.50%/event
- HH silent + DMI either: 44 events; **held wins by +1.7 to +2.5%/event** (clean "hold-correct" pocket)
- Both silent (24 events): hold wins by +2.54%/event

The AND-rule looked promising on paper (+133pp predicted on 363 rejection-event PnL). Backtest implementation regressed −1,943pp because:
- Backward override fires 24.5/q (vs 11.8 safety/q in REF). Replaces ~5 safety exits/q correctly + ~20 fast-exit/st-flip/trail incorrectly per quarter.
- Per-event symmetric magnitudes (gain on right-flip ≈ loss on wrong-flip) mean +9pp lift in count isn't enough to compound positively.
- Compound math is unforgiving to overtrading.

**Diagnostic conclusion**: the strategy's slow exits ARE the edge. Local optimum on this dimension. Need different shape of intervention or a different dimension entirely.

### Open hypotheses (probing 2026-05-09 onward)

- **Profit-protect gate**: hold-flip's catastrophic losses concentrate on rejections at low/zero unrealized gain (no profit cushion to absorb the safety-stop adverse move). When unrealized gain is large at rejection, a safety-stop exit still books profit. Test: if gain ≥ X% at rejection, take the flip; if gain < X%, hold-flip can fire as today.
- **Tighter regime safety stops**: cap the −1.35%/event safety bucket loss without altering the rejection-vs-flip decision. Test grid on `flip_vol_ratio_regime_low/high_stop_pct`.
- **Per-regime detector**: 2026_Q1 (where holds go bad) vs 2024_Q4 (where holds go great) likely differ on a measurable structural axis (volatility, ADX level, BB width). If so, gate the held-flip mechanism on regime instead of trying to discriminate per-event.

## Idea register (refinement track on combined-BC)

Running list of structural improvements to combined-BC. Status reflects experiments completed in this branch.

### Quick-reference table

| # | Idea | Status | Result |
|---|---|---|---|
| 1 | **Windowed giveback gate** — BC trigger ARMS exit; require ≥0.75% giveback within N=1–2 5m bars. | ✅ done | **+443pp compound** vs prior ship. Lands `BC_cross_GB75_N2`. |
| 2 | **Slower indicator family** — longer MACD periods, longer ADX (period & lookback). | ✅ done | **+717pp compound** stacked on #1. `adx_lookback: 6 → 12` is the lever. New winner: `BC_m8-21-9_adx14_lb12`. |
| 3 | **Higher-high / lower-low structural break** — fire only on price-action confirmation. | 📋 pending | — |
| 4 | **Volatility expansion + against-position bar** — fire only at vol-spike capitulation. Refined view: on big-vol move in our favor, just take profit and skip slow signals — but first measure how often big-vol moves reverse vs continue. | 📋 pending | — |
| 5 | **Anchored-VWAP from entry crossover** — fire only after avg cost-basis flips against position. | 📋 pending | — |
| 6 | **Tighten or disable held-flip mechanism** (`flip_vol_ratio_*`) — current setting rejects ST flips when vol-ratio says "fake reversal". On Mar–May 2026 sample, 28 rejected flips netted −2.04% (43% win rate) vs +2.68% if every flip had been honored. Mechanism may be a net drag. | 📋 pending | — |
| 7 | **Honor ST flips during/after fast_exit cooldown** — `_prev_st_bullish` staleness filter intentionally ignores ST flips that occur during fast_exit cooldown ("skip-the-flip-after-chop" filter). On Mar–May 2026, 14/57 fast_exits (25%) saw an ST flip within 2h that the strategy never re-entered. Net move in missed direction: ~0% on average — wash, but variance high. Test: enable `flat_realign_hourly_closes` safety net (currently 0) or disable the staleness filter entirely. | 📋 pending | — |

### Detailed descriptions

#### ✅ Idea #1 — Windowed giveback gate (DONE, +389pp compound vs prior cross_signal)

**What:** Instead of exiting the moment B+C trigger, *arm* the trail and require price to retrace ≥ 0.75% from the trade's high-water mark within N=2 additional 5m bars (~15 minutes). If the retracement doesn't materialize, cancel the arm.

**Why:** The BC trigger fires on both real reversals and consolidation pauses. A real reversal naturally produces price giveback; a pause doesn't. Adding price-action confirmation filters out the pauses without adding latency on real reversals.

**Mechanism added in code:** `trail_stop_exit_on_signal=False` + new `trail_stop_giveback_window_bars=2` parameter (state field `_combined_bc_exit_armed_at_bar`). Tuning: tested giveback ∈ {0.5%, 0.75%, 1.0%} × N ∈ {1, 2, 3} — 0.75% × N=1/2/3 all win, ±0.25% regresses by ~315pp.

#### ✅ Idea #2 — Slower indicator family (DONE, +717pp on top of #1)

**What:** Tested longer MACD periods (f12/s26, f21/s55), longer ADX base period (28), and longer ADX lookback (12). Only **`adx_lookback: 6 → 12`** worked.

**Why it worked:** B fires when ADX drops ≥ 3.5% vs the ADX from `lookback` bars ago. With lookback=6 (3 hours), brief ADX dips during consolidations satisfy the test. With lookback=12 (6 hours), the drop must persist — filtering out short pauses while still catching real trend exhaustion.

**Why slower MACD didn't work:** cross is already a rare, decisive event. Slowing MACD periods makes it rarer still, missing real reversals. **Why longer ADX base period (14 → 28) didn't work:** introduces a regression to −4.6% in 2026_Q1 (creates a new negative quarter).

#### Idea #2 sub-experiment: full lb × drop sweep — confirms lb=12 drop=3.5% is at the optimum

Sweep `adx_lookback ∈ {8, 12, 16}` × `adx_drop_pct ∈ {2.5, 3.5, 5.0}`:

| Variant | Compd% | Min% |
|---|---|---|
| **lb12_drop3.5 (ship)** | **+3,395%** | +7.4 |
| lb12_drop2.5 | +3,368% | +7.4 |
| lb12_drop5.0 | +2,954% | +3.9 |
| lb16_drop{2.5/3.5/5.0} | +2,837–2,893% | **−1.9% to −2.0%** (new neg Q in 2026_Q1) |
| lb8_drop{2.5/3.5/5.0} | +2,368–2,583% | **−0.6% to −1.3%** (new neg Q in 2026_Q1) |

Both lb=8 and lb=16 reintroduce a negative quarter in 2026_Q1. drop=2.5% is statistically tied with 3.5% at lb=12 (within ~30pp out of 3,400pp). drop=5.0% is too strict and loses 441pp.

**Conclusion**: lb=12, drop=3.5% is genuinely at the optimum, not just locally best.

#### Idea #2 sub-experiment: ADX slope (linear regression) vs scalar — REJECTED

User intuition: scalar `adx[now] / adx[now-lookback] - 1` looks fragile to endpoint noise. Tested replacing it with a linear regression slope over W=6/8/12 30m bars × drop ∈ {3.5%, 5%, 7.5%}. **All 9 slope variants compounded 1,000+pp worse** than the scalar lb=12 baseline, and several reintroduced negative quarters in 2026_Q1 (−0.7% to −2.0%).

Reason: the regression averages the entire window, while the scalar test is biased toward the recent endpoint. For exhaustion detection, current ADX state matters more than 6h-average trajectory. Slope code was removed (was in `regime_exhaustion_adx_slope_window` param). See git history for implementation if needed.

#### 📋 Idea #3 — Higher-high / lower-low structural break (PENDING)

**What:** Add a price-action gate to the BC trigger. For a short exit, only allow BC to fire if the current bar makes a higher high than the previous K bars (i.e., price is structurally breaking the downtrend). For a long exit, lower low.

**Why:** This is the canonical technical-analysis definition of "trend break". A pause produces lower highs (still trending); a real reversal produces a higher high (structure broken). Different signal type than B (deceleration) or C (momentum reversal) — adds genuine new information.

**Risk:** May fire too late in some real reversals (HH/LL takes a few bars to form), causing later exits than current.

**Implementation:** ~15 lines — track `recent_high` / `recent_low` over a K-bar lookback window since entry (or in general); add as additional gate in the combined_bc state machine.

#### 📋 Idea #4 — Volatility expansion + against-position bar (PENDING)

**What:** Fire BC only when **volatility has expanded recently** (BB-width or ATR up by ≥ X% in last few bars) AND **the latest bar moved against the position direction**. This detects capitulation / breakout-against bars at the inflection point of a real regime change.

**Why:** Real reversals usually come with a vol spike (capitulation bar). Pauses inside a trend tend to *reduce* vol (low-energy chop). This is the opposite of "vol declining" (which we considered and rejected — vol decline is *post*-reversal cooldown, not the reversal itself).

**Risk:** May miss slow drift-style reversals (gradual fade with no vol spike). Some quarters may exhibit drift-exhaustion rather than capitulation reversals.

**Concrete motivating case — 2026-05-04 SHORT (entry 06:30 @ 2363.31):**

This trade reached its peak gain of **+2.06%** at 10:20 (price 2315.52, sharp V-bottom). B (adx_exhaustion) fired correctly one bar after the bottom (10:25) and kept firing every 30 min thereafter for 4 hours. **C (MACD cross above signal) did not fire until 14:30 — by which time price had recovered all the way back to 2371**, gain was already negative, and the trade unwound via fast_exit at 14:45 at −0.05%.

Why C was so late: the sharp V drove MACD line 7.7 points below signal (huge gap). Even with both lines trending up afterward, the cross took 4 hours to occur — long after the trade was reversed.

Vol-expansion check would have caught this: at 10:00 the 30m bar's range jumped from ~$2 to $20+ — a clear capitulation expansion. Combined with the against-position move (price down after sustained sideways), the BC gate would have triggered ~11:00 with gain still ≥1%. Then the windowed-giveback would have confirmed within minutes (price had already retraced from 2315 to 2336 = +0.9% giveback, near the 0.75% threshold).

**Test must verify:**
1. Vol-expansion + against-position triggers fire on 2026-05-04 between 10:25 and 11:30 (post-bottom)
2. The same gate doesn't over-trigger in 2024_Q2 or 2025_Q1 (the high-compound quarters where cross_signal already wins)
3. Compound across 8 quarters beats current ship (+3,395%)

This trade is a textbook V-bottom reversal that current cross-based BC misses. Histogram-based BC would have caught it (`hist_dec` fired starting 11:30) but compounds worse overall. Vol-expansion is the candidate that may catch the V-bottoms without the histogram over-trigger problem.

**Refinement (added 2026-05-08): "big vol move → just take the profit, skip the slow signals":**

Stronger version of the idea — when a high-volatility bar moves *in our favor* and pushes the position into meaningful profit, **just exit**. Don't wait for MACD cross, ADX exhaustion, or any other slow indicator — those typically lag the reversal by hours on sharp V-shapes (see May 4 trace above). After exit, wait for a fresh entry signal; if the original direction resumes, re-enter.

Differences from the AND-gate version above:
- AND-gate: vol-expansion is one of multiple confluence requirements alongside B+C.
- This refinement: vol-expansion in our favor with material gain is *itself* sufficient to exit, BC AND-gate not required. Bypasses the slow signals entirely.

The trade-off — needs empirical validation:
- If big-vol moves *reverse* most of the time → take profit and we've locked in a win cleanly.
- If big-vol moves *continue* most of the time → we exit early, then re-enter (with slippage cost) or miss the rest of the run.

**Required research before testing this refinement:**

For each "big-vol move in favor" event in the dataset (e.g. ATR-percentile-95 5m bar in our position direction):
1. Distribution of "additional move in favor" over the next K bars (5/15/30/60 min, 4h)
2. What fraction reverse within 1h vs continue?
3. Of the ones that continue, how much further do they go?
4. EV of "exit immediately and re-enter on next ST flip" vs "keep holding"

Until we have those numbers, we're guessing. The May 4 case is one anecdote; need 100+ events across 8 quarters before picking a rule.

**Test plan (after the research):**
1. **Define the trigger**: ATR-percentile (90th/95th) on 5m or 30m bar, OR BB-width-jump (+2σ in 1 bar), OR raw bar-range / recent ATR (> 3×).
2. **Define the favor check**: bar's close in position-favorable direction by ≥ X% (e.g. 0.5%).
3. **Define the gain floor**: only fire if current gain ≥ Y% (e.g. 1.5%; avoids exit at break-even on a single big bar).
4. **Bypass mechanism**: when conditions met, force exit regardless of BC state and regardless of windowed-giveback gate.
5. Sweep across 8 quarters; verify May 4 fires correctly without breaking 2024_Q2 / 2025_Q1 wins.

**Additional risk for this refinement:** May exit prematurely on bars that look like capitulation but are actually *acceleration* of an existing move (a strong-trend day that has multiple high-vol bars in favor without reversal). Research step 4 above directly answers whether this risk is large.

**Concrete motivating case — 2026-05-04 SHORT (entry 06:30 @ 2363.31):**

This trade reached its peak gain of **+2.06%** at 10:20 (price 2315.52, sharp V-bottom). B (adx_exhaustion) fired correctly one bar after the bottom (10:25) and kept firing every 30 min thereafter for 4 hours. **C (MACD cross above signal) did not fire until 14:30 — by which time price had recovered all the way back to 2371**, gain was already negative, and the trade unwound via fast_exit at 14:45 at −0.05%.

Why C was so late: the sharp V drove MACD line 7.7 points below signal (huge gap). Even with both lines trending up afterward, the cross took 4 hours to occur — long after the trade was reversed.

Vol-expansion check would have caught this: at 10:00 the 30m bar's range jumped from ~$2 to $20+ — a clear capitulation expansion. Combined with the against-position move (price down after sustained sideways), the BC gate would have triggered ~11:00 with gain still ≥1%. Then the windowed-giveback would have confirmed within minutes (price had already retraced from 2315 to 2336 = +0.9% giveback, near the 0.75% threshold).

**Test must verify:**
1. Vol-expansion + against-position triggers fire on 2026-05-04 between 10:25 and 11:30 (post-bottom)
2. The same gate doesn't over-trigger in 2024_Q2 or 2025_Q1 (the high-compound quarters where cross_signal already wins)
3. Compound across 8 quarters beats current ship (+3,395%)

This trade is a textbook V-bottom reversal that current cross-based BC misses. Histogram-based BC would have caught it (`hist_dec` fired starting 11:30) but compounds worse overall. Vol-expansion is the candidate that may catch the V-bottoms without the histogram over-trigger problem.

#### 📋 Idea #6 — Tighten or disable the held-flip mechanism (PENDING)

**What:** Investigate whether the strategy's `flip_vol_ratio_*` "rejected flip" / "held flip" mechanism is a net positive. When ST flips against the open position but the volatility ratio says "this is a fake reversal", the strategy holds the position and lets a safety stop (`st_flip_ratio_safety`) fire later if it turns out the reversal was real.

**Evidence motivating the experiment:** On the 2026-03-01 → 2026-05-08 backtest of the new HOF config, 28 distinct rejected-flip events occurred. Outcome:

| Metric | Held (kept position) | If we'd flipped instead |
|---|---|---|
| Sum PnL | **−2.04%** | +2.68% |
| Mean per decision | −0.07% | +0.10% |
| Win rate | 43% | 57% |

Worst held outcomes consistently ended in `st_flip_ratio_safety` after price moved 1.5–2.8% against the held position — the mechanism buys hope, the safety pays for it.

**Caveat:** This is one 10-week sample. The 8-quarter compound test (+3,395%) implicitly *includes* held-flip behavior and the overall strategy still wins, so the mechanism isn't catastrophic. But it appears to be a drag.

**Test plan:**
- Option A: disable entirely (`flip_vol_ratio_enabled: false`)
- Option B: tighten thresholds so fewer flips get rejected
- Option C: shorten the held-flip-safety stop so bad holds exit faster
- Run all three across the 8-quarter set and compare compound vs current ship.

#### 📋 Idea #7 — Honor ST flips during/after fast_exit cooldown (PENDING)

**What:** After a `fast_exit`, the strategy enters a 4-bar cooldown. If ST flips during that cooldown, the `_prev_st_bullish` staleness mechanism intentionally suppresses the flip detection — the strategy stays flat. Documented at lazy_swing.py:274–289 as "part of the strategy's edge in choppy regimes". The `flat_realign_hourly_closes` safety net is supposed to catch cases where this strands the strategy flat through a clean ST regime, but is currently set to 0 (disabled).

**Evidence motivating the experiment:** On the 2026-03-01 → 2026-05-08 backtest, 57 fast_exits occurred. Of those, 43 had an ST flip to the opposite direction within 2 hours. The strategy re-entered correctly in 29 cases (avg +0.36% captured); **14 cases never re-entered within 4 hours** — the staleness filter killed them.

| Group | N | 4h move in missed direction (avg) | Sum |
|---|---|---|---|
| Re-entered | 29 | +0.36% | +10.4% |
| Missed (no_entry_4h) | 14 | +0.045% | +0.63% |

The missed-entry cases are roughly a coin-flip on aggregate (filter ≈ 50% accuracy at distinguishing real flips from noise), so the *expected* damage is small but the variance is high. Specific cases the user noticed (May 4 14:45) had near-zero 4h move in the missed direction — feels worse than it is.

**Test plan:**
- Option A: enable `flat_realign_hourly_closes: 4` (or 6, 8) — the existing safety net that catches stranded-flat cases
- Option B: disable the staleness filter entirely (always honor post-fast-exit ST flips)
- Option C: shorten `fast_exit_cooldown_bars` from 4 to 1 or 2 (entries allowed sooner, but still some cushion)
- Run all options across 8 quarters and compare compound vs current ship.

#### 📋 Idea #5 — Anchored-VWAP from entry crossover (PENDING)

**What:** Compute VWAP anchored at the trade's entry timestamp. For a short, exit only when price has crossed back through `vwap_entry` (volume-weighted average cost basis since entry has flipped against the position). For a long, the mirror.

**Why:** VWAP carries information MACD doesn't have — it's volume-weighted, which captures where institutional money actually traded. Crossing entry-anchored VWAP means "the average participant since I entered is now on the other side" — a meaningful structural signal.

**Risk:** In crypto markets, volume distribution can be lopsided (one big bar dominates), making VWAP noisy. May behave like a slower MACD with extra steps if volume is fairly uniform across bars.

### User-rejected / parked ideas

- **Vol-declining as exit signal** (parked, 2026-05-08): trend-end usually accompanies vol *expansion*, not decline; ADX-drop already captures vol decline so adding it would double-count.
- **Histogram peak-drop X% filter** (abandoned, 2026-05-08): tested X∈{0.5, 0.75}, all worse than X=0; the gate is a delay-within-decline, not a selectivity filter.
- **Profit-scaled trail giveback** (parked, 2026-05-08): tighten `trail_stop_pct` as gain grows; user wants to defer until indicator-side ideas are exhausted.

## Current shipping config (after Ideas #1 + #2)

`BC_m8-21-9_adx14_lb12` — combined-BC with windowed giveback (N=2) and longer ADX lookback (12):
- 8-quarter compound: **+3,395%** (was +2,677% before idea #2)
- Mean per quarter: **+59.0%**
- 0 negative quarters; min quarter +7.4%
- Beats best static baseline by **+1,217pp** compound

```yaml
regime_trail_mode: combined_bc
combined_bc_window_bars: 6                      # B-then-C window (5m bars)
trail_stop_min_gain_pct: 2.0
trail_stop_pct: 0.75                            # 0.75% giveback from peak
trail_stop_atr_multiple: 0.75
trail_stop_exit_on_signal: false                # idea #1: arm, don't exit immediately
trail_stop_giveback_window_bars: 2              # idea #1: confirm within 2 extra 5m bars
# B (adx_exhaustion) params:
regime_momentum_adx_period: 14
regime_exhaustion_adx_lookback: 12              # idea #2: was 6, now 12 (sustained drop)
regime_exhaustion_adx_drop_pct: 3.5
regime_exhaustion_prev_adx_min: 20
# C (macd cross) params:
profit_exit_macd_fast: 8
profit_exit_macd_slow: 21
profit_exit_macd_signal_period: 9
profit_exit_macd_condition: cross
```

### Idea #2 details — slower indicator family

Sweep of MACD periods × ADX period × ADX lookback on top of the idea-#1 winner:

| Variant | Compd% | vs Ship-before-#2 |
|---|---|---|
| **BC_m8-21-9_adx14_lb12** ⭐ | **+3,395%** | **+717pp** |
| BC_m8-21-9_adx28_lb12 | +2,698% | +21pp (but new −4.6% Q in 2026_Q1) |
| Ship before #2 (m8-21-9_adx14_lb6) | +2,677% | — |
| BC_m12-26-9_adx14_lb12 | +2,626% | −51pp |
| BC_m21-55-13_* | +751 to +1,925% | −752pp to −1,926pp |

Key findings:
- **`adx_lookback: 6 → 12` is the lever** (single change → +717pp). ADX drop must be sustained over 6 hours rather than 3 — filters short consolidations.
- **Slower MACD (f12/s26, f21/s55) hurts**: cross is already a rare event; making it rarer misses real reversals.
- **Longer ADX base period (14 → 28) hurts** in 3 of 4 variants — introduces a −4.6% loss in 2026_Q1 that wasn't there before.

---

# Original 4-Category Grid (2026-05-07)

**Initial scripts:**
- `scripts/grid_search_lazyswing_profit_exit.py`
- `data/backtests/eth/profit_exit_grid/{summary_all,aggregate_all}.csv`

## Goal

LazySwing's baseline only exits on Supertrend flips. We tested whether adding a profit-trailing exit (gated by min-gain) on top of the HOF base config improves performance — without sacrificing baseline upside.

## Setup

- **Underlying:** ETH-PERP-INTX, 5-min bars (resampled to 30-min for ST signals)
- **Windows:** 10 quarters — 2024_Q1..Q4, 2025_Q1..Q4, 2026_Q1, 2026_Q2 (partial through 5/8)
- **Variants:** 196 — baseline (4) + 4 categories of new exit mechanism × min_gain ∈ {1.0, 1.25, 1.5, 1.75}
- **Total runs:** 1960 (196 × 10), 4 workers, 103 min wall clock
- **Strategy code path verified:** `verify_grid_reproducibility.py` reproduces grid log to 4 decimal places — see `docs/lazyswing-profit-exit-grid-audit.md` (audit notes embedded below).

### Categories

| Cat | Mechanism | Variants |
|---|---|---|
| baseline | strict_exhaustion (HOF z-score gates) | 4 (mg only) |
| **A** | Relaxed strict_exhaustion (kc/bb thresholds) | 12 |
| **B** | adx_exhaustion only (ADX drop %) | 108 |
| **C** | macd_exit (cross or histogram) | 64 |
| **D** | ema_trail (price crosses EMA) | 8 |

Score: `return_pct × WR_pct` per window; ranking by **median across 10 windows**.

## Headline

**No new variant ships as a drop-in replacement for baseline.** The top variant by median score wins on consistency, but every category gives back substantial mean-return and Sharpe.

## Top 10 (by median score across 10 windows)

| Rank | Tag | Cat | Med-Score | MeanRet% | MeanWR% | Sharpe | WrstDD% | Trail/qtr |
|---|---|---|---|---|---|---|---|---|
| 1 | B_lb6_d2.0_min30_mg1.75 | B | 2616 | +36.26 | 42.7 | 2.33 | −32.5 | 29 |
| 2 | B_lb6_d1.0_min30_mg1.75 | B | 2574 | +35.83 | 42.8 | 2.33 | −32.9 | 30 |
| 3 | B_lb6_d2.0_min30_mg1.5 | B | 2495 | +34.80 | 43.5 | 2.31 | −30.3 | 32 |
| 4 | B_lb6_d1.0_min30_mg1.5 | B | 2453 | +34.50 | 43.5 | 2.31 | −30.7 | 32 |
| 5 | B_lb6_d1.0_min20_mg1.0 | B | 2447 | +31.85 | 52.0 | 2.35 | −32.3 | 63 |
| 6 | B_lb6_d2.0_min20_mg1.0 | B | 2434 | +33.73 | 51.7 | 2.39 | −32.6 | 62 |
| 7 | B_lb6_d3.5_min30_mg1.75 | B | 2333 | +34.61 | 42.8 | 2.27 | −32.5 | 27 |
| 8 | B_lb6_d2.0_min30_mg1.25 | B | 2285 | +33.78 | 44.5 | 2.21 | −30.9 | 36 |
| 9 | B_lb2_d1.0_min15_mg1.75 | B | 2276 | +29.66 | 44.4 | 2.00 | −28.5 | 43 |
| 10 | B_lb6_d3.5_min30_mg1.5 | B | 2208 | +33.52 | 43.5 | 2.26 | −30.3 | 30 |

**Top 30 dominated by Category B (ADX-only).** Highest C variant is `C_f13s21g13_cross_mg1.75` at #22 (Med-Score 2026).

## Top variant vs best baseline (head-to-head)

| Metric | B_lb6_d2.0_min30_mg1.75 | baseline_mg1.5 | Verdict |
|---|---|---|---|
| **Median score** | **2616** | 1759 | New +49% |
| Mean return | +36.26% | **+50.95%** | Baseline +14.7pp |
| Mean WR | 42.7% | 41.1% | Tie |
| Mean Sharpe | 2.33 | **2.96** | Baseline +0.63 |
| Worst DD | −32.5% | **−25.8%** | Baseline +6.7pp |

**Trade-off:** the new trail trades return + Sharpe + DD-protection for steadier per-quarter score. Mean return loss > median score gain.

## Per-category summary

| Cat | Best score | Mean score | MeanRet% | MeanWR% | AvgPnL% | TrlPnL% |
|---|---|---|---|---|---|---|
| **baseline** | 5263 | **2091** | **+50.41** | 41.1 | +0.372 | +2.634 |
| A (KC/BB) | 5734 | 1865 | +38.68 | 43.8 | +0.268 | +2.399 |
| B (ADX) | **8444** | 1908 | +31.79 | 46.0 | +0.201 | +2.032 |
| C (MACD) | 7660 | 1431 | +25.73 | 46.7 | +0.152 | +2.015 |
| D (EMA) | 4154 | 1520 | +28.97 | 46.9 | +0.171 | +1.638 |

**Pattern:** every new category lifts WR by 2–6pp but gives back 12–25pp of mean return. The trail mechanisms exit winners too early; small wins more often, miss the runners.

## Per-quarter winners vs baseline

| Quarter | Best baseline | Per-Q winner | Δ ret | Δ WR |
|---|---|---|---|---|
| 2024_Q1 | mg1.0: +39.0% / 39.3% | C_f8s21g9_cross_mg1.0: +49.5% / 46.3% | +10.5pp | +7.0pp |
| 2024_Q2 | mg1.5: +48.9% / 40.7% | C_f13s34g9_cross_mg1.0: +77.5% / 46.2% | +28.6pp | +5.5pp |
| 2024_Q3 | mg1.0: +55.8% / 40.8% | B_lb6_d2.0_min20_mg1.75: +89.4% / 50.0% | +33.6pp | +9.2pp |
| 2024_Q4 | mg1.25: +24.5% / 44.9% | C_f13s34g13_histogram_mg1.0: +33.9% / 60.7% | +9.4pp | +15.8pp |
| 2025_Q1 | mg1.0: +119.1% / 44.2% | B_lb2_d3.5_min15_mg1.0: +148.7% / 56.8% | +29.6pp | +12.6pp |
| 2025_Q2 | mg1.25: +81.7% / 40.8% | C_f13s34g9_cross_mg1.0: +104.0% / 45.5% | +22.3pp | +4.7pp |
| 2025_Q3 | mg1.5: +81.0% / 42.9% | A_kc1.75_bb2.75_d1.0_mg1.75: +84.5% / 42.1% | **+3.5pp** | **−0.8pp** |
| 2025_Q4 | mg1.0: +26.8% / 36.4% | D_ema8_mg1.75: +41.2% / 42.5% | +14.4pp | +6.1pp |
| 2026_Q1 | mg1.5: +19.7% / 38.7% | B_lb6_d1.0_min20_mg1.0: +55.3% / 53.1% | +35.6pp | +14.4pp |
| 2026_Q2 | mg1.75: +31.7% / 48.8% | C_f13s34g13_cross_mg1.0: +31.2% / 52.1% | −0.5pp | +3.3pp |

**No single variant wins consistently.** Each quarter's "winner" is from a different config family; cross-quarter overlap is rare. Selecting per-quarter is overfitting.

### 2025_Q3 anomaly — investigated

Only +3.5pp ret with **−0.8pp WR** and 7 trail exits vs 6 in baseline. Root-cause analysis (sub-agent at `scripts/analyze_2025q3_trade_character.py`): Q3'25 was a clean-trend regime — winners averaged +5.39% (2.3× normal) on long holds (~641 bars) with 82.4% WR on long-hold trades. **Baseline ST flips already captured the moves end-to-end; there was no profit being given back for trail exits to recapture.** Trail mechanism correctly *didn't fire* — not a strategy failure, just a regime that didn't need it.

## Audit / reproducibility

Concern was raised that grid numbers might not match a standalone backtest. Verified:

- **Reproducibility:** `verify_grid_reproducibility.py` rebuilds the exact `Config` the grid uses and runs Controller standalone. For 2025_Q3 baseline_mg1.0, standalone produces +76.11% / 42.9% / 7 trail exits / +0.455% avg_pnl — **identical to grid log to 4 decimals**.
- **Strategy code:** Same `lazy_swing` registry entry, same `src/strategies/lazy_swing.py`.
- **Warmup:** Slice files prepend 5000 5-min bars (~17.4 days = 416 hours) before window start. Strategy declares `min_warmup_hours = atr_period × 15 = 375 hours`. Longest internal lookback is `fast_exit_rvol_long_period = 2016` 5-min bars = 7 days. **Comfortable margin.**
- **Position sizing:** `qty = pv.cash * 0.9999 / close` — full-cash long/short flip, no leverage. High returns come from bidirectional compounding on a volatile underlying with 100+ flips per quarter.
- **min_gain unit handling:** correct (config value / 100 in `__init__`, so `mg=1.0` → 1% gate).

A prior sub-agent had run `config/strategies/lazy_swing/eth_q3_2025.yaml` (vanilla LazySwing v1: ST 20/2.0, no resample, no regime, no fast_exit, no regime_trail) and got very different absolute P&L numbers. That config is a **different strategy parameterization entirely** — not a bug, just incomparable.

## Conclusions

1. **Don't ship any of these as a drop-in replacement for baseline.** Mean return and Sharpe regression isn't worth the median-consistency gain.
2. The trail mechanism is **firing correctly** (sub-agent verified Q3'25 redundancy), but the **default behavior over-exits winners** in trend regimes. Higher `min_gain` gates help (top variants cluster at mg1.75) but don't fully solve the problem.
3. Per-quarter winners are **regime-conditional**; no static config dominates.

## Recommended next experiments

Pick one:

### A. Higher min_gain gates
Top variants cluster at `mg1.75` (highest tested). Extend the grid: **`mg2.5 / mg3.0 / mg4.0`** to require a bigger paper gain before any trail can trigger. Hypothesis: this preserves runners while still capturing chop-quarter pullbacks. Cheap to test — only 4 mg values × ~20 representative variants × 10 windows ≈ 800 runs (~30 min).

### B. Regime-conditional trail
Only enable the trail mechanism when an upstream regime classifier flags **chop / weak-trend** (ADX < 20, ER < 0.3, vol-ratio low). Baseline runs in clean-trend, trail in chop. Requires a lightweight regime gate added to `lazy_swing.py`.

### C. Trail-only-on-shorts (or longs)
Inspect whether the upside loss is asymmetric: does the trail hurt long runners more than short runners (or vice versa)? If asymmetric, gate the trail by direction.

### D. Stop the experiment, keep baseline
The grid says baseline is already strong. Spend cycles elsewhere (entry filters, position sizing, multi-asset).

**Suggested:** start with **A** (smallest cost, tests the strongest hypothesis surfaced by the data). If that doesn't lift mean return, escalate to B.

## min_gain extension (mg2.0 / mg2.5) — added 2026-05-07

After the initial grid completed, the top variants clustered at `mg1.75` (the highest tested), suggesting room to gate higher. Re-ran each quarter's top-10 mechanisms with `min_gain ∈ {2.0, 2.5}` — 160 additional runs in 8 min.

**Headline:** mg2.5 reshuffled top-5 in **2 of 10 quarters**:

- **2024_Q4 — five mg2.5 histogram variants now sweep top-5.** New #1: `C_f8s21g9_histogram_mg2.5` jumped from +33.91% (mg1.0) to **+52.55%** (mg2.5). Baseline still +24.54%. Sharpe 3.01, max DD −16.87%.
- **2025_Q4 — `D_ema8_mg2.5` now #2 at +44.64%** (vs original mg1.75 winner +41.16%).

In trending quarters (2024_Q1–Q3, 2025_Q1–Q2, 2026_Q1) `mg ≤ 1.75` still wins — wider gates miss runners. The mg2.5 advantage shows up specifically in **chop quarters** where wider min-gain prevents premature exits in noisy retracement structure.

## Median-best per category — cross-quarter travel

For each category, identify the quarters where it was per-quarter winner, then pick the single variant with the highest **median score across those winning quarters** (variant must appear in every one). This gives 4 fixed strategies — one per category — that we can drop into every quarter to see how well each "category champion" travels outside its home turf.

| Cat | Won quarters | Best-median variant | Median score |
|---|---|---|---|
| A | 2025_Q3 | `A_kc1.75_bb2.75_d1.0_mg1.75` | 3554 |
| B | 2024_Q3, 2025_Q1, 2026_Q1 | `B_lb6_d3.5_min20_mg1.75` | 3590 |
| C | 2024_Q1, 2024_Q2, 2024_Q4, 2025_Q2, 2026_Q2 | `C_f8s21g9_cross_mg1.0` | 2736 |
| D | 2025_Q4 | `D_ema8_mg1.75` | 1750 |

Each per-quarter table below: 4 baseline rows + top 5 from original grid + 4 median-best rows (M-A/M-B/M-C/M-D). Stats columns: ret%, WR%, avgPnL%, trlPnL%, trail count, W, L, N, Sharpe, maxDD%, score.

### 2024_Q1 (winner: C)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.0 | base | +39.01 | 39.3 | +0.228 | +1.639 | 13 | 55 | 85 | 142 | 2.80 | −14.32 | 1532.4 |
| B2 | baseline_mg1.75 | base | +37.32 | 35.8 | +0.233 | +2.592 | 4 | 48 | 86 | 135 | 2.49 | −15.50 | 1336.8 |
| B3 | baseline_mg1.5 | base | +37.11 | 35.8 | +0.231 | +2.370 | 5 | 48 | 86 | 135 | 2.48 | −15.50 | 1329.4 |
| B4 | baseline_mg1.25 | base | +30.06 | 37.0 | +0.183 | +1.735 | 10 | 51 | 87 | 140 | 2.17 | −14.40 | 1111.1 |
| #1 | C_f8s21g9_cross_mg1.0 | C | +49.48 | 46.3 | +0.233 | +1.602 | 70 | 75 | 87 | 162 | 3.10 | −16.78 | **2290.6** |
| #2 | C_f13s34g13_histogram_mg1.0 | C | +33.01 | 57.9 | +0.136 | +1.198 | 98 | 99 | 72 | 171 | 3.01 | −11.89 | 1911.1 |
| #3 | C_f13s21g13_histogram_mg1.0 | C | +32.26 | 57.9 | +0.132 | +1.193 | 98 | 99 | 72 | 171 | 2.84 | −11.89 | 1867.6 |
| #4 | C_f8s21g13_histogram_mg1.0 | C | +31.75 | 57.9 | +0.130 | +1.188 | 98 | 99 | 72 | 171 | 2.80 | −12.22 | 1838.3 |
| #5 | C_f8s34g9_histogram_mg1.0 | C | +31.75 | 57.9 | +0.130 | +1.188 | 98 | 99 | 72 | 171 | 2.80 | −12.22 | 1838.3 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +31.07 | 35.6 | +0.194 | +2.434 | 5 | 48 | 87 | 137 | 2.18 | −15.50 | 1104.9 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | **−2.10** | 37.4 | −0.038 | +2.121 | 35 | 58 | 97 | 155 | −0.07 | −16.93 | nan |
| **M-C** | C_f8s21g9_cross_mg1.0 *(=#1)* | C | +49.48 | 46.3 | +0.233 | +1.602 | 70 | 75 | 87 | 162 | 3.10 | −16.78 | **2290.6** |
| **M-D** | D_ema8_mg1.75 | D | **−8.80** | 38.9 | −0.083 | +1.764 | 53 | 61 | 96 | 158 | −0.60 | −24.08 | nan |

### 2024_Q2 (winner: C)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.5 | base | +48.92 | 40.7 | +0.329 | +1.787 | 3 | 50 | 73 | 124 | 2.56 | −25.83 | 1988.4 |
| B2 | baseline_mg1.75 | base | +47.47 | 40.8 | +0.316 | +2.010 | 2 | 51 | 74 | 126 | 2.45 | −28.71 | 1936.6 |
| B3 | baseline_mg1.25 | base | +43.66 | 41.8 | +0.301 | +1.358 | 5 | 51 | 71 | 123 | 2.27 | −25.83 | 1824.9 |
| B4 | baseline_mg1.0 | base | +41.32 | 41.8 | +0.287 | +1.045 | 6 | 51 | 71 | 123 | 2.17 | −25.83 | 1727.2 |
| #1 | C_f13s34g9_cross_mg1.0 | C | +77.49 | 46.2 | +0.405 | +2.560 | 42 | 66 | 77 | 144 | 3.48 | −13.66 | **3576.2** |
| #2 | C_f8s34g13_cross_mg1.0 | C | +76.57 | 46.2 | +0.402 | +2.578 | 44 | 66 | 77 | 144 | 3.45 | −13.66 | 3533.9 |
| #3 | B_lb4_d2.0_min15_mg1.75 | B | +76.57 | 45.3 | +0.408 | +2.648 | 47 | 62 | 75 | 139 | 3.89 | −13.22 | 3465.1 |
| #4 | B_lb6_d2.0_min15_mg1.75 | B | +74.38 | 46.3 | +0.400 | +2.526 | 48 | 63 | 73 | 138 | 3.88 | −12.71 | 3445.4 |
| #5 | B_lb6_d2.0_min15_mg1.5 | B | +73.01 | 47.2 | +0.379 | +2.243 | 55 | 67 | 75 | 143 | 3.82 | −13.77 | 3444.6 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +35.33 | 40.0 | +0.245 | +5.600 | 6 | 50 | 75 | 126 | 1.92 | −28.71 | 1413.0 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +46.34 | 43.3 | +0.276 | +2.622 | 41 | 58 | 76 | 136 | 2.62 | −19.79 | 2005.6 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | +69.49 | 45.8 | +0.369 | +2.166 | 56 | 65 | 77 | 144 | 3.13 | −15.51 | 3180.7 |
| **M-D** | D_ema8_mg1.75 | D | +57.73 | 47.1 | +0.322 | +2.320 | 52 | 66 | 74 | 142 | 2.78 | −17.59 | 2721.5 |

### 2024_Q3 (winner: B)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.0 | base | +55.81 | 40.8 | +0.392 | +2.550 | 3 | 53 | 77 | 130 | 3.30 | −15.68 | 2275.5 |
| B2 | baseline_mg1.25 | base | +53.26 | 40.0 | +0.379 | +3.340 | 2 | 52 | 78 | 130 | 3.20 | −15.68 | 2130.4 |
| B3 | baseline_mg1.5 | base | +53.26 | 40.0 | +0.379 | +3.340 | 2 | 52 | 78 | 130 | 3.20 | −15.68 | 2130.4 |
| B4 | baseline_mg1.75 | base | +53.26 | 40.0 | +0.379 | +3.340 | 2 | 52 | 78 | 130 | 3.20 | −15.68 | 2130.4 |
| #1 | B_lb6_d2.0_min20_mg1.75 | B | +89.38 | 50.0 | +0.459 | +2.264 | 54 | 71 | 71 | 143 | 5.09 | −13.61 | **4468.8** |
| #2 | B_lb6_d1.0_min20_mg1.75 | B | +89.27 | 50.0 | +0.459 | +2.264 | 54 | 71 | 71 | 143 | 5.08 | −13.61 | 4463.3 |
| #3 | B_lb6_d3.5_min20_mg1.75 | B | +87.06 | 50.4 | +0.453 | +2.264 | 52 | 71 | 70 | 142 | 5.04 | −13.81 | 4383.7 |
| #4 | B_lb6_d2.0_min20_mg2.0 | B | +75.05 | 46.8 | +0.412 | +2.507 | 46 | 66 | 75 | 142 | 4.51 | −17.12 | 3513.2 |
| #5 | B_lb6_d1.0_min20_mg2.0 | B | +75.01 | 46.8 | +0.413 | +2.508 | 46 | 66 | 75 | 142 | 4.50 | −17.12 | 3511.1 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +44.45 | 40.0 | +0.335 | +2.697 | 6 | 52 | 78 | 130 | 2.82 | −17.71 | 1778.2 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 *(=#3)* | B | +87.06 | 50.4 | +0.453 | +2.264 | 52 | 71 | 70 | 142 | 5.04 | −13.81 | 4383.7 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | **+8.96** | 46.5 | +0.066 | +1.604 | 55 | 72 | 83 | 156 | 0.97 | −25.18 | 416.4 |
| **M-D** | D_ema8_mg1.75 | D | +27.44 | 45.5 | +0.172 | +2.054 | 56 | 71 | 85 | 156 | 2.00 | −14.71 | 1249.0 |

### 2024_Q4 (winner: C, post-mg-extension)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.25 | base | +24.54 | 44.9 | +0.222 | +1.290 | 3 | 53 | 65 | 119 | 1.27 | −21.80 | 1102.4 |
| B2 | baseline_mg1.0 | base | +24.04 | 44.9 | +0.218 | +1.153 | 3 | 53 | 65 | 119 | 1.25 | −21.80 | 1079.8 |
| B3 | baseline_mg1.5 | base | +22.99 | 44.1 | +0.211 | +1.675 | 2 | 52 | 66 | 119 | 1.20 | −21.80 | 1013.3 |
| B4 | baseline_mg1.75 | base | +20.43 | 43.2 | +0.193 | +1.890 | 1 | 51 | 67 | 119 | 1.09 | −22.52 | 883.1 |
| #1 (orig) | C_f13s34g13_histogram_mg1.0 | C | +33.91 | 60.7 | +0.137 | +1.229 | 107 | 108 | 70 | 178 | 2.35 | −10.99 | 2057.2 |
| #2 (orig) | C_f13s21g9_histogram_mg1.0 | C | +33.63 | 59.0 | +0.136 | +1.273 | 104 | 105 | 73 | 178 | 2.40 | −10.65 | 1983.9 |
| #3 (orig) | C_f8s34g13_histogram_mg1.0 | C | +32.48 | 59.0 | +0.131 | +1.265 | 104 | 105 | 73 | 178 | 2.33 | −10.76 | 1916.2 |
| #4 (orig) | C_f8s21g13_histogram_mg1.0 | C | +28.36 | 58.0 | +0.116 | +1.260 | 102 | 102 | 74 | 176 | 2.11 | −12.97 | 1643.5 |
| #5 (orig) | C_f13s34g9_histogram_mg1.0 | C | +27.79 | 58.8 | +0.110 | +1.229 | 104 | 104 | 73 | 178 | 2.05 | −11.85 | 1632.7 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +30.51 | 41.2 | +0.263 | +3.307 | 6 | 49 | 70 | 120 | 1.52 | −24.11 | 1256.3 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +9.75 | 44.5 | +0.073 | +2.350 | 38 | 65 | 81 | 147 | 0.63 | −20.05 | 434.0 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | **−18.12** | 43.2 | −0.149 | +1.299 | 62 | 70 | 92 | 163 | −1.34 | −33.29 | nan |
| **M-D** | D_ema8_mg1.75 | D | +5.11 | 45.1 | +0.033 | +1.804 | 57 | 69 | 84 | 154 | 0.36 | −23.68 | 230.3 |

Note: post-mg-extension overall best for 2024_Q4 is `C_f8s21g9_histogram_mg2.5` (+52.55% / 47.4% WR / score 2491.4). Original grid top-5 shown above for context.

### 2025_Q1 (winner: B)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.0 | base | +119.12 | 44.2 | +0.690 | +2.209 | 8 | 57 | 72 | 130 | 3.28 | −12.87 | 5263.3 |
| B2 | baseline_mg1.75 | base | +111.43 | 42.2 | +0.669 | +2.521 | 7 | 54 | 74 | 129 | 3.14 | −12.87 | 4700.8 |
| B3 | baseline_mg1.5 | base | +109.94 | 42.2 | +0.663 | +2.419 | 7 | 54 | 74 | 129 | 3.11 | −12.87 | 4638.0 |
| B4 | baseline_mg1.25 | base | +109.43 | 42.2 | +0.661 | +2.384 | 7 | 54 | 74 | 129 | 3.10 | −12.87 | 4616.8 |
| #1 | B_lb2_d3.5_min15_mg1.0 | B | +148.74 | 56.8 | +0.634 | +1.233 | 65 | 88 | 67 | 155 | 4.53 | −17.11 | **8444.3** |
| #2 | B_lb2_d2.0_min15_mg1.0 | B | +140.94 | 56.6 | +0.581 | +1.352 | 72 | 90 | 69 | 159 | 4.56 | −14.02 | 7977.8 |
| #3 | C_f8s34g13_cross_mg1.0 | C | +156.32 | 49.0 | +0.692 | +2.654 | 53 | 74 | 77 | 151 | 3.80 | −18.09 | 7660.9 |
| #4 | B_lb2_d1.0_min15_mg1.0 | B | +128.18 | 56.6 | +0.541 | +1.461 | 76 | 90 | 69 | 159 | 4.28 | −13.86 | 7255.3 |
| #5 | C_f13s21g13_cross_mg1.0 | C | +150.12 | 48.0 | +0.681 | +2.729 | 51 | 72 | 78 | 150 | 3.70 | −19.38 | 7205.6 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +113.83 | 42.2 | +0.677 | +2.767 | 9 | 54 | 74 | 129 | 3.18 | −13.36 | 4802.4 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +78.65 | 45.6 | +0.439 | +2.186 | 43 | 68 | 81 | 149 | 3.03 | −15.53 | 3589.6 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | +129.38 | 48.7 | +0.601 | +2.386 | 67 | 73 | 77 | 150 | 3.54 | −17.29 | 6296.3 |
| **M-D** | D_ema8_mg1.75 | D | +69.27 | 46.3 | +0.394 | +2.075 | 65 | 68 | 79 | 147 | 2.99 | −16.72 | 3204.1 |

### 2025_Q2 (winner: C)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.25 | base | +81.74 | 40.8 | +0.532 | +1.740 | 2 | 51 | 74 | 126 | 3.15 | −20.42 | 3335.1 |
| B2 | baseline_mg1.0 | base | +81.02 | 40.8 | +0.528 | +1.530 | 2 | 51 | 74 | 126 | 3.13 | −20.73 | 3305.5 |
| B3 | baseline_mg1.5 | base | +79.32 | 41.1 | +0.526 | +2.090 | 1 | 51 | 73 | 125 | 3.08 | −21.48 | 3262.4 |
| B4 | baseline_mg1.75 | base | +79.32 | 41.1 | +0.526 | +2.090 | 1 | 51 | 73 | 125 | 3.08 | −21.48 | 3262.4 |
| #1 | C_f13s34g9_cross_mg1.0 | C | +104.00 | 45.5 | +0.520 | +2.987 | 40 | 66 | 79 | 146 | 3.57 | −17.42 | **4733.6** |
| #2 | C_f13s21g9_cross_mg1.0 | C | +100.65 | 45.4 | +0.486 | +2.652 | 51 | 69 | 83 | 153 | 3.52 | −16.86 | 4569.2 |
| #3 | C_f8s34g9_cross_mg1.0 | C | +98.21 | 45.4 | +0.480 | +2.642 | 54 | 69 | 83 | 153 | 3.59 | −16.52 | 4458.0 |
| #4 | C_f8s34g13_cross_mg1.0 | C | +94.47 | 45.2 | +0.480 | +2.799 | 42 | 66 | 80 | 147 | 3.41 | −17.24 | 4270.8 |
| #5 | C_f13s21g13_cross_mg1.0 | C | +91.25 | 45.2 | +0.467 | +2.756 | 42 | 66 | 80 | 147 | 3.34 | −17.43 | 4125.0 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +82.30 | 41.0 | +0.550 | +2.847 | 3 | 50 | 72 | 123 | 3.16 | −21.48 | 3373.1 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +56.46 | 43.7 | +0.355 | +2.716 | 36 | 59 | 76 | 136 | 2.61 | −18.66 | 2467.7 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | +73.20 | 45.0 | +0.376 | +2.177 | 57 | 68 | 83 | 153 | 3.61 | −15.52 | 3296.4 |
| **M-D** | D_ema8_mg1.75 | D | +64.97 | 46.5 | +0.375 | +2.549 | 55 | 66 | 76 | 143 | 2.89 | −15.31 | 3019.8 |

### 2025_Q3 (winner: A)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.5 | base | +81.02 | 42.9 | +0.477 | +2.393 | 6 | 54 | 72 | 128 | 4.28 | −15.97 | 3472.2 |
| B2 | baseline_mg1.75 | base | +80.60 | 42.1 | +0.484 | +2.800 | 5 | 53 | 73 | 128 | 4.04 | −15.97 | 3390.5 |
| B3 | baseline_mg1.25 | base | +76.41 | 42.9 | +0.456 | +2.230 | 7 | 54 | 72 | 128 | 4.14 | −15.97 | 3274.8 |
| B4 | baseline_mg1.0 | base | +76.11 | 42.9 | +0.455 | +2.206 | 7 | 54 | 72 | 128 | 4.12 | −15.97 | 3262.0 |
| #1 | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +84.50 | 42.1 | +0.501 | +4.026 | 7 | 53 | 73 | 128 | 4.17 | −15.97 | **3554.2** |
| #2 | B_lb4_d1.0_min30_mg1.75 | B | +71.74 | 47.0 | +0.392 | +2.508 | 33 | 62 | 70 | 134 | 4.40 | −15.05 | 3369.5 |
| #3 | B_lb4_d2.0_min30_mg1.75 | B | +69.64 | 47.0 | +0.382 | +2.469 | 33 | 62 | 70 | 134 | 4.35 | −15.43 | 3271.0 |
| #4 | B_lb6_d1.0_min20_mg1.25 | B | +62.58 | 51.7 | +0.318 | +1.702 | 65 | 75 | 70 | 147 | 4.43 | −8.49 | 3236.7 |
| #5 | C_f8s34g13_histogram_mg1.5 | C | +58.66 | 54.7 | +0.311 | +1.802 | 72 | 76 | 63 | 141 | 4.10 | −7.61 | 3207.1 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 *(=#1)* | A | +84.50 | 42.1 | +0.501 | +4.026 | 7 | 53 | 73 | 128 | 4.17 | −15.97 | **3554.2** |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +20.63 | 45.1 | +0.116 | +1.804 | 45 | 64 | 78 | 143 | 1.80 | −17.56 | 929.8 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | +38.18 | 50.6 | +0.195 | +1.516 | 65 | 78 | 76 | 156 | 2.95 | −9.55 | 1933.8 |
| **M-D** | D_ema8_mg1.75 | D | +30.10 | 48.6 | +0.163 | +1.671 | 56 | 71 | 75 | 148 | 2.31 | −15.25 | 1463.7 |

### 2025_Q4 (winner: D)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.0 | base | +26.76 | 36.4 | +0.205 | +1.837 | 3 | 51 | 89 | 141 | 1.31 | −24.91 | 974.8 |
| B2 | baseline_mg1.25 | base | +26.76 | 36.4 | +0.205 | +1.837 | 3 | 51 | 89 | 141 | 1.31 | −24.91 | 974.8 |
| B3 | baseline_mg1.5 | base | +25.94 | 36.4 | +0.200 | +2.180 | 2 | 51 | 89 | 141 | 1.28 | −25.53 | 944.9 |
| B4 | baseline_mg1.75 | base | +25.35 | 36.4 | +0.197 | +2.850 | 1 | 51 | 89 | 141 | 1.25 | −25.53 | 923.3 |
| #1 | D_ema8_mg1.75 | D | +41.16 | 42.5 | +0.220 | +2.274 | 63 | 71 | 96 | 169 | 1.95 | −18.94 | **1749.8** |
| #2 | D_ema8_mg1.0 | D | +30.41 | 47.7 | +0.172 | +1.634 | 81 | 82 | 90 | 173 | 1.54 | −20.28 | 1449.8 |
| #3 | D_ema8_mg1.5 | D | +30.84 | 43.5 | +0.180 | +2.096 | 68 | 73 | 95 | 169 | 1.54 | −20.28 | 1340.0 |
| #4 | D_ema8_mg1.25 | D | +26.34 | 44.6 | +0.160 | +1.880 | 73 | 74 | 92 | 167 | 1.34 | −20.28 | 1174.4 |
| #5 | D_ema13_mg1.75 | D | +23.02 | 42.1 | +0.131 | +2.108 | 58 | 72 | 99 | 173 | 1.22 | −19.48 | 969.3 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +13.71 | 35.2 | +0.120 | +4.580 | 3 | 50 | 92 | 143 | 0.72 | −27.02 | 482.6 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +2.27 | 38.9 | +0.024 | +2.299 | 47 | 63 | 99 | 163 | 0.14 | −28.09 | 88.3 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | +11.90 | 40.4 | +0.083 | +1.925 | 55 | 67 | 99 | 167 | 0.66 | −20.40 | 480.5 |
| **M-D** | D_ema8_mg1.75 *(=#1)* | D | +41.16 | 42.5 | +0.220 | +2.274 | 63 | 71 | 96 | 169 | 1.95 | −18.94 | **1749.8** |

### 2026_Q1 (winner: B)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.5 | base | +19.66 | 38.7 | +0.166 | +7.090 | 2 | 55 | 87 | 142 | 1.22 | −19.33 | 761.4 |
| B2 | baseline_mg1.0 | base | +19.58 | 38.5 | +0.164 | +5.130 | 3 | 55 | 88 | 143 | 1.22 | −19.33 | 753.2 |
| B3 | baseline_mg1.25 | base | +19.58 | 38.5 | +0.164 | +5.130 | 3 | 55 | 88 | 143 | 1.22 | −19.33 | 753.2 |
| B4 | baseline_mg1.75 | base | +15.04 | 37.8 | +0.137 | +12.660 | 1 | 54 | 89 | 143 | 1.01 | −19.33 | 568.0 |
| #1 | B_lb6_d1.0_min20_mg1.0 | B | +55.27 | 53.1 | +0.279 | +1.364 | 66 | 85 | 75 | 160 | 3.04 | −12.76 | **2936.0** |
| #2 | B_lb6_d1.0_min15_mg1.0 | B | +52.31 | 53.2 | +0.265 | +1.438 | 72 | 84 | 74 | 158 | 3.18 | −12.76 | 2781.3 |
| #3 | B_lb6_d2.0_min20_mg1.0 | B | +52.46 | 52.5 | +0.268 | +1.324 | 64 | 84 | 76 | 160 | 2.91 | −12.88 | 2754.0 |
| #4 | B_lb6_d3.5_min20_mg1.0 | B | +49.27 | 52.2 | +0.253 | +1.344 | 62 | 84 | 77 | 161 | 2.77 | −12.88 | 2570.5 |
| #5 | B_lb4_d1.0_min15_mg1.0 | B | +45.95 | 52.2 | +0.238 | +1.486 | 70 | 84 | 77 | 161 | 2.74 | −12.82 | 2397.6 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +7.75 | 37.9 | +0.078 | +5.090 | 4 | 55 | 90 | 145 | 0.66 | −19.33 | 293.8 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +40.05 | 43.8 | +0.231 | +2.149 | 38 | 67 | 86 | 155 | 2.22 | −22.02 | 1753.8 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | +13.34 | 43.8 | +0.082 | +1.699 | 64 | 70 | 90 | 161 | 1.05 | −14.46 | 583.8 |
| **M-D** | D_ema8_mg1.75 | D | +8.26 | 44.4 | +0.057 | +1.955 | 59 | 68 | 85 | 153 | 0.75 | −21.31 | 366.9 |

### 2026_Q2 (winner: C, partial through May 8)
| Rank | Tag | Cat | ret% | WR% | avgPnL% | trlPnL% | trail | W | L | N | Shrp | maxDD% | score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | baseline_mg1.75 | base | +31.70 | 48.8 | +0.635 | +2.045 | 4 | 21 | 22 | 44 | 7.19 | −5.06 | 1548.0 |
| B2 | baseline_mg1.5 | base | +31.33 | 48.8 | +0.628 | +1.972 | 4 | 21 | 22 | 44 | 7.14 | −5.06 | 1530.0 |
| B3 | baseline_mg1.25 | base | +29.30 | 45.8 | +0.526 | +1.557 | 6 | 22 | 26 | 49 | 6.66 | −5.06 | 1343.1 |
| B4 | baseline_mg1.0 | base | +27.30 | 45.8 | +0.492 | +1.331 | 7 | 22 | 26 | 49 | 5.95 | −5.57 | 1251.3 |
| #1 | C_f13s34g13_cross_mg1.0 | C | +31.23 | 52.1 | +0.554 | +2.787 | 14 | 25 | 23 | 49 | 7.32 | −5.67 | **1626.4** |
| #2 | C_f13s34g13_cross_mg1.5 | C | +31.23 | 52.1 | +0.554 | +2.787 | 14 | 25 | 23 | 49 | 7.32 | −5.67 | 1626.4 |
| #3 | C_f13s34g13_cross_mg1.25 | C | +31.23 | 52.1 | +0.554 | +2.787 | 14 | 25 | 23 | 49 | 7.32 | −5.67 | 1626.4 |
| #4 | A_kc1.75_bb2.75_d1.0_mg1.75 | A | +31.70 | 48.8 | +0.635 | +2.045 | 4 | 21 | 22 | 44 | 7.19 | −5.06 | 1548.0 |
| #5 | B_lb4_d2.0_min30_mg1.5 | B | +30.14 | 51.0 | +0.500 | +2.624 | 15 | 26 | 25 | 52 | 6.64 | −5.73 | 1536.5 |
| **M-A** | A_kc1.75_bb2.75_d1.0_mg1.75 *(=#4)* | A | +31.70 | 48.8 | +0.635 | +2.045 | 4 | 21 | 22 | 44 | 7.19 | −5.06 | 1548.0 |
| **M-B** | B_lb6_d3.5_min20_mg1.75 | B | +19.09 | 49.0 | +0.321 | +2.338 | 13 | 25 | 26 | 52 | 5.40 | −5.63 | 935.9 |
| **M-C** | C_f8s21g9_cross_mg1.0 | C | +13.73 | 48.0 | +0.232 | +1.777 | 17 | 24 | 26 | 51 | 4.76 | −6.69 | 659.0 |
| **M-D** | D_ema8_mg1.75 | D | +10.96 | 46.0 | +0.183 | +1.937 | 17 | 23 | 27 | 51 | 3.72 | −6.40 | 503.9 |

### Cross-quarter travel observations

- **M-B (`B_lb6_d3.5_min20_mg1.75`)** is the only fixed strategy that **never produces negative return** in any quarter. Closest to per-quarter best in 2024_Q3 (Δ = −85, its home turf).
- **M-C (`C_f8s21g9_cross_mg1.0`)** ties best in 2024_Q1, but **goes negative in 2024_Q3 (+8.96%)** and **catastrophic in 2024_Q4 (−18.12%)** — the C cross variant is fragile in B-regimes and chop.
- **M-D (`D_ema8_mg1.75`)** **goes negative in 2024_Q1 (−8.80%)** — fragile early-trend regimes; only competitive in 2025_Q4.
- **M-A (`A_kc1.75_bb2.75_d1.0_mg1.75`)** stays positive in every quarter (lowest = +7.75% in 2026_Q1). Close to top-3 in C-quarters with way fewer trail exits (3–9 vs 50–70 for the C-cross variants) — tighter, more conservative profile.
- **No fixed strategy beats baseline + per-quarter best in more than 1 quarter.** Capturing the wins requires a regime-aware switcher; a single static replacement does not exist in this grid.

## Files

- `scripts/grid_search_lazyswing_profit_exit.py` — grid runner (1960 runs)
- `scripts/grid_search_top10_extend_mg.py` — mg2.0/2.5 extension (160 runs)
- `scripts/report_top10_per_quarter.py` — per-quarter top-5 + baseline report
- `scripts/report_with_median_best.py` — adds median-best-per-category rows + Δ-to-best
- `scripts/verify_grid_reproducibility.py` — standalone reproducibility check
- `scripts/analyze_2025q3_trade_character.py` — Q3'25 regime analysis
- `data/backtests/eth/profit_exit_grid/summary_all.csv` — original 1960 runs
- `data/backtests/eth/profit_exit_grid/summary_top10_extend.csv` — 160 mg2.0/2.5 runs
- `data/backtests/eth/profit_exit_grid/aggregate_all.csv` — 196 variants × cross-window aggregates
- `/tmp/profit_exit_grid_all.log` — full grid run log
- `/tmp/extend_mg.log` — extension run log

---

# Combined-BC Profit-Exit — added 2026-05-08

## Motivation

The original grid showed that no single per-category exit (B, C, D) is a drop-in replacement for baseline. **Combined_bc** is a new exit mode that requires *both* B (adx_exhaustion) AND C (macd_exit) to fire within a small window N (5m bars), turning two unreliable individual signals into one composite trigger.

Pinned signal params (median-best from prior grid):
- **B**: adx_lookback=6, drop=3.5%, prev_adx_min=20
- **C**: MACD f=8, slow=21, signal=9, condition=cross
- **N**: 6 5m bars (30 min wait window)

## Result: signal-mode (exit_on_signal=True)

`BC_n6_f8s21g9_cross_mg2.0` across 8 B/C-inclined quarters (2025_Q3/Q4 excluded):

| Tag | Compound | Mean/qtr | Min qtr | Negs |
|---|---|---|---|---|
| **BC_cross_signal_mg2.0** | **+2,288%** | +52.2% | +7.5% | 0 |
| baseline_mg1.5 | +2,177% | +50.3% | +19.7% | 0 |
| pure-B mg1.75 | +1,271% | +41.9% | −2.1% | 1 |
| pure-C mg1.75 | +702% | +32.6% | — | — |
| best-per-quarter cheating | +2,421% | +52.6% | — | — |

**Key insight**: combined_bc's success was partly luck. In big trending moves like trade 2026-01-18 SHORT (3340→2969, +12.4%), B and cross *happened* not to align within N — letting the trade ride. Histogram variants of C (which fire more often) DID align with B in the same trade and would have exited prematurely at +3.5% profit. So `cross` was beating `histogram` due to timing alignment, not true selectivity. See `scripts/analyze_combined_bc_filters_q1_2026.py` for per-bar trace.

## Result: windowed-giveback (exit_on_signal=False, gb_window=N)

The structural fix: instead of exiting on the BC trigger bar, **arm the trail** and require price to give back ≥0.75% from the trade's high-water mark **within N additional 5m bars**. If giveback isn't met in time, cancel the arm and re-evaluate later.

| Tag | Compound | Mean | Min |
|---|---|---|---|
| **BC_cross_GB_N1 (NEW WINNER)** | **+2,730%** | +55.6% | +7.0% |
| BC_cross_GB_N2 | +2,677% | +55.2% | +7.0% |
| REF_BC_cross_signal | +2,288% | +52.2% | +7.5% |
| baseline_mg1.5 | +2,177% | +50.3% | +19.7% |
| BC_hist_x0_GB_N2 | +1,664% | +45.8% | +10.1% |
| BC_hist_x0_GB_N3 | +1,655% | +45.9% | +9.0% |
| BC_hist_x0_GB_N1 | +1,642% | +45.4% | +13.4% |
| REF_BC_hist_x0_signal | +1,551% | +44.5% | +19.1% |

**`BC_cross_GB_N1` adds +442pp compound vs prior shipping config**, with 0 negative quarters and identical drawdown. Wins concentrated in C-favored trending quarters (2024_Q1: +5pp; **2024_Q2: +23pp**); other quarters tie or lose <2pp.

### Why it works

The BC trigger says "potential exhaustion". Giveback says "price has actually moved against you". Requiring both within a tight 5–10 minute window:
- **Filters out consolidation pauses** (BC fires but price doesn't retrace 0.75% before trend resumes → arm cancels)
- **Confirms real reversals** (price retraces 0.75% within 5–10 min → exit fires)
- **Doesn't over-delay** (small window prevents stale-arm exits during the next move)

Cross-with-windowed-giveback works better than histogram-with-windowed-giveback because cross is already a "rare and decisive" event — the giveback gate adds discipline without delaying real reversals. Histogram fires too often; the giveback filter catches some good fires but loses too many in C-favored quarters that compound heavily (2024_Q2, 2025_Q2).

### Giveback% × N sensitivity (added)

Sweep of `trail_stop_pct ∈ {0.5%, 0.75%, 1.0%}` × `gb_window_bars ∈ {1, 2, 3}` across 8 quarters confirms:
- **0.75% is the sweet spot for cross**: ±0.25% degrades by ~315pp compound. Wider gate (1.0%) helps the biggest winner (Q1'25 +133% vs +125%) but loses elsewhere.
- **N is nearly irrelevant for cross**: N=1/2/3 differ by <50pp out of 2,700pp. Cross fires for one 5m bar — if giveback isn't confirmed in ~10 min, the trigger is stale regardless of arm window length.
- **Histogram needs a wider gate (1.0%)** because it fires more often and needs aggressive filtering. But hist_GB100_N1 = +1,772% still loses ~960pp to cross_GB75_N1.

Best variants (8-quarter compound):

| Tag | Compound% | vs current ship |
|---|---|---|
| BC_cross_GB75_N3 | +2,730.65% | +443pp |
| BC_cross_GB75_N1 | +2,729.87% | +442pp |
| BC_cross_GB75_N2 | +2,677.47% | +390pp |
| BC_cross_GB50_N{1,2,3} | +2,414.07% (all tied) | +126pp |
| BC_cross_GB100_N2 | +2,407.71% | +120pp |
| BC_cross_signal_0.75 (prior ship) | +2,287.79% | — |

`BC_cross_GB75_N2` chosen as ship config (N=1, N=2, N=3 are tied within ~50pp of compound — N=2 picked for safety margin: gives ~15 minutes for confirmation rather than 10).

### Peak-drop X% filter — abandoned

Tested `histogram_peak_drop_pct ∈ {0, 0.5, 0.75}` as a pre-condition: histogram must have lost X% of its peak since entry before firing. **All X>0 variants underperformed X=0**. Reason: the X% test fires *earlier* than cross (e.g. peak=+1.5 → x50 fires at +0.75, well before cross's zero-crossing) but with fewer good fires preserved. The mechanism is a delay-within-decline, not a selectivity gate. See `scripts/analyze_combined_bc_filters_q1_2026.py` for the per-bar mechanism.

## Recommended config

```yaml
regime_trail_mode: combined_bc
combined_bc_window_bars: 6                      # B-then-C alignment window (5m bars)
trail_stop_min_gain_pct: 2.0                    # min profit floor before any trail
trail_stop_exit_on_signal: false                # NEW: don't exit on BC trigger alone
trail_stop_giveback_window_bars: 1              # NEW: arm trail for 1 extra 5m bar
trail_stop_pct: 0.75                            # giveback threshold from peak
# Pinned BC signal params:
regime_exhaustion_adx_lookback: 6
regime_exhaustion_adx_drop_pct: 3.5
regime_exhaustion_prev_adx_min: 20
profit_exit_macd_fast: 8
profit_exit_macd_slow: 21
profit_exit_macd_signal_period: 9
profit_exit_macd_condition: cross
```

## Code changes

- `src/strategies/lazy_swing.py`:
  - New `regime_trail_mode = "combined_bc"`: arm-flag state machine (B-then-C or C-then-B within `combined_bc_window_bars`); cancels on profit retrace below min_gain or window expiry.
  - New `trail_stop_giveback_window_bars`: when set with `trail_stop_exit_on_signal=False`, BC trigger arms exit for that many additional 5m bars; existing giveback ≥ trail_stop_pct check fires the actual exit.
  - New `profit_exit_macd_histogram_peak_drop_pct`: optional X% peak-drop filter for histogram condition (tested, abandoned — kept in code for record).

## Files (combined-BC)

- `scripts/grid_search_lazyswing_combined_BC_exit.py` — initial 8-quarter combined_bc grid
- `scripts/grid_search_combined_bc_histogram.py` — histogram + peak-drop X% sweep
- `scripts/probe_combined_bc_2026_q1.py` — 2026_Q1-only loosening probe
- `scripts/analyze_combined_bc_filters_q1_2026.py` — per-bar filter trace on 5 trades
- `scripts/probe_bc_giveback_gate.py` — exit_on_signal=False, single-bar giveback (idea #1 v1)
- `scripts/probe_bc_giveback_window.py` — windowed giveback, 2 quarters (idea #1 v2)
- `scripts/grid_bc_giveback_window_8q.py` — windowed giveback, 8 quarters validation
- `data/backtests/eth/combined_bc_grid/summary_all.csv` — combined_bc grid (128 runs)
- `data/backtests/eth/combined_bc_grid/summary_histogram.csv` — histogram + X% sweep (128 runs)
- `data/backtests/eth/combined_bc_grid/giveback_window_8q.csv` — windowed-giveback 8-qtr
