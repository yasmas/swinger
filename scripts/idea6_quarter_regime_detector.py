#!/usr/bin/env python3
"""Idea 3 — quarter-regime detector.

What's structurally different between 2024_Q4 (held-flip CRUSHES, +154pp) vs
2026_Q1 (held-flip HURTS, −22pp)? Compute regime statistics per quarter on
30m bars: realized vol, ADX level, ADX trend, BB width, ER, choppy-bar
fraction. Look for an axis that ranks quarters in the same order as
held-flip edge.

If yes, we have a quarter-level regime detector — gate the held-flip
mechanism on that signal.
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

from strategies.macd_rsi_advanced import compute_adx, compute_atr  # noqa: E402

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


def _resample_30m(slice_csv: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(slice_csv)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    df = df.set_index("ts")
    rs = df.resample("30min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    rs = rs[(rs.index >= pd.Timestamp(start)) & (rs.index < pd.Timestamp(end))]
    return rs


def _quarter_regime_stats(rs: pd.DataFrame) -> dict:
    closes = rs["close"]
    highs = rs["high"]
    lows = rs["low"]
    # ADX 14
    adx14 = compute_adx(highs, lows, closes, 14).dropna()
    # ATR 14
    atr14 = compute_atr(highs, lows, closes, 14).dropna()
    # Realized vol (1-bar log returns, 96-bar window = 2 days)
    log_ret = np.log(closes / closes.shift(1))
    vol_2d = log_ret.rolling(96, min_periods=96).std().dropna()
    # BB width: 20-bar SMA + 2σ
    sma20 = closes.rolling(20, min_periods=20).mean()
    std20 = closes.rolling(20, min_periods=20).std()
    bb_width = (4 * std20 / sma20).dropna()  # = upper-lower / sma
    # Choppy bars: |daily ret| < some threshold; here use 1-bar moves with |ret| < 0.1%
    choppy_frac = (log_ret.abs() < 0.001).rolling(48, min_periods=48).mean().dropna()
    # ER (Kaufman efficiency ratio over 24 bars = 12h)
    n = 24
    direction = (closes - closes.shift(n)).abs()
    volatility = closes.diff().abs().rolling(n, min_periods=n).sum()
    er = (direction / volatility.replace(0, np.nan)).dropna()
    # Trend strength: pct of bars with ADX >= 25
    high_adx_frac = (adx14 >= 25).mean()
    # ATR / price (relative)
    atr_pct = (atr14 / closes).dropna() * 100
    return {
        "n_bars": len(rs),
        "adx_mean": float(adx14.mean()),
        "adx_p25": float(adx14.quantile(0.25)),
        "adx_p75": float(adx14.quantile(0.75)),
        "adx_high_frac": float(high_adx_frac),
        "atr_pct_mean": float(atr_pct.mean()),
        "rv_2d_mean_pct": float(vol_2d.mean() * 100),
        "bb_width_mean": float(bb_width.mean() * 100),
        "choppy_frac_mean": float(choppy_frac.mean()),
        "er_mean": float(er.mean()),
        "abs_ret_mean_pct": float(log_ret.abs().mean() * 100),
        "ret_skew": float(log_ret.skew()),
        "ret_kurt": float(log_ret.kurtosis()),
        # Net price move over the quarter (close-to-close)
        "net_move_pct": float((closes.iloc[-1] / closes.iloc[0] - 1) * 100),
    }


def main() -> None:
    if not EVENTS_CSV.exists():
        print(f"Missing {EVENTS_CSV}.")
        return
    events = pd.read_csv(EVENTS_CSV)

    # Compute per-quarter held-flip edge (sumHeld - sumFlip).
    quarter_stats = {}
    for wk in WINDOWS:
        sub = events[events["window"] == wk]
        sumH = sub["held_pnl_pct"].sum()
        sumF = sub["flipped_pnl_pct"].sum()
        edge = sumH - sumF  # positive = held wins (mechanism saves)
        quarter_stats[wk] = {
            "n_rejections": len(sub),
            "sum_held": sumH,
            "sum_flipped": sumF,
            "edge_held_minus_flip": edge,
        }

    # Compute regime stats per quarter.
    print("Computing regime statistics per quarter...")
    regime_rows = []
    for wk, win in WINDOWS.items():
        rs = _resample_30m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        rs_stats = _quarter_regime_stats(rs)
        row = {"quarter": wk, **quarter_stats[wk], **rs_stats}
        regime_rows.append(row)
    df = pd.DataFrame(regime_rows)

    # Print per-quarter with held-flip edge AND regime stats.
    print("\n" + "=" * 175)
    print("Per-quarter held-flip edge vs regime statistics")
    print("=" * 175)
    df_print = df.set_index("quarter").T
    cols = list(WINDOWS.keys())
    df_print = df_print[cols]
    rows_to_show = [
        ("n_rejections", "Rejections"),
        ("sum_held", "ΣHeld%"),
        ("sum_flipped", "ΣFlipped%"),
        ("edge_held_minus_flip", "Edge (H−F)%"),
        ("net_move_pct", "Net move %"),
        ("adx_mean", "ADX mean"),
        ("adx_high_frac", "ADX≥25 frac"),
        ("er_mean", "ER mean"),
        ("rv_2d_mean_pct", "RV 2d %"),
        ("atr_pct_mean", "ATR/price %"),
        ("bb_width_mean", "BB width %"),
        ("choppy_frac_mean", "Choppy frac"),
        ("abs_ret_mean_pct", "|ret| mean %"),
    ]
    print(f"{'metric':<22}" + "".join(f"{c[5:]:>12}" for c in cols))
    print("-" * 175)
    for key, label in rows_to_show:
        vals = df_print.loc[key].tolist()
        cells = []
        for v in vals:
            if isinstance(v, (int, np.integer)):
                cells.append(f"{int(v):>12d}")
            else:
                cells.append(f"{v:>+12.3f}")
        print(f"{label:<22}" + "".join(cells))

    # Rank quarters by edge and by each regime statistic; check if any regime
    # stat orders quarters the same way as held-flip edge.
    print("\n" + "=" * 110)
    print("Rank-correlation: held-flip edge vs each regime statistic")
    print("=" * 110)
    print(f"{'metric':<22}  {'Spearman ρ':>12}  {'Pearson r':>12}  (target: |ρ|→1 means strong predictor)")
    print("-" * 110)
    edge = df["edge_held_minus_flip"]
    for key, label in rows_to_show:
        if key in ("n_rejections", "sum_held", "sum_flipped", "edge_held_minus_flip"):
            continue
        s = df[key]
        try:
            spearman = edge.corr(s, method="spearman")
            pearson = edge.corr(s, method="pearson")
        except Exception:
            spearman = pearson = float("nan")
        print(f"{label:<22}  {spearman:>+12.3f}  {pearson:>+12.3f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_quarter_regime_detector"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "quarter_stats.csv", index=False)
    print(f"\nResults: {out_dir}")


if __name__ == "__main__":
    main()
