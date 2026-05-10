#!/usr/bin/env python3
"""Step 2 — rolling ER research at per-event level.

The quarter-aggregate ER (idea 3) ranked quarters with ρ ≈ −0.7 against
held-flip edge. But quarter-ER averages many bars; rolling ER at the
rejection moment may behave differently.

For each of the 363 rejection events, compute rolling Kaufman ER
(period=24 30m bars = 12h, same as ship's `regime_momentum_er_period`)
at the rejection timestamp. Bucket events by ER and check whether ER
discriminates fate (safety vs fast-exit) and held-vs-flipped edge.

Decision gate: if ER cleanly separates buckets, ER-gated mechanism is
worth implementing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

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
ER_PERIOD = 24  # bars (12h on 30m), matches ship's regime_momentum_er_period


def _resample_30m(slice_csv: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(slice_csv)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    df = df.set_index("ts")
    rs = df.resample("30min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    rs = rs[(rs.index >= pd.Timestamp(start)) & (rs.index < pd.Timestamp(end))]
    return rs


def _kaufman_er(closes: pd.Series, period: int) -> pd.Series:
    direction = (closes - closes.shift(period)).abs()
    volatility = closes.diff().abs().rolling(period, min_periods=period).sum()
    return direction / volatility.replace(0, np.nan)


def main() -> None:
    if not EVENTS_CSV.exists():
        print(f"Missing {EVENTS_CSV}.")
        return
    events = pd.read_csv(EVENTS_CSV, parse_dates=["rejection_ts"])
    print(f"Loaded {len(events)} rejection events")

    # Compute rolling ER per quarter; look up ER at each rejection bar.
    per_quarter_er = {}
    for wk, win in WINDOWS.items():
        rs = _resample_30m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        rs["er"] = _kaufman_er(rs["close"], ER_PERIOD)
        per_quarter_er[wk] = rs

    er_at_rejection = []
    for _, row in events.iterrows():
        rs = per_quarter_er.get(row["window"])
        if rs is None:
            er_at_rejection.append(float("nan"))
            continue
        # rejection_ts is a 5-min timestamp at the hourly close → 30m bar starts
        # one bar earlier.
        bar_start = pd.Timestamp(row["rejection_ts"]) - pd.Timedelta("30min")
        idx_arr = rs.index.get_indexer([bar_start], method="nearest")
        if len(idx_arr) == 0 or idx_arr[0] < 0:
            er_at_rejection.append(float("nan"))
            continue
        er_at_rejection.append(float(rs["er"].iloc[int(idx_arr[0])]))
    events["er_at_rejection"] = er_at_rejection

    # Also add gain at rejection (entry → rejection price, in favor direction).
    def gain_pct(row):
        ep = float(row["entry_price"]); rp = float(row["rejection_price"])
        if ep <= 0: return float("nan")
        if row["direction"] == "long": return (rp / ep - 1) * 100.0
        else: return (ep / rp - 1) * 100.0
    events["gain_at_rejection_pct"] = events.apply(gain_pct, axis=1)

    print(f"\nER summary across 363 events:")
    print(f"  mean   = {events['er_at_rejection'].mean():.3f}")
    print(f"  median = {events['er_at_rejection'].median():.3f}")
    print(f"  p25    = {events['er_at_rejection'].quantile(0.25):.3f}")
    print(f"  p75    = {events['er_at_rejection'].quantile(0.75):.3f}")

    # Bucket by ER quintile + a hand-picked threshold suite.
    def show_buckets(df, buckets, label_prefix):
        print(f"\n{'Bucket':<22} {'N':>4} "
              f"{'safety':>7} {'safety%':>8} {'fast':>5} {'fast%':>7} "
              f"{'st_flip':>8} {'trail':>6} "
              f"{'meanHeld%':>10} {'meanFlip%':>10} {'edge':>9}")
        print("-" * 130)
        for lo, hi, name in buckets:
            sub = df[(df["er_at_rejection"] >= lo) & (df["er_at_rejection"] < hi)]
            if sub.empty:
                print(f"{name:<22} {0:>4}")
                continue
            n = len(sub)
            safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
            fast = (sub["actual_exit_reason"] == "fast_exit").sum()
            stflip = (sub["actual_exit_reason"] == "st_flip").sum()
            trail = (sub["actual_exit_reason"] == "regime_trail_stop").sum()
            mh = sub["held_pnl_pct"].mean()
            mf = sub["flipped_pnl_pct"].mean()
            edge = (mf - mh) if (pd.notna(mh) and pd.notna(mf)) else float("nan")
            print(f"{name:<22} {n:>4} {safety:>7} {safety/n*100:>+7.1f}% "
                  f"{fast:>5} {fast/n*100:>+6.1f}% "
                  f"{stflip:>8} {trail:>6} "
                  f"{mh:>+10.3f} {mf:>+10.3f} {edge:>+9.3f}")

    print("\n" + "=" * 130)
    print("Quintile buckets")
    print("=" * 130)
    qs = events["er_at_rejection"].dropna().quantile([0, 0.2, 0.4, 0.6, 0.8, 1.0]).tolist()
    quintile_buckets = [
        (qs[i], qs[i+1] + 1e-9, f"Q{i+1} [{qs[i]:.3f},{qs[i+1]:.3f}]") for i in range(5)
    ]
    show_buckets(events, quintile_buckets, "Q")

    print("\n" + "=" * 130)
    print("Hand-picked thresholds (rounded)")
    print("=" * 130)
    fixed_buckets = [
        (0.0, 0.10, "[0.00, 0.10)"),
        (0.10, 0.15, "[0.10, 0.15)"),
        (0.15, 0.20, "[0.15, 0.20)"),
        (0.20, 0.25, "[0.20, 0.25)"),
        (0.25, 0.30, "[0.25, 0.30)"),
        (0.30, 0.40, "[0.30, 0.40)"),
        (0.40, 1.00 + 1e-9, "[0.40, 1.00]"),
    ]
    show_buckets(events, fixed_buckets, "fix")

    # ---- ER-gate simulation: flip if ER >= X, hold otherwise ----
    print("\n" + "=" * 110)
    print("ER-gate simulation — flip rejections only when ER ≥ X")
    print("=" * 110)
    print(f"{'X (ER ≥)':<12} {'#flipped':>10} {'sumHeld%':>10} {'sumFlip%':>10} "
          f"{'Δ total':>10} {'mean Δ/event':>14}")
    print("-" * 110)
    df_valid = events[events["er_at_rejection"].notna()]
    base_sum = df_valid["held_pnl_pct"].sum()
    print(f"{'baseline (all hold)':<12} {0:>10d} {base_sum:>+10.2f} {0:>+10.2f} {0:>+10.2f} {0:>+13.3f}%")
    for thresh in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]:
        flip_set = df_valid[df_valid["er_at_rejection"] >= thresh]
        hold_set = df_valid[df_valid["er_at_rejection"] < thresh]
        new_total = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
        delta = new_total - base_sum
        n_flip = len(flip_set)
        per_event = delta / n_flip if n_flip else float("nan")
        print(f"{'ER ≥ '+str(thresh):<12} {n_flip:>10d} "
              f"{hold_set['held_pnl_pct'].sum():>+10.2f} "
              f"{flip_set['flipped_pnl_pct'].sum():>+10.2f} "
              f"{delta:>+10.2f} {per_event:>+13.3f}%")

    # ---- Per-quarter ER-gate at best threshold (find it from above) ----
    print("\n" + "=" * 110)
    print("Per-quarter view of ER-gate at three candidate thresholds")
    print("=" * 110)
    for thresh in [0.20, 0.25, 0.30]:
        print(f"\n  ER ≥ {thresh}:")
        print(f"  {'Quarter':<10} {'#flipped':>9} {'baseHeld%':>10} {'newTotal%':>10} {'Δ':>9}")
        for wk in sorted(df_valid["window"].unique()):
            sub = df_valid[df_valid["window"] == wk]
            flip_set = sub[sub["er_at_rejection"] >= thresh]
            hold_set = sub[sub["er_at_rejection"] < thresh]
            base = sub["held_pnl_pct"].sum()
            new = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
            print(f"  {wk:<10} {len(flip_set):>9d} {base:>+10.2f} {new:>+10.2f} {new-base:>+9.2f}")

    # ---- Compare: events captured by profit-protect (X=3%) vs ER-gate (≥0.25) ----
    print("\n" + "=" * 110)
    print("Overlap: profit-protect (gain ≥ 3%) vs ER-gate (ER ≥ 0.25)")
    print("=" * 110)
    pp = (df_valid["gain_at_rejection_pct"] >= 3.0)
    er = (df_valid["er_at_rejection"] >= 0.25)
    print(f"  profit-protect only: {((pp & ~er).sum())}")
    print(f"  ER-gate only:        {((~pp & er).sum())}")
    print(f"  both:                {((pp & er).sum())}")
    print(f"  neither:             {((~pp & ~er).sum())}")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_rolling_er_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "events_with_er.csv", index=False)
    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
