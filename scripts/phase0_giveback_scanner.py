#!/usr/bin/env python3
"""Phase 0 — peak-gain giveback scanner.

Runs the current ship config (BC_m8-21-9_adx14_lb12, windowed-giveback N=2)
across the 8 B/C-inclined quarters, preserves trade logs, and analyses every
`regime_trail_stop` exit by **peak gain** bucket.

Key columns produced per bucket:
    n_exits, total_pnl_pct (sum), avg_pnl_pct, avg_peak_gain_pct,
    avg_giveback_pct, what-if pnl uplift if giveback had been tighter
    ({0.50, 0.35, 0.25}%).

Decision gate for Phase 1: do peak-gain ≥ 3% trades give back meaningful
PnL today? If yes → tier table is worth grid-testing.
"""
from __future__ import annotations

import multiprocessing as mp
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
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

# Mirrors grid_bc_giveback_window_8q.py BASE_PARAMS (current ship base).
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

# Idea #2 winner: lookback 12 (not 6).
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

PEAK_BUCKETS = [
    (2.0, 3.0, "[2%,3%)"),
    (3.0, 5.0, "[3%,5%)"),
    (5.0, 8.0, "[5%,8%)"),
    (8.0, float("inf"), "[8%+)"),
]
WHATIF_TIGHTEN_TO = [0.50, 0.35, 0.25]  # tighter giveback floors to test (%)


def _run_one(args: tuple) -> tuple[str, pd.DataFrame]:
    wk, params = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="phase0_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"phase0_{wk}", "version": "phase0-giveback-scan",
                "initial_cash": 100000.0,
                "start_date": win["start"], "end_date": win["end"],
            },
            "data_source": {
                "type": "csv_file", "parser": "coinbase_intx_kline",
                "params": {"file_path": slice_file, "symbol": "ETH-PERP-INTX"},
            },
            "strategies": [{"type": "lazy_swing", "params": {**BASE_PARAMS, **params}}],
        })
        t0 = time.time()
        result = Controller(cfg, output_dir=tmp).run()[0]
        elapsed = time.time() - t0
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    rows: list[dict] = []
    for _, row in tl.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        d = row.get("details")
        if not isinstance(d, dict):
            continue
        if d.get("exit_reason") != "regime_trail_stop":
            continue
        peak = d.get("trail_gain_pct")
        gb = d.get("trail_giveback_pct")
        pnl = d.get("pnl_pct")
        active = d.get("trail_stop_pct")
        if peak is None or gb is None or pnl is None:
            continue
        rows.append({
            "window": wk,
            "date": row["date"],
            "action": row["action"],
            "peak_gain_pct": float(peak),
            "giveback_pct": float(gb),
            "active_trail_stop_pct": float(active) if active is not None else float("nan"),
            "pnl_pct": float(pnl),
            "bars_held": d.get("bars_held"),
        })
    df = pd.DataFrame(rows)
    print(f"  {wk}  trail_exits={len(df)}  ({elapsed:.1f}s)", flush=True)
    return wk, df


def _bucket_label(peak: float) -> str | None:
    for lo, hi, label in PEAK_BUCKETS:
        if lo <= peak < hi:
            return label
    return None


def _summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bucket"] = df["peak_gain_pct"].apply(_bucket_label)
    df = df[df["bucket"].notna()]

    # what-if uplifts: if giveback had been tighter, exit price would have been
    # closer to peak by (giveback - tighter), so pnl improves by that delta
    # (per trade in this bucket whose actual giveback exceeds the tighter floor).
    for tighter in WHATIF_TIGHTEN_TO:
        delta = (df["giveback_pct"] - tighter).clip(lower=0.0)
        df[f"whatif_uplift_gb{tighter:.2f}"] = delta

    g = df.groupby("bucket")
    summary = g.agg(
        n_exits=("pnl_pct", "size"),
        sum_pnl_pct=("pnl_pct", "sum"),
        avg_pnl_pct=("pnl_pct", "mean"),
        avg_peak_gain_pct=("peak_gain_pct", "mean"),
        avg_giveback_pct=("giveback_pct", "mean"),
        avg_active_stop_pct=("active_trail_stop_pct", "mean"),
        n_negative=("pnl_pct", lambda s: int((s < 0).sum())),
    )
    for tighter in WHATIF_TIGHTEN_TO:
        col = f"whatif_uplift_gb{tighter:.2f}"
        summary[f"sum_uplift_gb{tighter:.2f}"] = g[col].sum()

    # Add a TOTAL row.
    total = pd.DataFrame({
        "n_exits": [df["pnl_pct"].size],
        "sum_pnl_pct": [df["pnl_pct"].sum()],
        "avg_pnl_pct": [df["pnl_pct"].mean()],
        "avg_peak_gain_pct": [df["peak_gain_pct"].mean()],
        "avg_giveback_pct": [df["giveback_pct"].mean()],
        "avg_active_stop_pct": [df["active_trail_stop_pct"].mean()],
        "n_negative": [int((df["pnl_pct"] < 0).sum())],
    }, index=["TOTAL"])
    for tighter in WHATIF_TIGHTEN_TO:
        col = f"whatif_uplift_gb{tighter:.2f}"
        total[f"sum_uplift_gb{tighter:.2f}"] = df[col].sum()
    summary = pd.concat([summary, total])
    return summary


def main() -> None:
    tasks = [(wk, SHIP_PARAMS) for wk in WINDOWS]
    print(f"Running ship config across {len(tasks)} quarters\n")
    t0 = time.time()
    with mp.Pool(min(8, len(tasks))) as pool:
        results = pool.map(_run_one, tasks)

    all_exits = pd.concat([df for _, df in results if not df.empty], ignore_index=True)
    out_dir = REPO / "data" / "backtests" / "eth" / "phase0_giveback_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    all_exits.to_csv(out_dir / "trail_exits_all.csv", index=False)

    # Per-quarter table.
    print("\n" + "=" * 110)
    print("Per-quarter trail-exit profile")
    print("=" * 110)
    print(f"{'Quarter':<10} {'N':>4} {'sumPnL%':>9} {'avgPnL%':>9} "
          f"{'avgPeak%':>9} {'avgGB%':>8} "
          + " ".join(f"{'+gb<' + str(t):>8}" for t in WHATIF_TIGHTEN_TO))
    print("-" * 110)
    for wk, df in results:
        if df.empty:
            print(f"{wk:<10} no trail exits")
            continue
        line = f"{wk:<10} {len(df):>4} {df['pnl_pct'].sum():>+9.2f} {df['pnl_pct'].mean():>+9.3f} "
        line += f"{df['peak_gain_pct'].mean():>+9.3f} {df['giveback_pct'].mean():>+8.3f}"
        for tighter in WHATIF_TIGHTEN_TO:
            uplift = (df["giveback_pct"] - tighter).clip(lower=0).sum()
            line += f" {uplift:>+8.2f}"
        print(line)

    # Pooled bucket table.
    print("\n" + "=" * 110)
    print("Peak-gain bucket summary (pooled across 8 quarters)")
    print("=" * 110)
    summary = _summarize(all_exits)
    summary_path = out_dir / "bucket_summary.csv"
    summary.to_csv(summary_path)
    with pd.option_context("display.float_format", lambda x: f"{x:+.3f}",
                           "display.width", 200,
                           "display.max_columns", 20):
        print(summary.to_string())

    # Decision-gate readout.
    print("\n" + "=" * 110)
    print("Decision gate")
    print("=" * 110)
    high_buckets = ["[3%,5%)", "[5%,8%)", "[8%+)"]
    n_high = int(summary.loc[high_buckets, "n_exits"].sum()) if all(
        b in summary.index for b in high_buckets
    ) else 0
    sum_uplift_25 = float(summary.loc[high_buckets, "sum_uplift_gb0.25"].sum()) if n_high else 0.0
    sum_uplift_35 = float(summary.loc[high_buckets, "sum_uplift_gb0.35"].sum()) if n_high else 0.0
    print(f"Trail exits with peak ≥ 3%: {n_high} / {int(summary.loc['TOTAL', 'n_exits'])}")
    print(f"Total what-if uplift if peak≥3% buckets had giveback floor 0.35%: "
          f"+{sum_uplift_35:.2f} pnl-points")
    print(f"Total what-if uplift if peak≥3% buckets had giveback floor 0.25%: "
          f"+{sum_uplift_25:.2f} pnl-points")
    print()
    print("→ Phase 1 worth pursuing if uplifts are large vs typical per-quarter return")
    print("  (current ship per-quarter mean ≈ +59%; uplift of even +5–10pp / quarter is meaningful).")
    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
