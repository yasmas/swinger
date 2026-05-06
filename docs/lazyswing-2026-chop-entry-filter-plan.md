# LazySwing 2026 Chop-Entry Filter Plan

## Findings That Triggered This Plan

The `2026-03-01` to `2026-05-06` audit showed the main leak is not the new take-profit logic. The HOF strategy returned `+15.42%`, but the average PnL per exit was only `+0.154%`, with `97` closed trades, `42.27%` win rate, and median trade PnL of `-0.60%`.

The biggest problem bucket is trades that never became eligible for take-profit:

- `53 / 97` trades never reached `+1.5%` open profit on a 5m close.
- `51` truly never reached the threshold; `2` only touched it intrabar by high/low wick.
- This bucket produced about `-74.30%` total per-trade PnL.
- Average PnL in this bucket was about `-1.40%`.
- Average close-based MFE was only about `+0.52%`.
- Average MAE was about `-1.69%`.

This means the take-profit system could not help most of the damage. These trades were bad/choppy entries that failed almost immediately.

The HOLD mode did not solve this because HOLD only applies when a SuperTrend flip is rejected by the flip-vol ratio gate. Most bad trades were not rejected flips; they were normal allowed flips. The chop came through the front door.

Breakdown of the bad bucket:

| Pattern | Trades | Avg PnL | Total PnL |
|---|---:|---:|---:|
| Normal ST flip bullish/bearish entries | 46 | about -1.39% | about -63.98% |
| Fast-exit reentries | 7 | -1.47% | -10.32% |
| Immediate flip entries within 5m of prior exit | 18 | -1.77% | -31.79% |
| Held-flip safety exits | 5 | -2.13% | -10.64% |

Simple existing knobs did not fix it:

| Variant | 2026-03-01 Return | Verdict |
|---|---:|---|
| Current HOF | +15.42% | Baseline |
| Entry delay 1h | +15.97% | Tiny help only |
| Entry delay 2h | +6.74% | Bad |
| Entry persistence 2 bars | -3.57% | Bad |
| Entry persistence 4 bars | +2.61% | Bad |
| Fast reentry cooldown 8 | +13.43% | Worse |
| Fast reentry cooldown 12 | +11.95% | Worse |
| Stricter flip-vol gate | about -16.7% | Very bad |

The conclusion: this is a stateful chop-entry problem. We need targeted controls for fast-exit reentries, weak HOLD cases, and ST flips that lack momentum confirmation.

## Implementation Checklist

- [ ] Create `docs/lazyswing-2026-chop-entry-filter-plan.md` with this plan and keep it updated after each stage.
- [ ] Add a reusable `momentum_confirmation` helper in `LazySwingStrategy`.
- [ ] Support confirmation modes:
  - `hmacd`: long requires HMACD histogram > threshold; short requires < negative threshold.
  - `adx_er`: requires ADX >= threshold, ER >= threshold, and ADX delta not too negative.
  - `composite`: requires HMACD direction plus either ADX/ER quality or slow-vol ratio confirmation.
- [ ] Add config params, all default disabled:
  - `flip_momentum_confirm_enabled: false`
  - `flip_momentum_confirm_mode: "hmacd" | "adx_er" | "composite"`
  - `flip_momentum_confirm_hmacd_min_abs: 0.0`
  - `flip_momentum_confirm_adx_min: 20.0`
  - `flip_momentum_confirm_er_min: 0.20`
  - `flip_momentum_confirm_adx_delta_min: -2.0`
  - `flip_momentum_confirm_vol_ratio_max: 1.25`
- [ ] Apply momentum confirmation to normal ST flip reversals.
- [ ] If currently long and ST flips bearish but short confirmation fails: close long, stay flat, set `_prev_st_bullish` to current ST, and do not arm `_pending_short`.
- [ ] If currently short and ST flips bullish but long confirmation fails: cover short, stay flat, set `_prev_st_bullish` to current ST, and do not arm `_pending_long`.
- [ ] If flat and a fresh ST flip appears but confirmation fails: stay flat and mark the flip consumed so it does not enter one bar later from the same flip.
- [ ] Emit explicit reasons:
  - `st_flip_momentum_reject_flat`
  - `entry_momentum_rejected`
  - `pending_flip_momentum_rejected`

- [ ] Add fast-exit reentry quality gates.
- [ ] Add config params, all default preserving current behavior:
  - `fast_exit_reentry_quality_enabled: false`
  - `fast_exit_reentry_min_prior_mfe_pct: 1.5`
  - `fast_exit_reentry_block_after_loss: false`
  - `fast_exit_reentry_require_momentum_confirm: false`
  - `fast_exit_reentry_standdown_bars_after_weak_trade: 0`
- [ ] Track each fast-exited trade's close-based MFE and realized PnL.
- [ ] Block same-side fast-exit reentry if prior trade had close-MFE below threshold.
- [ ] Optionally block same-side reentry after any losing fast-exit.
- [ ] Optionally require the same momentum confirmation helper before fast-exit reentry.
- [ ] Emit reasons:
  - `fast_exit_reentry_blocked_weak_mfe`
  - `fast_exit_reentry_blocked_prior_loss`
  - `fast_exit_reentry_momentum_rejected`

- [ ] Add profit-aware HOLD behavior for vol-ratio-rejected flips.
- [ ] Add config params:
  - `held_flip_profit_aware_enabled: false`
  - `held_flip_min_profit_to_hold_pct: 0.0`
  - `held_flip_flat_if_loss: false`
  - `held_flip_flat_if_mfe_below_pct: 1.5`
- [ ] When a flip is rejected by the flip-vol gate, only HOLD if the current trade has enough realized/unrealized cushion.
- [ ] If not enough cushion, close current trade and stay flat instead of arming the held-flip safety stop.
- [ ] Emit reason `st_flip_ratio_rejected_flat_weak_trade`.
- [ ] Keep existing HOLD behavior unchanged when the feature is disabled.

## Grid Search Plan

- [ ] Create `scripts/grid_search_lazyswing_chop_entry_filters.py`.
- [ ] Use subprocess or capped worker scheduling with max `4` concurrent jobs, not `ProcessPoolExecutor`, to avoid the macOS sandbox semaphore issue.
- [ ] Primary optimization window: `2026-03-01` to `2026-05-06`.
- [ ] Robustness windows:
  - `2026-01-01` to `2026-05-06`
  - `2024 H2`
  - `2025`
  - `2024 H1`
- [ ] Always compare against:
  - HOF current `stretch_tighter_175_275`
  - relaxed KC1.5 / BB2.5 take-profit variant
  - baseline without new chop filters
- [ ] Report metrics:
  - total return
  - compound return across robustness windows
  - win rate
  - avg PnL per exit
  - max drawdown
  - trade count
  - count and total PnL of `not_eligible_for_takeprofit` trades
  - fast-exit reentry count and PnL
  - held-flip safety count and PnL
  - missed big-winner count, defined as trades skipped by a filter where baseline later made >= `+3%`

- [ ] Stage 1 grid: isolate each idea.
- [ ] Fast-exit reentry quality grid:
  - `min_prior_mfe_pct`: `[0.75, 1.0, 1.5, 2.0]`
  - `block_after_loss`: `[false, true]`
  - `require_momentum_confirm`: `[false, true]`
- [ ] Profit-aware HOLD grid:
  - `min_profit_to_hold_pct`: `[0.0, 0.5, 1.0]`
  - `flat_if_loss`: `[false, true]`
  - `flat_if_mfe_below_pct`: `[0.75, 1.5, 2.0]`
- [ ] Momentum confirmation grid:
  - modes: `[hmacd, adx_er, composite]`
  - HMACD abs threshold: `[0.0, 1.0, 2.5, 5.0]`
  - ADX min: `[20, 25, 30]`
  - ER min: `[0.15, 0.20, 0.25, 0.30]`
- [ ] Stage 2 grid: combine the top 2-3 candidates from each idea.
- [ ] Stage 3 robustness: run top 5 combined variants across all windows.
- [ ] Winner rule:
  - Must improve `2026-03-01` return and avg PnL.
  - Must reduce `not_eligible_for_takeprofit` total loss by at least `25%`.
  - Must not reduce full 2025 return by more than `10%` relative to HOF unless compound return improves.
  - Must not increase max drawdown on the `2026-03-01` slice.

## Tests And Reports

- [ ] Add unit tests for momentum confirmation helper:
  - long pass/fail
  - short pass/fail
  - warmup/NaN defaults to reject only when feature enabled
- [ ] Add strategy tests for:
  - ST flip with failed momentum confirmation exits flat and does not reverse.
  - Fast-exit reentry blocked after weak prior trade.
  - Held-flip rejected weak trade exits flat instead of arming safety stop.
  - All new params disabled preserves current behavior.
- [ ] Extend the March trade audit to classify:
  - blocked would-have-entered trades
  - avoided losers
  - missed winners
  - net effect by filter family
- [ ] Save outputs under `reports/lazyswing-2026-chop-entry-filters/`.
- [ ] Update `docs/lazyswing-2026-chop-entry-filter-plan.md` after each stage with checked boxes, result tables, and the current recommendation.

## Assumptions And Defaults

- We will not change the HOF YAML until a variant passes robustness.
- New behavior must be opt-in and default to current behavior.
- Momentum confirmation applies to entering the new direction, not exiting the old trade.
- If confirmation fails on a reversal, we close the existing trade and stay flat.
- The first implementation should reuse existing indicators: HMACD, ADX, ER, slow-vol ratio. No CMF.
- Success is not just higher return; success is specifically reducing the bad/choppy `not_eligible_for_takeprofit` loss bucket without destroying 2025 trend performance.
