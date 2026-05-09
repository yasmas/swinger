#!/usr/bin/env python3
"""Grid: combined_bc with histogram-based C signal across 8 B/C-inclined quarters.

Tests whether histogram (looser than cross) preserves C-quarter wins while
recovering B-favored quarters where cross misses.

Variants:
  - 4 references (baseline_mg1.5, pure-B mg1.75, BC_n6_cross_mg1.75 prior winner, mg2.0)
  - 12 BC histogram: 2 MACD configs × 3 X% peak-drop × 2 mg
      MACD: f8/s21/g9 (original) or f5/s13/g5 (faster)
      X%: 0 (no check), 50%, 75% — extra filter requiring histogram lost X% of peak

Quarters: 2024_Q1..Q4, 2025_Q1..Q2, 2026_Q1..Q2 (B/C-inclined; 2025_Q3/Q4 excluded)

Usage:
    PYTHONPATH=src python3 scripts/grid_search_combined_bc_histogram.py [--workers N]
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import shutil
import sys
import tempfile
import time
from itertools import product
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import Config  # noqa: E402
from controller import Controller  # noqa: E402
from reporting.reporter import compute_stats  # noqa: E402
from trade_log import TradeLogReader  # noqa: E402

WINDOWS: dict[str, dict] = {
    "2024_Q1": {"start": "2024-01-01", "end": "2024-04-01", "favored": "C"},
    "2024_Q2": {"start": "2024-04-01", "end": "2024-07-01", "favored": "C"},
    "2024_Q3": {"start": "2024-07-01", "end": "2024-10-01", "favored": "B"},
    "2024_Q4": {"start": "2024-10-01", "end": "2025-01-01", "favored": "C"},
    "2025_Q1": {"start": "2025-01-01", "end": "2025-04-01", "favored": "B"},
    "2025_Q2": {"start": "2025-04-01", "end": "2025-07-01", "favored": "C"},
    "2026_Q1": {"start": "2026-01-01", "end": "2026-04-01", "favored": "B"},
    "2026_Q2": {"start": "2026-04-01", "end": "2026-05-08", "favored": "C"},
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
    "trail_stop_exit_on_signal": True,
}

B_PARAMS = {
    "regime_exhaustion_adx_lookback": 6,
    "regime_exhaustion_adx_drop_pct": 3.5,
    "regime_exhaustion_prev_adx_min": 20.0,
}

C_CONFIGS = [
    # (label, fast, slow, signal)
    ("f8s21g9",  8, 21, 9),
    ("f5s13g5",  5, 13, 5),
]


def _make_variants() -> list[tuple[str, dict]]:
    v: list[tuple[str, dict]] = []

    # References
    v.append(("REF_baseline_mg1.5", {
        "regime_trail_mode": "strict_exhaustion",
        "regime_exhaustion_stretch_lookback": 3,
        "regime_exhaustion_kc_z_min": 1.75, "regime_exhaustion_bb_z_min": 2.75,
        "regime_exhaustion_adx_lookback": 2,
        "regime_exhaustion_prev_adx_min": 20.0,
        "regime_exhaustion_adx_drop_pct": 2.5,
        "trail_stop_min_gain_pct": 1.5,
    }))
    v.append(("REF_pureB_mg1.75", {
        "regime_trail_mode": "adx_exhaustion", **B_PARAMS,
        "trail_stop_min_gain_pct": 1.75,
    }))
    for mg in [1.75, 2.0]:
        v.append((f"REF_BC_n6_f8s21g9_cross_mg{mg}", {
            "regime_trail_mode": "combined_bc", **B_PARAMS,
            "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
            "profit_exit_macd_signal_period": 9,
            "profit_exit_macd_condition": "cross",
            "profit_exit_macd_histogram_bars": 2,
            "combined_bc_window_bars": 6,
            "trail_stop_min_gain_pct": mg,
        }))

    # Histogram BC: 2 configs × 3 X% × 2 mg = 12
    for (clbl, f, s, sg), x_drop, mg in product(
        C_CONFIGS, [0.0, 0.5, 0.75], [1.75, 2.0],
    ):
        x_str = f"x{int(x_drop*100)}"
        v.append((f"BC_n6_{clbl}_hist_{x_str}_mg{mg}", {
            "regime_trail_mode": "combined_bc", **B_PARAMS,
            "profit_exit_macd_fast": f, "profit_exit_macd_slow": s,
            "profit_exit_macd_signal_period": sg,
            "profit_exit_macd_condition": "histogram",
            "profit_exit_macd_histogram_bars": 2,
            "profit_exit_macd_histogram_peak_drop_pct": x_drop,
            "combined_bc_window_bars": 6,
            "trail_stop_min_gain_pct": mg,
        }))
    return v


VARIANTS = _make_variants()


def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    all_pnls: list[float] = []
    trail_pnls: list[float] = []
    for _, row in trade_log.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        d = row.get("details")
        if not isinstance(d, dict):
            continue
        pnl = d.get("pnl_pct")
        if pnl is None:
            continue
        pnl = float(pnl)
        all_pnls.append(pnl)
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
        if d.get("exit_reason") == "regime_trail_stop":
            trail_pnls.append(pnl)
    total = wins + losses
    return {
        "wr_pct": (wins / total * 100.0) if total else float("nan"),
        "trail_exits": len(trail_pnls),
        "avg_pnl_pct": (sum(all_pnls) / len(all_pnls)) if all_pnls else float("nan"),
        "avg_trail_pnl_pct": (sum(trail_pnls) / len(trail_pnls)) if trail_pnls else float("nan"),
    }


def _category(tag: str) -> str:
    if tag.startswith("REF_baseline"): return "baseline"
    if tag.startswith("REF_pureB"):    return "B"
    if tag.startswith("REF_BC"):       return "BC-cross"
    if tag.startswith("BC_"):          return "BC-hist"
    return "?"


def _run_one(args: tuple) -> dict:
    wk, tag, params = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="bc_hist_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"bc_hist_{wk}_{tag}", "version": "bc-hist-grid",
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
    stats = compute_stats(tl, cfg.initial_cash, 0.05)
    tm = _trade_metrics(tl)
    ret = float(stats["total_return"])
    print(
        f"  {wk} ({win['favored']}) {tag:<38} ret={ret:+7.2f}% "
        f"WR={tm['wr_pct']:5.1f}% trl={tm['trail_exits']:3d} {elapsed:.1f}s",
        flush=True,
    )
    return {
        "window": wk, "favored": win["favored"], "tag": tag,
        "category": _category(tag),
        "return_pct": ret, "wr_pct": tm["wr_pct"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "avg_pnl_pct": tm["avg_pnl_pct"],
        "avg_trail_pnl_pct": tm["avg_trail_pnl_pct"],
        "trail_exits": tm["trail_exits"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    tasks = [(wk, tag, params) for wk in WINDOWS for tag, params in VARIANTS]
    print(f"Grid: {len(VARIANTS)} variants × {len(WINDOWS)} windows = {len(tasks)} runs"
          f"  |  workers={args.workers}\n")

    t_start = time.time()
    with mp.Pool(args.workers) as pool:
        rows = pool.map(_run_one, tasks)
    df = pd.DataFrame(rows)
    out_dir = REPO / "data" / "backtests" / "eth" / "combined_bc_grid"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "summary_histogram.csv"
    df.to_csv(summary_path, index=False)

    # ---- Per-quarter table ----
    print("\n" + "=" * 130)
    print("Per-quarter (sorted by return%)")
    print("=" * 130)
    for wk in WINDOWS:
        sub = df[df.window == wk].sort_values("return_pct", ascending=False)
        print(f"\n--- {wk} (favored: {WINDOWS[wk]['favored']}) ---")
        print(f"{'Tag':<40} {'Cat':>10} {'Ret%':>8} {'WR%':>6} {'TrlPnL%':>8} {'Trl':>4} {'Shrp':>6}")
        print("-" * 100)
        for _, r in sub.iterrows():
            tp = r.avg_trail_pnl_pct
            tp_s = f"{tp:+8.3f}" if tp == tp else "     nan"
            print(f"{r.tag:<40} {r.category:>10} {r.return_pct:>+8.2f} "
                  f"{r.wr_pct:>6.1f} {tp_s} {r.trail_exits:>4d} {r.sharpe:>6.2f}")

    # ---- Compound returns across 8 quarters ----
    print("\n" + "=" * 130)
    print("Compound return across 8 quarters (sorted desc)")
    print("=" * 130)
    pivot = df.pivot(index="tag", columns="window", values="return_pct")
    pivot = pivot[list(WINDOWS.keys())]  # consistent column order

    def compound(rets):
        g = 1.0
        for r in rets:
            g *= (1.0 + r / 100.0)
        return (g - 1.0) * 100.0

    pivot["compound_pct"] = pivot.apply(lambda row: compound(row.tolist()), axis=1)
    pivot["mean_pct"] = pivot.iloc[:, :-1].mean(axis=1)
    pivot["min_pct"] = pivot.iloc[:, :-2].min(axis=1)
    pivot = pivot.sort_values("compound_pct", ascending=False)

    print(f"{'Tag':<40} " +
          "".join(f"{w[5:]:>8}" for w in WINDOWS) +
          f" {'Compd%':>10} {'Mean%':>8} {'Min%':>8}")
    print("-" * 140)
    for tag, row in pivot.iterrows():
        rets = [row[w] for w in WINDOWS]
        line = f"{tag:<40} " + "".join(f"{r:>+8.2f}" for r in rets)
        line += f" {row.compound_pct:>+10.2f} {row.mean_pct:>+8.2f} {row.min_pct:>+8.2f}"
        print(line)

    pivot_path = out_dir / "compound_histogram.csv"
    pivot.to_csv(pivot_path)

    elapsed = time.time() - t_start
    print(f"\nTotal: {elapsed:.0f}s")
    print(f"Per-run:  {summary_path}")
    print(f"Compound: {pivot_path}")


if __name__ == "__main__":
    main()
