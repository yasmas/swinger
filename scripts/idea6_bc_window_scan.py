#!/usr/bin/env python3
"""Idea #6 — B and C signal scan in a window around each rejection event.

Read the rejection events captured by `idea6_held_flip_research.py` and, for
each one, scan a window of 30-min bars around the rejection timestamp to see
whether B (ADX exhaustion) or C (MACD cross in flip direction) fires within
[-N_before, +N_after] bars. Partition by the rejection's actual fate
(safety-stop = wrong, fast-exit = right) and check whether B/C state
discriminates between the two buckets.

If discrimination is clean, we have a "second-opinion" override: even when
flip_vol_ratio rejects, honor the flip if B or C also confirms within window.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strategies.macd_rsi_advanced import compute_adx  # noqa: E402

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

# Ship indicator params (match lazy_swing.py)
RESAMPLE = "30min"
ADX_PERIOD = 14
ADX_LOOKBACK = 12       # bars (= 6h on 30m)
ADX_DROP_PCT = 3.5      # B fires if pct_change ≤ -3.5%
PREV_ADX_MIN = 20.0     # B requires prior ADX ≥ 20
MACD_FAST = 8
MACD_SLOW = 21
MACD_SIGNAL = 9

# Window around the rejection (in 30-min bars)
N_BEFORE = 3   # 1.5h
N_AFTER = 6    # 3h


def _resample_30m(slice_csv: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(slice_csv)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    df = df.set_index("ts")
    rs = df.resample(RESAMPLE).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    rs = rs[(rs.index >= pd.Timestamp(start)) & (rs.index < pd.Timestamp(end))]
    return rs


def _compute_signals(rs: pd.DataFrame) -> pd.DataFrame:
    """Compute B (ADX exhaustion) and direction-keyed C (MACD cross) per bar."""
    df = rs.copy()
    # ADX
    df["adx"] = compute_adx(df["high"], df["low"], df["close"], ADX_PERIOD)
    df["prev_adx"] = df["adx"].shift(ADX_LOOKBACK)
    df["adx_pct_change"] = (df["adx"] / df["prev_adx"].replace(0.0, np.nan) - 1.0) * 100.0
    df["b_fires"] = (
        (df["prev_adx"] >= PREV_ADX_MIN)
        & (df["adx_pct_change"] <= -ADX_DROP_PCT)
    )

    # MACD on this 30m series
    ema_fast = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd_line"] = ema_fast - ema_slow
    df["macd_sig"] = df["macd_line"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    prev_l = df["macd_line"].shift(1)
    prev_s = df["macd_sig"].shift(1)
    # C-for-long-position (rejected flip-to-short): MACD crossed below signal.
    df["c_long_fires"] = (prev_l >= prev_s) & (df["macd_line"] < df["macd_sig"])
    # C-for-short-position (rejected flip-to-long): MACD crossed above signal.
    df["c_short_fires"] = (prev_l <= prev_s) & (df["macd_line"] > df["macd_sig"])
    return df


def _scan_event(rs_signals: pd.DataFrame, rejection_ts: pd.Timestamp,
                direction: str) -> dict:
    """For a rejection at timestamp `rejection_ts` (5-min bar timestamp at the
    hourly close), scan 30-min signal bars in [-N_BEFORE, +N_AFTER]."""
    # The rejection happens at a 30-min bar close. Resampled index is bar
    # START. Bar START = rejection_ts - 30min  (close = start + 30min).
    bar_start = rejection_ts - pd.Timedelta(RESAMPLE)
    # Find idx of that bar in resampled signals.
    idx_arr = rs_signals.index.get_indexer([bar_start], method="nearest")
    if len(idx_arr) == 0 or idx_arr[0] < 0:
        return {}
    center_i = int(idx_arr[0])
    n = len(rs_signals)

    c_col = "c_long_fires" if direction == "long" else "c_short_fires"

    # Per-offset signal state
    offsets = list(range(-N_BEFORE, N_AFTER + 1))
    b_per_off: dict[int, bool] = {}
    c_per_off: dict[int, bool] = {}
    for off in offsets:
        i = center_i + off
        if 0 <= i < n:
            row = rs_signals.iloc[i]
            b_per_off[off] = bool(row["b_fires"]) if not pd.isna(row["b_fires"]) else False
            c_per_off[off] = bool(row[c_col]) if not pd.isna(row[c_col]) else False
        else:
            b_per_off[off] = False
            c_per_off[off] = False

    b_any = any(b_per_off.values())
    c_any = any(c_per_off.values())
    bc_any = b_any or c_any
    b_at_0 = b_per_off.get(0, False)
    c_at_0 = c_per_off.get(0, False)
    # First offset within [0, +N_AFTER] where each fired (forward-looking only)
    b_first_fwd = next((off for off in range(0, N_AFTER + 1) if b_per_off.get(off, False)), None)
    c_first_fwd = next((off for off in range(0, N_AFTER + 1) if c_per_off.get(off, False)), None)

    out: dict = {
        "b_at_0": b_at_0,
        "c_at_0": c_at_0,
        "b_any_window": b_any,
        "c_any_window": c_any,
        "bc_any_window": bc_any,
        "b_first_fwd_off": b_first_fwd,
        "c_first_fwd_off": c_first_fwd,
    }
    # Per-offset booleans (compact int per offset for export)
    for off in offsets:
        out[f"b_off_{off:+d}"] = int(b_per_off[off])
        out[f"c_off_{off:+d}"] = int(c_per_off[off])
    return out


def main() -> None:
    if not EVENTS_CSV.exists():
        print(f"Missing {EVENTS_CSV}. Run scripts/idea6_held_flip_research.py first.")
        return
    events = pd.read_csv(EVENTS_CSV, parse_dates=["rejection_ts"])
    print(f"Loaded {len(events)} rejection events")

    # Compute signals once per window, cache.
    cache: dict[str, pd.DataFrame] = {}
    t0 = time.time()
    for wk, win in WINDOWS.items():
        rs = _resample_30m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        cache[wk] = _compute_signals(rs)
        print(f"  {wk}: {len(rs)} 30m bars, "
              f"{int(cache[wk]['b_fires'].sum())} B-fires, "
              f"{int(cache[wk]['c_long_fires'].sum())} C-long-fires, "
              f"{int(cache[wk]['c_short_fires'].sum())} C-short-fires")

    # Scan each event.
    extra_rows = []
    for i, row in events.iterrows():
        wk = row["window"]
        if wk not in cache:
            extra_rows.append({})
            continue
        scan = _scan_event(cache[wk], pd.Timestamp(row["rejection_ts"]), row["direction"])
        extra_rows.append(scan)
    extra = pd.DataFrame(extra_rows)
    out = pd.concat([events.reset_index(drop=True), extra], axis=1)

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_bc_window_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_dir / "events_with_bc_window.csv", index=False)

    # Aggregate by exit-reason fate.
    print("\n" + "=" * 130)
    print(f"B / C window-firing rates by actual exit reason  (window = [-{N_BEFORE}, +{N_AFTER}] 30m bars)")
    print("=" * 130)
    print(f"{'fate':<22} {'N':>4} {'B@0':>7} {'C@0':>7} "
          f"{'Bany%':>7} {'Cany%':>7} {'B|C%':>7} "
          f"{'meanHeld%':>10} {'meanFlip%':>10}")
    print("-" * 130)
    by_fate = out.groupby("actual_exit_reason")
    for fate, sub in sorted(by_fate, key=lambda kv: -len(kv[1])):
        n = len(sub)
        b_at_0 = sub["b_at_0"].mean() * 100
        c_at_0 = sub["c_at_0"].mean() * 100
        b_any = sub["b_any_window"].mean() * 100
        c_any = sub["c_any_window"].mean() * 100
        bc_any = sub["bc_any_window"].mean() * 100
        held = sub["held_pnl_pct"].mean()
        flip = sub["flipped_pnl_pct"].mean()
        print(f"{fate:<22} {n:>4} {b_at_0:>+6.1f}% {c_at_0:>+6.1f}% "
              f"{b_any:>+6.1f}% {c_any:>+6.1f}% {bc_any:>+6.1f}% "
              f"{held:>+10.3f} {flip:>+10.3f}")

    # Discrimination — split events by B|C any-fired and look at outcomes.
    print("\n" + "=" * 110)
    print("Outcome split by 'B or C fired anywhere in window'")
    print("=" * 110)
    print(f"{'group':<28} {'N':>4} {'meanHeld%':>10} {'meanFlip%':>10} "
          f"{'edge_flip-held%':>16} {'safety_n':>9} {'fast_n':>7}")
    print("-" * 110)
    for label, sub in [
        ("B|C fired in window",     out[out["bc_any_window"]]),
        ("B|C silent in window",    out[~out["bc_any_window"]]),
        ("B fired (any in window)", out[out["b_any_window"]]),
        ("C fired (any in window)", out[out["c_any_window"]]),
        ("Both B and C fired",      out[out["b_any_window"] & out["c_any_window"]]),
        ("B fired only at 0",       out[out["b_at_0"]]),
        ("C fired only at 0",       out[out["c_at_0"]]),
    ]:
        n = len(sub)
        if n == 0:
            print(f"{label:<28} {n:>4}  (no events)")
            continue
        held = sub["held_pnl_pct"].mean()
        flip = sub["flipped_pnl_pct"].mean()
        edge = flip - held
        safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
        fast = (sub["actual_exit_reason"] == "fast_exit").sum()
        print(f"{label:<28} {n:>4} {held:>+10.3f} {flip:>+10.3f} {edge:>+15.3f}% "
              f"{safety:>9d} {fast:>7d}")

    # Forward-only: B/C first fire at offsets 0..N_AFTER (exclude pre-rejection)
    print("\n" + "=" * 90)
    print("Distribution of first-fire offset (forward only)")
    print("=" * 90)
    for label, col in [("B first fwd offset", "b_first_fwd_off"),
                       ("C first fwd offset", "c_first_fwd_off")]:
        s = out[col].dropna()
        if len(s) == 0:
            continue
        print(f"  {label}: N={len(s)}, "
              f"mean={s.mean():+.2f}, "
              f"median={s.median():+.0f}, "
              f"p25={s.quantile(0.25):+.0f}, p75={s.quantile(0.75):+.0f}")

    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
