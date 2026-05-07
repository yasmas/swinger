#!/usr/bin/env python3
"""Grid search: profit-exit mechanisms for LazySwing.

Tests four categories of exit trigger (each replaces strict_exhaustion):
  A) Relaxed strict_exhaustion  — vary KC-z, BB-z, ADX-drop thresholds
  B) adx_exhaustion             — ADX drop only, no KC/BB stretch required
  C) macd_exit                  — standard MACD cross / histogram reversal
  D) ema_trail                  — price crosses EMA after min-gain reached

Tested on 10 quarterly windows (2024 Q1-Q4, 2025 Q1-Q4, 2026 Q1, 2026 Q2 partial).
Ranked by return_pct × win_rate (primary metric).

Usage:
    PYTHONPATH=src python3 scripts/grid_search_lazyswing_profit_exit.py [--workers N] [--window WINDOW]
"""

from __future__ import annotations

import argparse
import itertools
import multiprocessing as mp
import sys
import time
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

# ---------------------------------------------------------------------------
# Data windows
# ---------------------------------------------------------------------------
WINDOWS: dict[str, dict] = {
    "2024_Q1": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-01-01", "end": "2024-04-01",
    },
    "2024_Q2": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-04-01", "end": "2024-07-01",
    },
    "2024_Q3": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-07-01", "end": "2024-10-01",
    },
    "2024_Q4": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-10-01", "end": "2025-01-01",
    },
    "2025_Q1": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-01-01", "end": "2025-04-01",
    },
    "2025_Q2": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-04-01", "end": "2025-07-01",
    },
    "2025_Q3": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-07-01", "end": "2025-10-01",
    },
    "2025_Q4": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-10-01", "end": "2026-01-01",
    },
    "2026_Q1": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
        "start": "2026-01-01", "end": "2026-04-01",
    },
    "2026_Q2": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
        "start": "2026-04-01", "end": "2026-05-08",
    },
}

# ---------------------------------------------------------------------------
# Shared HOF base params (everything except the trail-stop mechanism)
# ---------------------------------------------------------------------------
BASE_PARAMS: dict = {
    "resample_interval": "30min",
    "supertrend_atr_period": 25,
    "supertrend_multiplier": 1.75,
    "adaptive_st_vol_period": 24,
    "adaptive_st_vol_long_period": 336,
    "adaptive_st_enter_ratio_threshold": 1.0,
    "adaptive_st_exit_ratio_threshold": 0.85,
    "adaptive_st_min_high_bars": 48,
    "flip_vol_ratio_enabled": True,
    "flip_vol_ratio_short_period": 4,
    "flip_vol_ratio_long_period": 336,
    "flip_vol_ratio_regime_mode": "squared",
    "flip_vol_ratio_regime_low_min": 0.7,
    "flip_vol_ratio_regime_high_min": 1.0,
    "flip_vol_ratio_regime_low_stop_pct": 1.0,
    "flip_vol_ratio_regime_high_stop_pct": 2.5,
    "flip_vol_ratio_regime_power": 1.5,
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
    "fast_exit_enabled": True,
    "fast_exit_cooldown_bars": 4,
    "fast_exit_rvol_short_period": 24,
    "fast_exit_rvol_long_period": 2016,
    "fast_exit_rvol_low_min": 1.1,
    "fast_exit_rvol_high_min": 1.3,
    "fast_exit_reentry_confirm": True,
    "flat_realign_hourly_closes": 0,
    # Shared regime indicator params (used by all trail modes)
    "regime_trail_enabled": True,
    "regime_momentum_adx_period": 14,
    "regime_momentum_adx_min": 40.0,
    "regime_momentum_er_period": 24,
    "regime_momentum_er_min": 0.40,
    "regime_momentum_adx_delta_bars": 2,
    "regime_momentum_adx_delta_min": 1.0,
    "regime_momentum_vol_period": 24,
    "regime_momentum_vol_long_period": 336,
    "regime_momentum_vol_ratio_max": 1.0,
    # Trail mechanics (shared; exit_on_signal=True so all modes exit immediately on trigger)
    "trail_stop_pct": 0.75,
    "trail_stop_atr_multiple": 0.75,
    "trail_stop_cooldown_bars": 0,
    "trail_stop_reentry_enabled": False,
    "trail_stop_exit_on_signal": True,
}


# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------

def _make_variants() -> list[tuple[str, dict]]:
    variants: list[tuple[str, dict]] = []

    # --- Baseline (current HOF strict_exhaustion) ---
    variants.append(("baseline", {
        "regime_trail_mode": "strict_exhaustion",
        "regime_exhaustion_stretch_lookback": 3,
        "regime_exhaustion_kc_z_min": 1.75,
        "regime_exhaustion_bb_z_min": 2.75,
        "regime_exhaustion_adx_lookback": 2,
        "regime_exhaustion_prev_adx_min": 20.0,
        "regime_exhaustion_adx_drop_pct": 2.5,
        "trail_stop_min_gain_pct": 1.5,
    }))

    # --- Category A: Relaxed strict_exhaustion ---
    for kc_z, bb_z, adx_drop in itertools.product(
        [0.5, 1.0, 1.75],       # kc_z_min
        [1.0, 1.5, 2.75],       # bb_z_min
        [1.0, 2.5],             # adx_drop_pct
    ):
        tag = f"A_kc{kc_z}_bb{bb_z}_d{adx_drop}"
        if tag == "A_kc1.75_bb2.75_d2.5":
            continue  # identical to baseline
        variants.append((tag, {
            "regime_trail_mode": "strict_exhaustion",
            "regime_exhaustion_kc_z_min": kc_z,
            "regime_exhaustion_bb_z_min": bb_z,
            "regime_exhaustion_adx_lookback": 2,
            "regime_exhaustion_prev_adx_min": 20.0,
            "regime_exhaustion_adx_drop_pct": adx_drop,
            "trail_stop_min_gain_pct": 1.5,
        }))

    # --- Category B: ADX-only exhaustion ---
    for adx_lb, adx_drop, prev_min in itertools.product(
        [2, 4, 6],              # adx_lookback (controls pct_change window)
        [1.0, 2.0, 3.5],        # adx_drop_pct
        [15.0, 20.0, 30.0],     # prev_adx_min
    ):
        tag = f"B_lb{adx_lb}_d{adx_drop}_min{prev_min:.0f}"
        variants.append((tag, {
            "regime_trail_mode": "adx_exhaustion",
            "regime_exhaustion_adx_lookback": adx_lb,
            "regime_exhaustion_adx_drop_pct": adx_drop,
            "regime_exhaustion_prev_adx_min": prev_min,
            "trail_stop_min_gain_pct": 1.5,
        }))

    # --- Category C: MACD exit ---
    for fast, slow, sig_p, cond, min_gain in itertools.product(
        [8, 13],                # macd_fast
        [21, 34],               # macd_slow
        [9, 13],                # signal_period
        ["cross", "histogram"], # exit condition
        [1.0, 1.5, 2.0],        # min_gain_pct
    ):
        if fast >= slow:
            continue
        tag = f"C_f{fast}s{slow}g{sig_p}_{cond}_mg{min_gain}"
        variants.append((tag, {
            "regime_trail_mode": "macd_exit",
            "profit_exit_macd_fast": fast,
            "profit_exit_macd_slow": slow,
            "profit_exit_macd_signal_period": sig_p,
            "profit_exit_macd_condition": cond,
            "profit_exit_macd_histogram_bars": 2,
            "trail_stop_min_gain_pct": min_gain,
        }))

    # --- Category D: EMA trail ---
    for ema_p, min_gain in itertools.product(
        [5, 8, 13, 21],         # ema_period
        [1.0, 1.5, 2.0],        # min_gain_pct
    ):
        tag = f"D_ema{ema_p}_mg{min_gain}"
        variants.append((tag, {
            "regime_trail_mode": "ema_trail",
            "profit_exit_ema_period": ema_p,
            "trail_stop_min_gain_pct": min_gain,
        }))

    return variants


VARIANTS = _make_variants()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _win_rate(trade_log: pd.DataFrame) -> tuple[float, int, int]:
    wins = losses = 0
    for _, row in trade_log.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        pnl = details.get("pnl_pct")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    total = wins + losses
    return (wins / total * 100.0) if total else float("nan"), wins, losses


def _trail_exits(trade_log: pd.DataFrame) -> int:
    count = 0
    for _, row in trade_log.iterrows():
        details = row.get("details")
        if isinstance(details, dict) and details.get("exit_reason") == "regime_trail_stop":
            count += 1
    return count


def _build_config(window_key: str, tag: str, trail_params: dict) -> Config:
    win = WINDOWS[window_key]
    params = {**BASE_PARAMS, **trail_params}
    return Config({
        "backtest": {
            "name": f"profit_exit_{window_key}_{tag}",
            "version": "profit-exit-grid",
            "initial_cash": 100000.0,
            "start_date": win["start"],
            "end_date": win["end"],
        },
        "data_source": {
            "type": "csv_file",
            "parser": "coinbase_intx_kline",
            "params": {
                "file_path": win["data_file"],
                "symbol": "ETH-PERP-INTX",
            },
        },
        "strategies": [{"type": "lazy_swing", "params": params}],
    })


# ---------------------------------------------------------------------------
# Single-run worker (must be top-level for multiprocessing pickling)
# ---------------------------------------------------------------------------

def _run_one(args: tuple) -> dict:
    window_key, tag, trail_params, output_root = args
    out_dir = Path(output_root) / window_key / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = _build_config(window_key, tag, trail_params)
    t0 = time.time()
    result = Controller(cfg, output_dir=str(out_dir)).run()[0]
    elapsed = time.time() - t0
    tl = TradeLogReader().read(result.trade_log_path)
    stats = compute_stats(tl, cfg.initial_cash, 0.05)
    wr, wins, losses = _win_rate(tl)
    trail_ex = _trail_exits(tl)
    ret = float(stats["total_return"])
    row = {
        "window": window_key,
        "tag": tag,
        "category": tag[0] if tag[0] in "ABCD" else "baseline",
        "return_pct": ret,
        "wr_pct": wr,
        "score": ret * wr if (ret > 0 and wr == wr) else float("nan"),
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "wins": wins,
        "losses": losses,
        "trail_exits": trail_ex,
        "n_trades": int(stats["num_buys"]) + int(stats["num_shorts"]),
        "elapsed_sec": round(elapsed, 1),
        **trail_params,
    }
    print(
        f"  {window_key} {tag:<38} ret={ret:+8.2f}% WR={wr:5.1f}% "
        f"score={row['score']:7.1f} dd={row['max_dd_pct']:+6.1f}% trail={trail_ex:3d} {elapsed:.1f}s",
        flush=True,
    )
    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=mp.cpu_count(),
                        help="Parallel workers (default: all CPUs)")
    parser.add_argument("--window", choices=[*WINDOWS.keys(), "all"], default="all",
                        help="Restrict to a single window for quick tests")
    args = parser.parse_args()

    windows = list(WINDOWS.keys()) if args.window == "all" else [args.window]
    output_root = REPO / "data" / "backtests" / "eth" / "profit_exit_grid"
    output_root.mkdir(parents=True, exist_ok=True)

    tasks = [
        (wk, tag, params, str(output_root))
        for wk in windows
        for tag, params in VARIANTS
    ]
    print(f"Grid: {len(VARIANTS)} variants × {len(windows)} windows = {len(tasks)} runs")
    print(f"Using {args.workers} workers\n")

    t_start = time.time()
    if args.workers == 1:
        rows = [_run_one(t) for t in tasks]
    else:
        with mp.Pool(args.workers) as pool:
            rows = pool.map(_run_one, tasks)

    df = pd.DataFrame(rows)

    # --- Per-window summary ---
    summary_path = output_root / f"summary_{args.window}.csv"
    df.to_csv(summary_path, index=False)
    print(f"\nFull results: {summary_path}")

    # --- Aggregate across windows: median score, mean return, mean WR ---
    agg = (
        df.groupby("tag")
        .agg(
            category=("category", "first"),
            median_score=("score", "median"),
            mean_return=("return_pct", "mean"),
            mean_wr=("wr_pct", "mean"),
            mean_sharpe=("sharpe", "mean"),
            worst_dd=("max_dd_pct", "min"),
            mean_trail_exits=("trail_exits", "mean"),
            n_windows=("window", "count"),
        )
        .reset_index()
        .sort_values("median_score", ascending=False)
    )
    agg_path = output_root / f"aggregate_{args.window}.csv"
    agg.to_csv(agg_path, index=False)

    # --- Print top 20 ---
    print(f"\n{'':=<100}")
    print(f"TOP 20 by median return×WR score  (across {len(windows)} windows)")
    print(f"{'':=<100}")
    print(f"{'Tag':<40} {'Cat':>3} {'Med-Score':>10} {'MeanRet%':>9} {'MeanWR%':>8} "
          f"{'MeanShrp':>9} {'WorstDD%':>9} {'AvgTrail':>8}")
    print("-" * 100)
    for _, r in agg.head(20).iterrows():
        print(
            f"{r['tag']:<40} {r['category']:>3} {r['median_score']:>10.1f} "
            f"{r['mean_return']:>+9.2f} {r['mean_wr']:>8.1f} "
            f"{r['mean_sharpe']:>9.2f} {r['worst_dd']:>+9.2f} {r['mean_trail_exits']:>8.1f}"
        )

    elapsed_total = time.time() - t_start
    print(f"\nTotal time: {elapsed_total:.0f}s  |  Aggregate: {agg_path}")


if __name__ == "__main__":
    main()
