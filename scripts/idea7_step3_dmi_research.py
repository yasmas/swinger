#!/usr/bin/env python3
"""Step 3 — DMI dominance / cross research on 5m bars.

For each rejection event, compute +DI and -DI (Wilder DMI) at rejection
time on 5m bars with various periods. Test:

  - dmi_gap = -DI − +DI for long-position rejection (positive = bearish
    dominance); mirror for short. As % of (+DI + -DI).
  - dmi_cross_recent: did DMI cross in flip direction in last K 5m bars?

Sweep periods {14, 28, 42, 56} and (where relevant) lookback windows.
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
PERIODS = [14, 28, 42, 56]
CROSS_LOOKBACKS = [3, 6, 12, 24]  # 5m bars (15m, 30m, 1h, 2h)


def _load_5m(slice_csv: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(slice_csv)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    df = df[(df["ts"] >= pd.Timestamp(start)) & (df["ts"] < pd.Timestamp(end))].reset_index(drop=True)
    return df


def _wilder_dmi(highs: pd.Series, lows: pd.Series, closes: pd.Series,
                period: int) -> tuple[pd.Series, pd.Series]:
    prev_h = highs.shift(1)
    prev_l = lows.shift(1)
    prev_c = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_c).abs(),
        (lows - prev_c).abs(),
    ], axis=1).max(axis=1)
    up = highs - prev_h
    dn = prev_l - lows
    plus_dm = up.where(up > dn, 0.0).clip(lower=0)
    minus_dm = dn.where(dn > up, 0.0).clip(lower=0)
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr
    return plus_di, minus_di


def main() -> None:
    if not EVENTS_CSV.exists():
        print(f"Missing {EVENTS_CSV}.")
        return
    events = pd.read_csv(EVENTS_CSV, parse_dates=["rejection_ts"])
    print(f"Loaded {len(events)} rejection events; periods={PERIODS}")

    # Cache 5m + DMI per quarter per period.
    cache = {}
    for wk, win in WINDOWS.items():
        df5 = _load_5m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        per_period = {}
        for p in PERIODS:
            plus_di, minus_di = _wilder_dmi(df5["high"], df5["low"], df5["close"], p)
            per_period[p] = (plus_di.to_numpy(), minus_di.to_numpy())
        cache[wk] = {
            "df": df5,
            "ts_idx": pd.Series(np.arange(len(df5)), index=df5["ts"]),
            "per_period": per_period,
        }

    # For each event, compute (a) gap_pct in flip direction at rejection,
    # (b) cross-in-last-K for various K, per period.
    for p in PERIODS:
        events[f"dmi_gap_pct_p{p}"] = float("nan")
        events[f"dmi_dom_p{p}"] = False
        for K in CROSS_LOOKBACKS:
            events[f"dmi_cross_K{K}_p{p}"] = False

    for i, row in events.iterrows():
        wk = row["window"]
        c = cache.get(wk)
        if c is None:
            continue
        ts = pd.Timestamp(row["rejection_ts"])
        if ts in c["ts_idx"].index:
            idx = int(c["ts_idx"].loc[ts])
        else:
            pos = c["ts_idx"].index.searchsorted(ts) - 1
            idx = int(c["ts_idx"].iloc[pos]) if pos >= 0 else -1
        if idx < 0:
            continue
        for p in PERIODS:
            plus_di_arr, minus_di_arr = c["per_period"][p]
            if idx >= len(plus_di_arr):
                continue
            pdi = float(plus_di_arr[idx]) if not np.isnan(plus_di_arr[idx]) else float("nan")
            mdi = float(minus_di_arr[idx]) if not np.isnan(minus_di_arr[idx]) else float("nan")
            if np.isnan(pdi) or np.isnan(mdi):
                continue
            total = pdi + mdi
            if total <= 0:
                continue
            # Long-rej (would-be flip=short): bearish dominance = (mdi - pdi)/total
            if row["direction"] == "long":
                gap = (mdi - pdi) / total * 100
                dom = mdi > pdi
            else:
                gap = (pdi - mdi) / total * 100
                dom = pdi > mdi
            events.at[i, f"dmi_gap_pct_p{p}"] = gap
            events.at[i, f"dmi_dom_p{p}"] = dom
            # Cross check
            for K in CROSS_LOOKBACKS:
                if idx - K - 1 < 0:
                    continue
                # Look for the bars [idx-K, idx]: did DMI cross in flip direction?
                # = there exists j ∈ [idx-K+1, idx] where prior bar had opposite ordering.
                cross_found = False
                for j in range(max(idx - K + 1, 1), idx + 1):
                    p_now = float(plus_di_arr[j]) if not np.isnan(plus_di_arr[j]) else float("nan")
                    m_now = float(minus_di_arr[j]) if not np.isnan(minus_di_arr[j]) else float("nan")
                    p_prev = float(plus_di_arr[j - 1]) if not np.isnan(plus_di_arr[j - 1]) else float("nan")
                    m_prev = float(minus_di_arr[j - 1]) if not np.isnan(minus_di_arr[j - 1]) else float("nan")
                    if np.isnan(p_now) or np.isnan(m_now) or np.isnan(p_prev) or np.isnan(m_prev):
                        continue
                    if row["direction"] == "long":
                        # cross to bearish: prev pdi >= mdi, now mdi > pdi
                        if p_prev >= m_prev and m_now > p_now:
                            cross_found = True
                            break
                    else:
                        if m_prev >= p_prev and p_now > m_now:
                            cross_found = True
                            break
                events.at[i, f"dmi_cross_K{K}_p{p}"] = cross_found

    base_held = events["held_pnl_pct"].sum()

    # ---- Dominance threshold sweep per period ----
    print("\n" + "=" * 130)
    print(f"DMI gap threshold sweep per period (Δ on rejection PnL; baseline {base_held:+.2f}%)")
    print("=" * 130)
    print(f"{'period':<8}" + "".join(f"{x:>+11.0f}" for x in [0, 5, 10, 15, 20, 25, 30, 40, 50])
          + f"  (gap% threshold)")
    print("-" * 130)
    for p in PERIODS:
        col = f"dmi_gap_pct_p{p}"
        cells = []
        for thresh in [0, 5, 10, 15, 20, 25, 30, 40, 50]:
            mask = events[col].notna() & (events[col] >= thresh)
            n = int(mask.sum())
            if n == 0:
                cells.append(f"{'-':>11}")
                continue
            flip = events[mask]
            hold = events[~mask]
            new = flip["flipped_pnl_pct"].sum() + hold["held_pnl_pct"].sum()
            delta = new - base_held
            cells.append(f"{delta:>+7.1f}({n})")
        print(f"p={p:<5}" + "".join(cells))

    # ---- DMI cross within last K bars, per period ----
    print("\n" + "=" * 130)
    print(f"DMI cross within last K 5m bars per period (Δ on rejection PnL; baseline {base_held:+.2f}%)")
    print("=" * 130)
    print(f"{'period':<8}" + "".join(f"{f'K={K}':>12}" for K in CROSS_LOOKBACKS))
    print("-" * 130)
    for p in PERIODS:
        cells = []
        for K in CROSS_LOOKBACKS:
            col = f"dmi_cross_K{K}_p{p}"
            mask = events[col].astype(bool)
            n = int(mask.sum())
            if n == 0:
                cells.append(f"{'-':>12}")
                continue
            flip = events[mask]
            hold = events[~mask]
            new = flip["flipped_pnl_pct"].sum() + hold["held_pnl_pct"].sum()
            delta = new - base_held
            cells.append(f"{delta:>+8.1f}({n})")
        print(f"p={p:<5}" + "".join(cells))

    # ---- Top combos per-quarter ----
    print("\n" + "=" * 130)
    print("Per-quarter Δ for promising (period, gap) combos")
    print("=" * 130)
    candidates = [
        (14, 0), (14, 10), (14, 20),
        (28, 0), (28, 10), (28, 20),
        (42, 0), (42, 10), (42, 20),
        (56, 0), (56, 10), (56, 20),
    ]
    print(f"  {'(p,gap)':<14}" + "".join(f"{q[5:]:>10}" for q in WINDOWS) + f"{'TotalΔ':>10}")
    print("-" * 130)
    for p, gap in candidates:
        col = f"dmi_gap_pct_p{p}"
        mask = events[col].notna() & (events[col] >= gap)
        cells = []
        total = 0
        for wk in WINDOWS:
            sub = events[events["window"] == wk]
            m = mask & (events["window"] == wk)
            flip = sub[sub.index.isin(events[m].index)]
            hold = sub[~sub.index.isin(events[m].index)]
            base = sub["held_pnl_pct"].sum()
            new = flip["flipped_pnl_pct"].sum() + hold["held_pnl_pct"].sum()
            d = new - base
            total += d
            cells.append(f"{d:>+10.2f}")
        print(f"  p={p},g={gap}".ljust(14) + "".join(cells) + f"{total:>+10.2f}")

    # ---- Top per-period view of bucket fates ----
    print("\n" + "=" * 130)
    print("Per-bucket fate distribution by DMI gap_pct (period=28, mid)")
    print("=" * 130)
    BUCKETS = [(-100, 0, "[<0% (against)]"), (0, 10, "[0%, 10%)"),
               (10, 20, "[10%, 20%)"), (20, 30, "[20%, 30%)"),
               (30, 50, "[30%, 50%)"), (50, 101, "[50%+]")]
    p = 28
    col = f"dmi_gap_pct_p{p}"
    print(f"  {'bucket':<20} {'N':>4} {'safety%':>9} {'fast%':>8} "
          f"{'meanHeld':>10} {'meanFlip':>10} {'edge':>9}")
    for lo, hi, label in BUCKETS:
        sub = events[(events[col] >= lo) & (events[col] < hi)]
        if sub.empty:
            continue
        n = len(sub)
        safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
        fast = (sub["actual_exit_reason"] == "fast_exit").sum()
        mh = sub["held_pnl_pct"].mean()
        mf = sub["flipped_pnl_pct"].mean()
        print(f"  {label:<20} {n:>4} {safety/n*100:>+8.1f}% {fast/n*100:>+7.1f}% "
              f"{mh:>+10.3f} {mf:>+10.3f} {mf-mh:>+9.3f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea7_step3_dmi_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "events_with_dmi.csv", index=False)
    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
