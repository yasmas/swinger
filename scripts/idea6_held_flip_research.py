#!/usr/bin/env python3
"""Idea #6 research — held-flip vs flipped EV across 8 quarters.

Doc evidence is from a 10-week 2026 sample: 28 rejected flips, sum PnL
−2.04% (held) vs +2.68% (if flipped). Re-establish across all 8 quarters.

For each `st_flip_ratio_rejected_hold` event in the ship config's trade log:
  - "Held" outcome: actual remaining PnL from rejection bar to actual exit.
  - "Flipped" outcome (hypothetical): open opposite position at rejection
    price, exit at the next same-direction entry (ST returned to original).

Bucket by quarter to detect regime dependence (doc warns this is one sample;
8q test may show held-flip is net positive in some quarters).
"""
from __future__ import annotations

import multiprocessing as mp
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import Config  # noqa: E402
from controller import Controller  # noqa: E402
from trade_log import TradeLogReader  # noqa: E402

WINDOWS = {
    "2024_Q1": {"start": "2024-01-01", "end": "2024-04-01"},
    "2024_Q2": {"start": "2024-04-01", "end": "2024-07-01"},
    "2024_Q3": {"start": "2024-07-01", "end": "2024-10-01"},
    "2024_Q4": {"start": "2024-10-01", "end": "2025-01-01"},
    "2025_Q1": {"start": "2025-01-01", "end": "2025-04-01"},
    "2025_Q2": {"start": "2025-04-01", "end": "2025-07-01"},
    "2026_Q1": {"start": "2026-01-01", "end": "2026-04-01"},
    "2026_Q2": {"start": "2026-04-01", "end": "2026-05-08"},
}
SLICE_DIR = REPO / "data" / "backtests" / "eth" / "profit_exit_grid_slices"

BASE_PARAMS: dict = {
    "resample_interval": "30min",
    "supertrend_atr_period": 25, "supertrend_multiplier": 1.75,
    "adaptive_st_vol_period": 24, "adaptive_st_vol_long_period": 336,
    "adaptive_st_enter_ratio_threshold": 1.0,
    "adaptive_st_exit_ratio_threshold": 0.85,
    "adaptive_st_min_high_bars": 48,
    "flip_vol_ratio_enabled": True,
    "flip_vol_ratio_short_period": 4, "flip_vol_ratio_long_period": 336,
    "flip_vol_ratio_regime_mode": "squared",
    "flip_vol_ratio_regime_low_min": 0.7, "flip_vol_ratio_regime_high_min": 1.0,
    "flip_vol_ratio_regime_low_stop_pct": 1.0,
    "flip_vol_ratio_regime_high_stop_pct": 2.5,
    "flip_vol_ratio_regime_power": 1.5,
    "hmacd_fast": 24, "hmacd_slow": 51, "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
    "fast_exit_enabled": True, "fast_exit_cooldown_bars": 4,
    "fast_exit_rvol_short_period": 24, "fast_exit_rvol_long_period": 2016,
    "fast_exit_rvol_low_min": 1.1, "fast_exit_rvol_high_min": 1.3,
    "fast_exit_reentry_confirm": True,
    "flat_realign_hourly_closes": 0,
    "regime_trail_enabled": True,
    "regime_momentum_adx_period": 14, "regime_momentum_adx_min": 40.0,
    "regime_momentum_er_period": 24, "regime_momentum_er_min": 0.40,
    "regime_momentum_adx_delta_bars": 2, "regime_momentum_adx_delta_min": 1.0,
    "regime_momentum_vol_period": 24, "regime_momentum_vol_long_period": 336,
    "regime_momentum_vol_ratio_max": 1.0,
    "trail_stop_pct": 0.75, "trail_stop_atr_multiple": 0.75,
    "trail_stop_cooldown_bars": 0, "trail_stop_reentry_enabled": False,
}

SHIP_PARAMS: dict = {
    "regime_trail_mode": "combined_bc",
    "regime_exhaustion_adx_lookback": 12,
    "regime_exhaustion_adx_drop_pct": 3.5,
    "regime_exhaustion_prev_adx_min": 20.0,
    "profit_exit_macd_fast": 8,
    "profit_exit_macd_slow": 21,
    "profit_exit_macd_signal_period": 9,
    "profit_exit_macd_histogram_bars": 2,
    "profit_exit_macd_condition": "cross",
    "combined_bc_window_bars": 6,
    "trail_stop_min_gain_pct": 2.0,
    "trail_stop_exit_on_signal": False,
    "trail_stop_giveback_window_bars": 2,
}

COST_PER_TRADE_PCT = 0.05  # round-trip is 2x, but per-trade is 0.05


def _run_one(args: tuple) -> tuple[str, pd.DataFrame]:
    wk, params = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="idea6_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"idea6_{wk}", "version": "idea6-held-flip-research",
                "initial_cash": 100000.0,
                "start_date": win["start"], "end_date": win["end"],
            },
            "data_source": {
                "type": "csv_file", "parser": "coinbase_intx_kline",
                "params": {"file_path": slice_file, "symbol": "ETH-PERP-INTX"},
            },
            "strategies": [{"type": "lazy_swing", "params": {**BASE_PARAMS, **params}}],
        })
        result = Controller(cfg, output_dir=tmp).run()[0]
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return wk, tl


def _analyze_one(args: tuple) -> pd.DataFrame:
    wk, tl = args
    # Index of every entry / exit / rejection event.
    # Position direction is determined by most recent BUY/SHORT.
    rows: list[dict] = []
    cur_direction: str | None = None
    cur_entry_price: float | None = None

    # Build a list of (idx, action, ts, price, details) for traversal.
    records = list(tl.itertuples(index=True))

    # Find next same-direction entry index for hypothetical flipped exit.
    # Iterate once, pick out events.
    for i, rec in enumerate(records):
        action = rec.action
        ts = rec.date
        price = float(rec.price)
        details = rec.details if isinstance(rec.details, dict) else {}

        if action == "BUY":
            cur_direction = "long"
            cur_entry_price = price
            continue
        if action == "SHORT":
            cur_direction = "short"
            cur_entry_price = price
            continue

        if action in ("SELL", "COVER"):
            cur_direction = None
            cur_entry_price = None
            continue

        # HOLD events — only interested in rejections.
        if details.get("reason") != "st_flip_ratio_rejected_hold":
            continue
        if cur_direction is None or cur_entry_price is None:
            continue

        # Find actual exit (next SELL/COVER after this rejection).
        actual_exit_price = None
        actual_exit_ts = None
        actual_exit_reason = None
        actual_exit_pnl = None
        for j in range(i + 1, len(records)):
            r2 = records[j]
            if r2.action in ("SELL", "COVER"):
                actual_exit_price = float(r2.price)
                actual_exit_ts = r2.date
                d2 = r2.details if isinstance(r2.details, dict) else {}
                actual_exit_reason = d2.get("exit_reason", "unknown")
                actual_exit_pnl = d2.get("pnl_pct")
                break

        # Find next same-direction entry (= when ST returned to original direction).
        entry_action = "BUY" if cur_direction == "long" else "SHORT"
        next_same_dir_price = None
        next_same_dir_ts = None
        for j in range(i + 1, len(records)):
            r2 = records[j]
            if r2.action == entry_action:
                next_same_dir_price = float(r2.price)
                next_same_dir_ts = r2.date
                break

        # Held PnL: from rejection_price to actual_exit_price (in held direction).
        held_pnl = None
        if actual_exit_price is not None:
            if cur_direction == "long":
                held_pnl = (actual_exit_price / price - 1) * 100 - COST_PER_TRADE_PCT
            else:
                held_pnl = (price / actual_exit_price - 1) * 100 - COST_PER_TRADE_PCT

        # Hypothetical flipped PnL: open opposite at rejection_price, exit at
        # next same-direction entry. opposite of cur_direction.
        flipped_pnl = None
        if next_same_dir_price is not None:
            if cur_direction == "long":
                # Hypothetical short: profits when price falls.
                flipped_pnl = (price / next_same_dir_price - 1) * 100 - COST_PER_TRADE_PCT
            else:
                flipped_pnl = (next_same_dir_price / price - 1) * 100 - COST_PER_TRADE_PCT

        rows.append({
            "window": wk,
            "rejection_ts": ts,
            "direction": cur_direction,
            "entry_price": cur_entry_price,
            "rejection_price": price,
            "actual_exit_ts": actual_exit_ts,
            "actual_exit_price": actual_exit_price,
            "actual_exit_reason": actual_exit_reason,
            "actual_exit_pnl_pct": actual_exit_pnl,
            "next_same_dir_ts": next_same_dir_ts,
            "next_same_dir_price": next_same_dir_price,
            "held_pnl_pct": held_pnl,
            "flipped_pnl_pct": flipped_pnl,
            "edge_flipped_minus_held": (
                (flipped_pnl - held_pnl) if (flipped_pnl is not None and held_pnl is not None) else None
            ),
        })
    return pd.DataFrame(rows)


def main() -> None:
    print(f"Running ship config across {len(WINDOWS)} quarters\n")
    t0 = time.time()
    tasks = [(wk, SHIP_PARAMS) for wk in WINDOWS]
    with mp.Pool(min(8, len(tasks))) as pool:
        run_results = pool.map(_run_one, tasks)
    print(f"Backtest done in {time.time()-t0:.0f}s. Analyzing rejections...\n")

    dfs = [_analyze_one(r) for r in run_results]
    events = pd.concat([d for d in dfs if not d.empty], ignore_index=True)

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_held_flip_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "rejection_events.csv", index=False)

    if events.empty:
        print("No rejection events found.")
        return

    # Per-quarter table
    print("=" * 120)
    print("Per-quarter held vs flipped (rejected-flip events)")
    print("=" * 120)
    print(f"{'Quarter':<10} {'N':>4} {'sumHeld%':>9} {'sumFlip%':>9} "
          f"{'meanHeld%':>10} {'meanFlip%':>10} "
          f"{'edge':>9} {'WR_held':>8} {'WR_flip':>8} {'safety_n':>9}")
    print("-" * 120)
    overall_n = 0
    overall_held = 0.0
    overall_flip = 0.0
    for wk in WINDOWS:
        sub = events[events["window"] == wk]
        if sub.empty:
            print(f"{wk:<10} no rejections")
            continue
        held_sum = sub["held_pnl_pct"].sum()
        flip_sum = sub["flipped_pnl_pct"].sum()
        held_mean = sub["held_pnl_pct"].mean()
        flip_mean = sub["flipped_pnl_pct"].mean()
        edge = flip_mean - held_mean if pd.notna(flip_mean) and pd.notna(held_mean) else float("nan")
        wr_held = (sub["held_pnl_pct"] > 0).mean() * 100
        wr_flip = (sub["flipped_pnl_pct"] > 0).mean() * 100
        n_safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
        print(f"{wk:<10} {len(sub):>4} {held_sum:>+9.2f} {flip_sum:>+9.2f} "
              f"{held_mean:>+10.3f} {flip_mean:>+10.3f} "
              f"{edge:>+9.3f} {wr_held:>7.1f}% {wr_flip:>7.1f}% {n_safety:>9d}")
        overall_n += len(sub)
        overall_held += held_sum
        overall_flip += flip_sum
    print("-" * 120)
    print(f"{'TOTAL':<10} {overall_n:>4} {overall_held:>+9.2f} {overall_flip:>+9.2f} "
          f"{events['held_pnl_pct'].mean():>+10.3f} "
          f"{events['flipped_pnl_pct'].mean():>+10.3f} "
          f"{events['edge_flipped_minus_held'].mean():>+9.3f}")

    # By exit reason
    print("\n" + "=" * 90)
    print("Held outcome by actual exit reason")
    print("=" * 90)
    by_reason = events.groupby("actual_exit_reason").agg(
        n=("held_pnl_pct", "size"),
        sum_held=("held_pnl_pct", "sum"),
        mean_held=("held_pnl_pct", "mean"),
        sum_flip=("flipped_pnl_pct", "sum"),
        mean_flip=("flipped_pnl_pct", "mean"),
    ).sort_values("n", ascending=False)
    print(by_reason.to_string(float_format=lambda x: f"{x:+.3f}"))
    by_reason.to_csv(out_dir / "by_exit_reason.csv")

    # Decision summary
    print("\n" + "=" * 90)
    print("Decision-gate summary")
    print("=" * 90)
    print(f"Total rejections:                 {len(events)}")
    print(f"Sum PnL of holds:                 {events['held_pnl_pct'].sum():+.2f}%")
    print(f"Sum PnL of hypothetical flips:    {events['flipped_pnl_pct'].sum():+.2f}%")
    print(f"Mean PnL of holds:                {events['held_pnl_pct'].mean():+.3f}%")
    print(f"Mean PnL of hypothetical flips:   {events['flipped_pnl_pct'].mean():+.3f}%")
    print(f"Mean edge (flipped − held):       {events['edge_flipped_minus_held'].mean():+.3f}%")
    print(f"Win-rate of holds:                "
          f"{(events['held_pnl_pct'] > 0).mean() * 100:.1f}%")
    print(f"Win-rate of flips:                "
          f"{(events['flipped_pnl_pct'] > 0).mean() * 100:.1f}%")
    n_safety = (events["actual_exit_reason"] == "st_flip_ratio_safety").sum()
    print(f"Holds that ended via safety stop: {n_safety} ({n_safety/len(events)*100:.1f}%)")
    print()
    print("→ Doc claim was 28 events / -2.04% held / +2.68% flipped.")
    print("  If 8q result also shows held < flipped by meaningful margin, Phase 1 grid is justified.")
    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
