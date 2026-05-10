#!/usr/bin/env python3
"""Step 2 — vol-expansion + against-position-bar research on 5m bars.

For each rejection event, check two AND-conditions on the rejection bar:
  1. TR (true range) percentile over last M=48 5m bars (vol expansion).
  2. Bar moved AGAINST the held position direction by ≥ Y%
     (capitulation magnitude). For long position (would-be flip=short):
     bar_change = (close - open) / open * 100 ≤ -Y. Mirror for short.

Sweep TR percentile P ∈ {0.80, 0.90, 0.95} × against-magnitude Y ∈
{0.0 (sign only), 0.10, 0.20, 0.30, 0.50}.
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
M = 48  # 4h on 5m bars


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
    print(f"Loaded {len(events)} rejection events; M={M} bars (4h)")

    cache = {}
    for wk, win in WINDOWS.items():
        df5 = _load_5m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        cache[wk] = df5

    # For each event, compute TR percentile over last M bars + bar-against magnitude.
    tr_pct_at_rej = []
    against_mag_pct = []  # signed against (positive = bar moved against position by this %)
    for _, row in events.iterrows():
        wk = row["window"]
        df = cache.get(wk)
        if df is None:
            tr_pct_at_rej.append(float("nan"))
            against_mag_pct.append(float("nan"))
            continue
        ts = pd.Timestamp(row["rejection_ts"])
        ts_idx = pd.Series(np.arange(len(df)), index=df["ts"])
        if ts in ts_idx.index:
            i = int(ts_idx.loc[ts])
        else:
            pos = ts_idx.index.searchsorted(ts) - 1
            i = int(ts_idx.iloc[pos]) if pos >= 0 else -1
        if i < M:
            tr_pct_at_rej.append(float("nan"))
            against_mag_pct.append(float("nan"))
            continue
        # TR for current bar
        h = float(df["high"].iloc[i])
        l = float(df["low"].iloc[i])
        prev_c = float(df["close"].iloc[i - 1])
        tr_now = max(h - l, abs(h - prev_c), abs(l - prev_c))
        # TR percentile vs prior M bars
        prior_tr = []
        for j in range(i - M, i):
            hh = float(df["high"].iloc[j])
            ll = float(df["low"].iloc[j])
            pc = float(df["close"].iloc[j - 1]) if j > 0 else hh
            prior_tr.append(max(hh - ll, abs(hh - pc), abs(ll - pc)))
        prior_tr = np.array(prior_tr)
        # Percentile rank of tr_now within prior_tr
        pct = float((prior_tr < tr_now).mean())
        tr_pct_at_rej.append(pct)
        # Against-position magnitude
        o = float(df["open"].iloc[i])
        c = float(df["close"].iloc[i])
        if o > 0:
            bar_change_pct = (c - o) / o * 100.0
        else:
            bar_change_pct = 0.0
        # For long-position rejection, "against" = bar fell. Mirror for short.
        if row["direction"] == "long":
            against = -bar_change_pct  # positive if bar fell
        else:
            against = bar_change_pct  # positive if bar rose
        against_mag_pct.append(against)
    events["tr_pct_at_rej"] = tr_pct_at_rej
    events["against_mag_pct"] = against_mag_pct

    print(f"\nTR percentile distribution: mean {events['tr_pct_at_rej'].mean():.2f}, "
          f"median {events['tr_pct_at_rej'].median():.2f}, "
          f"p25 {events['tr_pct_at_rej'].quantile(0.25):.2f}, "
          f"p75 {events['tr_pct_at_rej'].quantile(0.75):.2f}")
    print(f"Against-magnitude distribution: mean {events['against_mag_pct'].mean():+.3f}%, "
          f"median {events['against_mag_pct'].median():+.3f}%, "
          f"p75 {events['against_mag_pct'].quantile(0.75):+.3f}%, "
          f"p90 {events['against_mag_pct'].quantile(0.90):+.3f}%")

    # ---- Cross-tab: TR percentile × against magnitude ----
    print("\n" + "=" * 130)
    print("Cross-tab: TR percentile thresholds × against-magnitude thresholds (Δ on rejection PnL)")
    print("=" * 130)
    base_held = events["held_pnl_pct"].sum()
    print(f"Baseline (always hold): sum held = {base_held:+.2f}%\n")
    P_VALS = [0.50, 0.70, 0.80, 0.85, 0.90, 0.95]
    Y_VALS = [0.0, 0.10, 0.20, 0.30, 0.50]
    print(f"  {'P\\Y':<8}" + "".join(f"{y:>+10.2f}" for y in Y_VALS))
    print("-" * 130)
    for P in P_VALS:
        cells = []
        for Y in Y_VALS:
            mask = (
                events["tr_pct_at_rej"].notna()
                & (events["tr_pct_at_rej"] >= P)
                & (events["against_mag_pct"] >= Y)
            )
            n_flip = int(mask.sum())
            if n_flip == 0:
                cells.append(f"{'-':>10}")
                continue
            flip = events[mask]
            hold = events[~mask]
            new_total = flip["flipped_pnl_pct"].sum() + hold["held_pnl_pct"].sum()
            delta = new_total - base_held
            cells.append(f"{delta:>+8.1f}({n_flip:>2})")
        print(f"  P={P:.2f}  " + "".join(cells))

    # ---- Per-quarter for a few promising combos ----
    candidates = [
        (0.80, 0.10), (0.80, 0.20), (0.85, 0.10), (0.85, 0.20),
        (0.90, 0.10), (0.90, 0.20), (0.95, 0.10), (0.95, 0.20),
        (0.90, 0.0), (0.95, 0.0),
    ]
    print("\n" + "=" * 130)
    print("Per-quarter Δ for candidate (P, Y) combos")
    print("=" * 130)
    print(f"  {'(P, Y)':<14}" + "".join(f"{q[5:]:>12}" for q in WINDOWS) + f"{'TotalΔ':>10}")
    print("-" * 130)
    for P, Y in candidates:
        mask = (
            events["tr_pct_at_rej"].notna()
            & (events["tr_pct_at_rej"] >= P)
            & (events["against_mag_pct"] >= Y)
        )
        cells = []
        total_delta = 0
        for wk in WINDOWS:
            sub = events[events["window"] == wk]
            m = mask & (events["window"] == wk)
            flip = sub[sub.index.isin(events[m].index)]
            hold = sub[~sub.index.isin(events[m].index)]
            base = sub["held_pnl_pct"].sum()
            new = flip["flipped_pnl_pct"].sum() + hold["held_pnl_pct"].sum()
            delta = new - base
            total_delta += delta
            cells.append(f"{delta:>+12.2f}")
        print(f"  P={P:.2f},Y={Y:.2f}" + "".join(cells) + f"{total_delta:>+10.2f}")

    # ---- Bucket view of TR-percentile alone (no against gate) ----
    print("\n" + "=" * 130)
    print("Per-bucket fate distribution by TR percentile (no against-magnitude gate)")
    print("=" * 130)
    BUCKETS = [(0, 0.5, "[0, 0.5)"), (0.5, 0.7, "[0.5, 0.7)"),
               (0.7, 0.85, "[0.7, 0.85)"), (0.85, 0.95, "[0.85, 0.95)"),
               (0.95, 1.01, "[0.95, 1.0]")]
    print(f"  {'bucket':<14} {'N':>4} {'safety%':>9} {'fast%':>8} {'meanHeld':>10} {'meanFlip':>10} {'edge':>9}")
    for lo, hi, label in BUCKETS:
        sub = events[(events["tr_pct_at_rej"] >= lo) & (events["tr_pct_at_rej"] < hi)]
        if sub.empty:
            continue
        n = len(sub)
        safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
        fast = (sub["actual_exit_reason"] == "fast_exit").sum()
        mh = sub["held_pnl_pct"].mean()
        mf = sub["flipped_pnl_pct"].mean()
        print(f"  {label:<14} {n:>4} {safety/n*100:>+8.1f}% {fast/n*100:>+7.1f}% "
              f"{mh:>+10.3f} {mf:>+10.3f} {mf-mh:>+9.3f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea7_step2_volexp_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "events_with_volexp.csv", index=False)
    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
