#!/usr/bin/env python3
"""Idea 1 — profit-protect research.

Hypothesis: held-flip's catastrophic safety-bucket losses concentrate on
rejections that happen when the held position has little/no unrealized
gain. When gain at rejection is large, the safety stop still exits in
profit (or near-profit). When gain is near zero, safety stop = real loss.

Buckets the 363 rejection events from idea6_held_flip_research by gain at
rejection (entry → rejection_price), then looks at fate distribution and
PnL within each bucket.

Decision gate: if low-gain rejections are over-represented in safety bucket
AND high-gain rejections rarely end in safety, → profit-protect gate idea
has legs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
EVENTS_CSV = REPO / "data" / "backtests" / "eth" / "idea6_held_flip_research" / "rejection_events.csv"

GAIN_BUCKETS = [
    (-1e9, 0.0, "[ <0% ]"),
    (0.0, 0.5, "[0%, 0.5%)"),
    (0.5, 1.0, "[0.5%, 1%)"),
    (1.0, 2.0, "[1%, 2%)"),
    (2.0, 3.0, "[2%, 3%)"),
    (3.0, 5.0, "[3%, 5%)"),
    (5.0, 1e9, "[5%+]"),
]


def main() -> None:
    if not EVENTS_CSV.exists():
        print(f"Missing {EVENTS_CSV}.")
        return
    df = pd.read_csv(EVENTS_CSV)
    print(f"Loaded {len(df)} rejection events")

    # Compute current gain at rejection (entry → rejection_price), in favor direction.
    def gain_pct(row):
        ep = float(row["entry_price"])
        rp = float(row["rejection_price"])
        if ep <= 0:
            return float("nan")
        if row["direction"] == "long":
            return (rp / ep - 1) * 100.0
        else:
            return (ep / rp - 1) * 100.0

    df["gain_at_rejection_pct"] = df.apply(gain_pct, axis=1)

    def bucket(g):
        if pd.isna(g):
            return None
        for lo, hi, label in GAIN_BUCKETS:
            if lo <= g < hi:
                return label
        return None
    df["gain_bucket"] = df["gain_at_rejection_pct"].apply(bucket)

    print(f"\nMean gain at rejection (all 363): {df['gain_at_rejection_pct'].mean():+.3f}%")
    print(f"Median: {df['gain_at_rejection_pct'].median():+.3f}%")

    # ---- Per-bucket fate distribution ----
    print("\n" + "=" * 130)
    print("Rejection-event fate distribution by gain-at-rejection bucket")
    print("=" * 130)
    print(f"{'Bucket':<14} {'N':>4} "
          f"{'safety':>7} {'safety%':>8} "
          f"{'fast':>5} {'fast%':>7} "
          f"{'st_flip':>8} {'trail':>6} "
          f"{'meanHeld%':>10} {'meanFlip%':>10} {'edge':>9}")
    print("-" * 130)
    for lo, hi, label in GAIN_BUCKETS:
        sub = df[df["gain_bucket"] == label]
        if sub.empty:
            print(f"{label:<14} {0:>4}")
            continue
        n = len(sub)
        safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
        fast = (sub["actual_exit_reason"] == "fast_exit").sum()
        stflip = (sub["actual_exit_reason"] == "st_flip").sum()
        trail = (sub["actual_exit_reason"] == "regime_trail_stop").sum()
        mh = sub["held_pnl_pct"].mean()
        mf = sub["flipped_pnl_pct"].mean()
        edge = (mf - mh) if (pd.notna(mh) and pd.notna(mf)) else float("nan")
        print(f"{label:<14} {n:>4} {safety:>7} {safety/n*100:>+7.1f}% "
              f"{fast:>5} {fast/n*100:>+6.1f}% "
              f"{stflip:>8} {trail:>6} "
              f"{mh:>+10.3f} {mf:>+10.3f} {edge:>+9.3f}")

    # ---- Profit-protect simulation ----
    # Rule: at rejection, if gain >= X%, take the flip (don't reject); else hold as today.
    # PnL impact = (flip - held) summed over events with gain >= X.
    print("\n" + "=" * 110)
    print("Profit-protect simulation — net PnL impact on 363 rejection events")
    print("=" * 110)
    print(f"{'Threshold X (gain ≥)':<22} {'#flipped':>10} {'sumHeld%':>10} {'sumFlip%':>10} "
          f"{'Δ total':>10} {'mean Δ/event':>14}")
    print("-" * 110)
    base_held_sum = df["held_pnl_pct"].sum()
    print(f"{'baseline (always hold)':<22} {0:>10d} {base_held_sum:>+10.2f} {0:>+10.2f} {0:>+10.2f} {0:>+13.3f}%")
    for thresh in [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0]:
        # events where gain >= thresh → would flip; rest stay held.
        flip_set = df[df["gain_at_rejection_pct"] >= thresh]
        hold_set = df[df["gain_at_rejection_pct"] < thresh]
        new_total = (
            flip_set["flipped_pnl_pct"].sum()
            + hold_set["held_pnl_pct"].sum()
        )
        delta = new_total - base_held_sum
        n_flip = len(flip_set)
        per_event = delta / n_flip if n_flip else float("nan")
        print(f"{'X = '+str(thresh)+'%':<22} {n_flip:>10d} "
              f"{hold_set['held_pnl_pct'].sum():>+10.2f} "
              f"{flip_set['flipped_pnl_pct'].sum():>+10.2f} "
              f"{delta:>+10.2f} {per_event:>+13.3f}%")

    # ---- Per-quarter view of profit-protect at X=2% ----
    print("\n" + "=" * 110)
    print("Per-quarter view of profit-protect at X = 2%")
    print("=" * 110)
    print(f"{'Quarter':<10} {'#flipped':>9} {'baseHeld%':>10} {'newTotal%':>10} {'Δ':>9}")
    print("-" * 110)
    for wk in sorted(df["window"].unique()):
        sub = df[df["window"] == wk]
        flip_set = sub[sub["gain_at_rejection_pct"] >= 2.0]
        hold_set = sub[sub["gain_at_rejection_pct"] < 2.0]
        base = sub["held_pnl_pct"].sum()
        new = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
        print(f"{wk:<10} {len(flip_set):>9d} {base:>+10.2f} {new:>+10.2f} {new-base:>+9.2f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_profit_protect_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "events_with_gain_buckets.csv", index=False)
    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
