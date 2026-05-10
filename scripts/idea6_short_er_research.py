#!/usr/bin/env python3
"""Step 2.b — short-window ER research on 5-min bars.

Hypothesis: 12h-aggregate ER washes out local trend strength. ST flips
often happen because of a *sharp recent* price move; the relevant question
is whether that move was directional or chop-induced. Compute ER on M
5-min bars (defaults M=48 = 4h), with two variants:

  Variant A: exclude the last N 5-min bars before the rejection (the move
             that triggered the flip is *itself* a sharp move, may bias ER
             upward — exclude it). ER computed on bars [t-N-M, t-N].
  Variant B: include all M bars ending at rejection. ER on [t-M, t].

For each (M, N) combo, bucket events by ER and check fate discrimination.
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

# (M, N) combos: M 5m bars excluding last N before rejection.
# N = 0 is "Variant B" (no exclusion).
COMBOS: list[tuple[int, int]] = []
for M in [12, 24, 36, 48, 72, 96]:        # 1h, 2h, 3h, 4h, 6h, 8h
    for N in [0, 6, 12, 24]:               # 0, 30m, 1h, 2h excluded
        COMBOS.append((M, N))


def _load_5m(slice_csv: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(slice_csv)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    df = df[(df["ts"] >= pd.Timestamp(start)) & (df["ts"] < pd.Timestamp(end))].reset_index(drop=True)
    return df


def _compute_er(closes: np.ndarray, end_idx: int, M: int, N: int) -> float:
    """ER over closes[end_idx-N-M : end_idx-N] using 5-min closes."""
    end = end_idx - N
    start = end - M
    if start < 0 or end - start < 2:
        return float("nan")
    seg = closes[start: end + 1]  # +1 to include `end` close as the boundary
    if len(seg) < 2:
        return float("nan")
    net = abs(seg[-1] - seg[0])
    path = float(np.abs(np.diff(seg)).sum())
    return net / path if path > 0 else float("nan")


def main() -> None:
    if not EVENTS_CSV.exists():
        print(f"Missing {EVENTS_CSV}.")
        return
    events = pd.read_csv(EVENTS_CSV, parse_dates=["rejection_ts"])
    print(f"Loaded {len(events)} rejection events; computing ER for {len(COMBOS)} (M,N) combos…")

    # Cache 5m closes per quarter, plus a ts->idx map.
    quarter_closes = {}
    quarter_ts_idx = {}
    for wk, win in WINDOWS.items():
        df5 = _load_5m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        quarter_closes[wk] = df5["close"].to_numpy()
        quarter_ts_idx[wk] = pd.Series(np.arange(len(df5)), index=df5["ts"])

    # Compute ER for every event × (M, N) combo.
    er_cols: dict[str, list[float]] = {}
    for M, N in COMBOS:
        er_cols[f"er_M{M}_N{N}"] = []
    for _, row in events.iterrows():
        wk = row["window"]
        closes = quarter_closes.get(wk)
        ts_idx = quarter_ts_idx.get(wk)
        if closes is None or ts_idx is None:
            for M, N in COMBOS:
                er_cols[f"er_M{M}_N{N}"].append(float("nan"))
            continue
        ts = pd.Timestamp(row["rejection_ts"])
        # Find the bar idx whose ts equals (or nearest before) rejection_ts.
        # rejection_ts is itself a 5-min bar's open_time at hourly close.
        if ts in ts_idx.index:
            i = int(ts_idx.loc[ts])
        else:
            # nearest before
            pos = ts_idx.index.searchsorted(ts) - 1
            i = int(ts_idx.iloc[pos]) if pos >= 0 else -1
        for M, N in COMBOS:
            er_cols[f"er_M{M}_N{N}"].append(_compute_er(closes, i, M, N) if i >= 0 else float("nan"))
    for col, vals in er_cols.items():
        events[col] = vals

    # Add gain at rejection (handy for cross-tabs later).
    def gain_pct(row):
        ep = float(row["entry_price"]); rp = float(row["rejection_price"])
        if ep <= 0: return float("nan")
        return ((rp / ep - 1) if row["direction"] == "long" else (ep / rp - 1)) * 100.0
    events["gain_at_rejection_pct"] = events.apply(gain_pct, axis=1)

    # ---- Threshold sweep per (M, N) combo. Find best ΔPnL. ----
    print("\n" + "=" * 100)
    print("Best threshold per (M, N) combo  (by Δ total PnL across 363 events)")
    print("=" * 100)
    print(f"{'M (bars/h)':<14} {'N (bars/h)':<14} {'ER>=X best':>12} "
          f"{'#flipped':>10} {'Δ total%':>10} {'Δ/event%':>10}")
    print("-" * 100)
    base_held = events["held_pnl_pct"].sum()
    summary_rows = []
    for M, N in COMBOS:
        col = f"er_M{M}_N{N}"
        sub = events[events[col].notna()].copy()
        if sub.empty:
            continue
        best_delta = -1e18
        best_thresh = None
        best_n_flip = 0
        # Sweep thresholds
        for thresh in np.linspace(0.05, 0.55, 11):
            flip_set = sub[sub[col] >= thresh]
            hold_set = sub[sub[col] < thresh]
            new_total = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
            delta = new_total - base_held
            if delta > best_delta:
                best_delta = delta
                best_thresh = thresh
                best_n_flip = len(flip_set)
        per_event = best_delta / best_n_flip if best_n_flip else float("nan")
        summary_rows.append({
            "M": M, "N": N, "M_h": M / 12.0, "N_h": N / 12.0,
            "best_thresh": best_thresh, "n_flip": best_n_flip,
            "delta_total": best_delta, "delta_per_event": per_event,
        })
    summary = pd.DataFrame(summary_rows).sort_values("delta_total", ascending=False)
    for _, r in summary.iterrows():
        print(f"M={r.M:>3} ({r.M_h:.1f}h)  N={r.N:>3} ({r.N_h:.1f}h)  "
              f"ER≥{r.best_thresh:.2f}  {int(r.n_flip):>10d}  "
              f"{r.delta_total:>+9.2f}%  {r.delta_per_event:>+9.3f}%")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_short_er_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_dir / "combo_summary.csv", index=False)
    events.to_csv(out_dir / "events_with_short_er.csv", index=False)

    # ---- Detailed bucket view of top 3 combos ----
    print("\n" + "=" * 130)
    print("Top 3 (M, N) combos — bucket-level fate distribution")
    print("=" * 130)
    BUCKETS = [
        (0.0, 0.10, "[0.00, 0.10)"),
        (0.10, 0.20, "[0.10, 0.20)"),
        (0.20, 0.30, "[0.20, 0.30)"),
        (0.30, 0.40, "[0.30, 0.40)"),
        (0.40, 0.55, "[0.40, 0.55)"),
        (0.55, 1.01, "[0.55, 1.00]"),
    ]
    for _, r in summary.head(3).iterrows():
        M, N = int(r.M), int(r.N)
        col = f"er_M{M}_N{N}"
        print(f"\nM={M} N={N}  (mean ER = {events[col].mean():.3f}, p25 = {events[col].quantile(0.25):.3f}, "
              f"median = {events[col].median():.3f}, p75 = {events[col].quantile(0.75):.3f})")
        print(f"{'Bucket':<14} {'N':>4} {'safety':>7} {'safety%':>8} {'fast':>5} {'fast%':>7} "
              f"{'meanHeld%':>10} {'meanFlip%':>10} {'edge':>9}")
        print("-" * 130)
        sub = events[events[col].notna()]
        for lo, hi, name in BUCKETS:
            seg = sub[(sub[col] >= lo) & (sub[col] < hi)]
            if seg.empty:
                print(f"{name:<14} {0:>4}")
                continue
            n = len(seg)
            safety = (seg["actual_exit_reason"] == "st_flip_ratio_safety").sum()
            fast = (seg["actual_exit_reason"] == "fast_exit").sum()
            mh = seg["held_pnl_pct"].mean(); mf = seg["flipped_pnl_pct"].mean()
            edge = mf - mh
            print(f"{name:<14} {n:>4} {safety:>7} {safety/n*100:>+7.1f}% "
                  f"{fast:>5} {fast/n*100:>+6.1f}% "
                  f"{mh:>+10.3f} {mf:>+10.3f} {edge:>+9.3f}")

    # ---- Per-quarter view of best combo ----
    if not summary.empty:
        best = summary.iloc[0]
        M, N = int(best.M), int(best.N)
        col = f"er_M{M}_N{N}"
        thresh = best.best_thresh
        print("\n" + "=" * 110)
        print(f"Per-quarter view of best combo: M={M} ({M/12:.1f}h), N={N} ({N/12:.1f}h), threshold ER ≥ {thresh:.2f}")
        print("=" * 110)
        print(f"{'Quarter':<10} {'#flipped':>9} {'baseHeld%':>10} {'newTotal%':>10} {'Δ':>9}")
        for wk in sorted(events["window"].unique()):
            sub = events[events["window"] == wk]
            sub = sub[sub[col].notna()]
            flip_set = sub[sub[col] >= thresh]
            hold_set = sub[sub[col] < thresh]
            base = sub["held_pnl_pct"].sum()
            new = flip_set["flipped_pnl_pct"].sum() + hold_set["held_pnl_pct"].sum()
            print(f"{wk:<10} {len(flip_set):>9d} {base:>+10.2f} {new:>+10.2f} {new-base:>+9.2f}")

    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
