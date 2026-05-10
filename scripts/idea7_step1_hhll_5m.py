#!/usr/bin/env python3
"""Step 1 — HH/LL structural break on 5m bars (K ∈ {36, 48, 60}).

For each rejection event, check whether the rejection bar made a new
low (for would-be short flip) below the min of the prior K 5m bars,
or a new high (for would-be long flip) above the max of prior K.

Also tests continuous magnitude (how far past the prior extreme).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
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
EVENTS_CSV = REPO / "data" / "backtests" / "eth" / "idea6_held_flip_research" / "rejection_events.csv"
K_VALUES = [36, 48, 60]


def _load_5m(slice_csv: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(slice_csv)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    df = df[(df["ts"] >= pd.Timestamp(start)) & (df["ts"] < pd.Timestamp(end))].reset_index(drop=True)
    return df


def main() -> None:
    if not EVENTS_CSV.exists():
        print(f"Missing {EVENTS_CSV}.")
        return
    events = pd.read_csv(EVENTS_CSV, parse_dates=["rejection_ts"])
    print(f"Loaded {len(events)} rejection events")

    # Load 5m per quarter and pre-build ts->idx maps.
    cache: dict[str, dict] = {}
    for wk, win in WINDOWS.items():
        df5 = _load_5m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        cache[wk] = {
            "highs": df5["high"].to_numpy(),
            "lows": df5["low"].to_numpy(),
            "closes": df5["close"].to_numpy(),
            "ts_idx": pd.Series(np.arange(len(df5)), index=df5["ts"]),
        }

    # For each event, compute HH/LL break + magnitude for each K.
    for K in K_VALUES:
        events[f"break_K{K}"] = False
        events[f"break_mag_K{K}"] = 0.0  # how far past the prior extreme as % of close

    for i, row in events.iterrows():
        wk = row["window"]
        if wk not in cache:
            continue
        c = cache[wk]
        ts = pd.Timestamp(row["rejection_ts"])
        if ts in c["ts_idx"].index:
            idx = int(c["ts_idx"].loc[ts])
        else:
            pos = c["ts_idx"].index.searchsorted(ts) - 1
            idx = int(c["ts_idx"].iloc[pos]) if pos >= 0 else -1
        if idx < 0:
            continue
        for K in K_VALUES:
            if idx - K < 0:
                continue
            close = float(c["closes"][idx])
            if row["direction"] == "long":
                # would-be flip = short → look for LL break
                prior_min = float(np.min(c["lows"][idx - K: idx]))
                bar_low = float(c["lows"][idx])
                if bar_low < prior_min and close > 0:
                    events.at[i, f"break_K{K}"] = True
                    events.at[i, f"break_mag_K{K}"] = (prior_min - bar_low) / close * 100
            else:
                # would-be flip = long → look for HH break
                prior_max = float(np.max(c["highs"][idx - K: idx]))
                bar_high = float(c["highs"][idx])
                if bar_high > prior_max and close > 0:
                    events.at[i, f"break_K{K}"] = True
                    events.at[i, f"break_mag_K{K}"] = (bar_high - prior_max) / close * 100

    # ---- For each K: binary-break discrimination ----
    print("\n" + "=" * 130)
    print("Per-K binary break discrimination (broke vs didn't break)")
    print("=" * 130)
    for K in K_VALUES:
        col = f"break_K{K}"
        n_total = len(events)
        n_broke = int(events[col].sum())
        print(f"\nK={K} bars ({K*5}min = {K/12:.1f}h)  "
              f"broke: {n_broke}/{n_total} ({n_broke/n_total*100:.1f}%)")
        print(f"  {'group':<22} {'N':>4} {'safety':>7} {'safety%':>8} "
              f"{'fast':>5} {'fast%':>7} {'meanHeld%':>10} {'meanFlip%':>10} {'edge':>9}")
        print("  " + "-" * 110)
        for label, sub in [("BROKE", events[events[col]]),
                           ("did NOT break", events[~events[col]])]:
            if sub.empty:
                continue
            n = len(sub)
            safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
            fast = (sub["actual_exit_reason"] == "fast_exit").sum()
            mh = sub["held_pnl_pct"].mean()
            mf = sub["flipped_pnl_pct"].mean()
            edge = mf - mh
            print(f"  {label:<22} {n:>4} {safety:>7} {safety/n*100:>+7.1f}% "
                  f"{fast:>5} {fast/n*100:>+6.1f}% "
                  f"{mh:>+10.3f} {mf:>+10.3f} {edge:>+9.3f}")

    # ---- Threshold simulation: flip if broke (and optionally if mag ≥ X) ----
    print("\n" + "=" * 110)
    print("Threshold simulation — Δ total PnL on 363 rejections if we flip when 'broke' is True")
    print("=" * 110)
    base_held = events["held_pnl_pct"].sum()
    print(f"Baseline (always hold): sum held = {base_held:+.2f}%\n")
    print(f"{'K':<6} {'#broke':>8} {'sumHeld_kept%':>14} {'sumFlip_broke%':>16} "
          f"{'Δ total%':>10} {'mean Δ/event%':>14}")
    print("-" * 110)
    for K in K_VALUES:
        col = f"break_K{K}"
        flip_set = events[events[col]]
        hold_set = events[~events[col]]
        new_total = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
        delta = new_total - base_held
        per_event = delta / max(1, len(flip_set))
        print(f"K={K:<3} {len(flip_set):>8d} "
              f"{hold_set['held_pnl_pct'].sum():>+14.2f} "
              f"{flip_set['flipped_pnl_pct'].sum():>+16.2f} "
              f"{delta:>+10.2f} {per_event:>+13.3f}%")

    # ---- Magnitude-thresholded flip (only flip if mag ≥ X) ----
    print("\n" + "=" * 110)
    print("Magnitude-gated flip simulation — only flip when break magnitude ≥ X (% of close)")
    print("=" * 110)
    for K in K_VALUES:
        col_b = f"break_K{K}"
        col_m = f"break_mag_K{K}"
        print(f"\nK={K}:")
        print(f"  {'X (mag ≥)':<14} {'#flipped':>10} {'Δ total%':>10} {'mean Δ/event%':>14}")
        for x in [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]:
            mask = (events[col_b]) & (events[col_m] >= x)
            flip_set = events[mask]
            hold_set = events[~mask]
            new_total = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
            delta = new_total - base_held
            per_event = delta / max(1, len(flip_set))
            print(f"  X={x:.2f}        {len(flip_set):>10d} {delta:>+10.2f} {per_event:>+13.3f}%")

    # ---- Per-quarter view of best K (binary) ----
    print("\n" + "=" * 110)
    print("Per-quarter view of binary break (best K by total Δ)")
    print("=" * 110)
    best_K = max(K_VALUES, key=lambda K: (
        events[events[f"break_K{K}"]]["flipped_pnl_pct"].sum()
        + events[~events[f"break_K{K}"]]["held_pnl_pct"].sum()
        - base_held
    ))
    col = f"break_K{best_K}"
    print(f"Best K = {best_K}")
    print(f"  {'Quarter':<10} {'#flipped':>9} {'baseHeld%':>10} {'newTotal%':>10} {'Δ':>9}")
    for wk in sorted(events["window"].unique()):
        sub = events[events["window"] == wk]
        flip_set = sub[sub[col]]
        hold_set = sub[~sub[col]]
        base = sub["held_pnl_pct"].sum()
        new = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
        print(f"  {wk:<10} {len(flip_set):>9d} {base:>+10.2f} {new:>+10.2f} {new-base:>+9.2f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea7_step1_hhll_5m"
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "events_with_hhll_5m.csv", index=False)
    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
