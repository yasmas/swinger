#!/usr/bin/env python3
"""Grid search: combined-BC profit-exit for LazySwing.

Concept (per user, 2026-05-08):
  Instead of forcing a foreign trail exit (B or C) on a quarter that favors a
  different category, REQUIRE both B AND C to fire within a small window N
  (5m bars) before allowing a profit-trail exit.  Either signal arms the
  flag; the *other* signal (above min_gain) triggers the exit.  If profit
  retraces below min_gain while armed, cancel the flag (option a).

Pinned signals (chosen as median-best in their category from prior grid):
  B = adx_exhaustion: lb=6, drop=3.5%, prev_adx_min=20
  C = macd_exit:      fast=8, slow=21, signal=9, condition=cross

Sweeps:
  min_gain ∈ {1.75, 2.0}   (per user; 2.25 only if 2.0 beats 1.75)
  N (window 5m bars) ∈ {3, 6, 12, 24}   (15min / 30min / 1h / 2h)
  → 8 combined-BC variants

Baselines for comparison on each quarter:
  baseline_mg{1.0,1.25,1.5,1.75}        (HOF strict_exhaustion, 4 variants)
  B_lb6_d3.5_min20_mg{1.75,2.0}         (pure B, 2 variants)
  C_f8s21g9_cross_mg{1.75,2.0}          (pure C, 2 variants)

Quarters tested (B/C-inclined, 2025_Q3/Q4 excluded per user):
  B: 2024_Q3, 2025_Q1, 2026_Q1
  C: 2024_Q1, 2024_Q2, 2024_Q4, 2025_Q2, 2026_Q2

Usage:
    PYTHONPATH=src python3 scripts/grid_search_lazyswing_combined_BC_exit.py [--workers N]
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import shutil
import sys
import tempfile
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
# Data windows — only B/C-inclined quarters (excluding 2025_Q3 and 2025_Q4)
# ---------------------------------------------------------------------------
_SOURCES = {
    "2023_2024": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
    "2025":      "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
    "2026":      "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
}

# Per-quarter category from docs/lazyswing-profit-exit-grid-results.md
WINDOWS: dict[str, dict] = {
    "2024_Q1": {"src": "2023_2024", "start": "2024-01-01", "end": "2024-04-01", "favored": "C"},
    "2024_Q2": {"src": "2023_2024", "start": "2024-04-01", "end": "2024-07-01", "favored": "C"},
    "2024_Q3": {"src": "2023_2024", "start": "2024-07-01", "end": "2024-10-01", "favored": "B"},
    "2024_Q4": {"src": "2023_2024", "start": "2024-10-01", "end": "2025-01-01", "favored": "C"},
    "2025_Q1": {"src": "2025",      "start": "2025-01-01", "end": "2025-04-01", "favored": "B"},
    "2025_Q2": {"src": "2025",      "start": "2025-04-01", "end": "2025-07-01", "favored": "C"},
    "2026_Q1": {"src": "2026",      "start": "2026-01-01", "end": "2026-04-01", "favored": "B"},
    "2026_Q2": {"src": "2026",      "start": "2026-04-01", "end": "2026-05-08", "favored": "C"},
}

_WARMUP_BARS = 5000
_SLICE_DIR = REPO / "data" / "backtests" / "eth" / "profit_exit_grid_slices"


def _prepare_slices() -> dict[str, str]:
    """Return {window_key: slice_file_path}, creating slices if needed.

    Reuses the same cached slices as grid_search_lazyswing_profit_exit.py.
    """
    _SLICE_DIR.mkdir(parents=True, exist_ok=True)
    source_dfs: dict[str, pd.DataFrame] = {}

    # Only load source files we actually need
    needed_srcs = {win["src"] for win in WINDOWS.values()}
    for key in needed_srcs:
        path = _SOURCES[key]
        full_path = REPO / path
        if not full_path.exists():
            raise FileNotFoundError(f"Source data not found: {full_path}")

    slice_map: dict[str, str] = {}
    for wk, win in WINDOWS.items():
        out_path = _SLICE_DIR / f"{wk}.csv"
        if not out_path.exists():
            if win["src"] not in source_dfs:
                path = _SOURCES[win["src"]]
                print(f"  Loading {path} ...", flush=True)
                df = pd.read_csv(REPO / path)
                df["_dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                source_dfs[win["src"]] = df
            src_df = source_dfs[win["src"]]
            end_dt = pd.Timestamp(win["end"], tz="UTC")
            start_dt = pd.Timestamp(win["start"], tz="UTC")
            mask_before_end = src_df["_dt"] < end_dt
            rows_before_end = src_df[mask_before_end]
            start_idx = rows_before_end[rows_before_end["_dt"] >= start_dt].index
            if len(start_idx) == 0:
                raise ValueError(f"No data found for window {wk} start={win['start']}")
            first_bar_pos = rows_before_end.index.get_loc(start_idx[0])
            warmup_start = max(0, first_bar_pos - _WARMUP_BARS)
            sliced = rows_before_end.iloc[warmup_start:].drop(columns=["_dt"])
            sliced.to_csv(out_path, index=False)
            print(f"  Sliced {wk}: {len(sliced):,} bars → {out_path.name}", flush=True)
        slice_map[wk] = str(out_path)
    return slice_map


# ---------------------------------------------------------------------------
# Shared HOF base params (identical to grid_search_lazyswing_profit_exit.py)
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
    "trail_stop_pct": 0.75,
    "trail_stop_atr_multiple": 0.75,
    "trail_stop_cooldown_bars": 0,
    "trail_stop_reentry_enabled": False,
    "trail_stop_exit_on_signal": True,
}

# Pinned median-best signal params per category (from prior grid)
B_PARAMS = {
    "regime_exhaustion_adx_lookback": 6,
    "regime_exhaustion_adx_drop_pct": 3.5,
    "regime_exhaustion_prev_adx_min": 20.0,
}
C_PARAMS = {
    "profit_exit_macd_fast": 8,
    "profit_exit_macd_slow": 21,
    "profit_exit_macd_signal_period": 9,
    "profit_exit_macd_condition": "cross",
    "profit_exit_macd_histogram_bars": 2,
}


def _make_variants() -> list[tuple[str, dict]]:
    variants: list[tuple[str, dict]] = []

    # --- Baseline strict_exhaustion ---
    for mg in [1.0, 1.25, 1.5, 1.75]:
        variants.append((f"baseline_mg{mg}", {
            "regime_trail_mode": "strict_exhaustion",
            "regime_exhaustion_stretch_lookback": 3,
            "regime_exhaustion_kc_z_min": 1.75,
            "regime_exhaustion_bb_z_min": 2.75,
            "regime_exhaustion_adx_lookback": 2,
            "regime_exhaustion_prev_adx_min": 20.0,
            "regime_exhaustion_adx_drop_pct": 2.5,
            "trail_stop_min_gain_pct": mg,
        }))

    # --- Pure B (median-best) ---
    for mg in [1.75, 2.0]:
        variants.append((f"B_lb6_d3.5_min20_mg{mg}", {
            "regime_trail_mode": "adx_exhaustion",
            **B_PARAMS,
            "trail_stop_min_gain_pct": mg,
        }))

    # --- Pure C (median-best) ---
    for mg in [1.75, 2.0]:
        variants.append((f"C_f8s21g9_cross_mg{mg}", {
            "regime_trail_mode": "macd_exit",
            **C_PARAMS,
            "trail_stop_min_gain_pct": mg,
        }))

    # --- Combined BC ---
    for mg in [1.75, 2.0]:
        for n in [3, 6, 12, 24]:
            variants.append((f"BC_n{n}_mg{mg}", {
                "regime_trail_mode": "combined_bc",
                **B_PARAMS,
                **C_PARAMS,
                "combined_bc_window_bars": n,
                "trail_stop_min_gain_pct": mg,
            }))

    return variants


VARIANTS = _make_variants()


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    all_pnls: list[float] = []
    trail_pnls: list[float] = []

    for _, row in trade_log.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        pnl = details.get("pnl_pct")
        if pnl is None:
            continue
        pnl = float(pnl)
        all_pnls.append(pnl)
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        if details.get("exit_reason") == "regime_trail_stop":
            trail_pnls.append(pnl)

    total = wins + losses
    return {
        "wr_pct": (wins / total * 100.0) if total else float("nan"),
        "wins": wins,
        "losses": losses,
        "avg_pnl_pct": (sum(all_pnls) / len(all_pnls)) if all_pnls else float("nan"),
        "avg_trail_pnl_pct": (sum(trail_pnls) / len(trail_pnls)) if trail_pnls else float("nan"),
        "trail_exits": len(trail_pnls),
    }


def _build_config(window_key: str, tag: str, trail_params: dict, data_file: str) -> Config:
    win = WINDOWS[window_key]
    params = {**BASE_PARAMS, **trail_params}
    return Config({
        "backtest": {
            "name": f"combined_bc_{window_key}_{tag}",
            "version": "combined-bc-grid",
            "initial_cash": 100000.0,
            "start_date": win["start"],
            "end_date": win["end"],
        },
        "data_source": {
            "type": "csv_file",
            "parser": "coinbase_intx_kline",
            "params": {
                "file_path": data_file,
                "symbol": "ETH-PERP-INTX",
            },
        },
        "strategies": [{"type": "lazy_swing", "params": params}],
    })


def _category_of(tag: str) -> str:
    if tag.startswith("BC_"):
        return "BC"
    if tag.startswith("B_"):
        return "B"
    if tag.startswith("C_"):
        return "C"
    return "baseline"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def _run_one(args: tuple) -> dict:
    window_key, tag, trail_params, data_file = args
    tmp_dir = tempfile.mkdtemp(prefix="combined_bc_")
    try:
        cfg = _build_config(window_key, tag, trail_params, data_file)
        t0 = time.time()
        result = Controller(cfg, output_dir=tmp_dir).run()[0]
        elapsed = time.time() - t0
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    stats = compute_stats(tl, cfg.initial_cash, 0.05)
    tm = _trade_metrics(tl)

    ret = float(stats["total_return"])
    wr = tm["wr_pct"]
    score = ret * wr if (ret > 0 and wr == wr) else float("nan")

    row = {
        "window": window_key,
        "favored": WINDOWS[window_key]["favored"],
        "tag": tag,
        "category": _category_of(tag),
        "return_pct": ret,
        "wr_pct": wr,
        "score": score,
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "avg_pnl_pct": tm["avg_pnl_pct"],
        "avg_trail_pnl_pct": tm["avg_trail_pnl_pct"],
        "trail_exits": tm["trail_exits"],
        "wins": tm["wins"],
        "losses": tm["losses"],
        "n_trades": int(stats["num_buys"]) + int(stats["num_shorts"]),
        "elapsed_sec": round(elapsed, 1),
    }
    print(
        f"  {window_key} ({WINDOWS[window_key]['favored']}) {tag:<28} "
        f"ret={ret:+8.2f}% WR={wr:5.1f}% "
        f"avgPnL={tm['avg_pnl_pct']:+6.3f}% trailPnL={tm['avg_trail_pnl_pct']:+6.3f}% "
        f"trail={tm['trail_exits']:3d} {elapsed:.1f}s",
        flush=True,
    )
    return row


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--window", choices=[*WINDOWS.keys(), "all"], default="all")
    args = parser.parse_args()

    print("Preparing quarterly data slices (one-time, cached)...")
    slice_map = _prepare_slices()
    print()

    windows = list(WINDOWS.keys()) if args.window == "all" else [args.window]
    output_root = REPO / "data" / "backtests" / "eth" / "combined_bc_grid"
    output_root.mkdir(parents=True, exist_ok=True)

    tasks = [
        (wk, tag, params, slice_map[wk])
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
    summary_path = output_root / f"summary_{args.window}.csv"
    df.to_csv(summary_path, index=False)

    # ---- Per-quarter table: baseline-best vs B-only vs C-only vs BC ----
    print("\n" + "=" * 130)
    print("Per-quarter results (sorted by return%; favored category in parens)")
    print("=" * 130)
    hdr = (f"{'Window':<10} {'Tag':<28} {'Cat':>4} {'Ret%':>8} {'WR%':>6} "
           f"{'AvgPnL%':>8} {'TrlPnL%':>8} {'Shrp':>6} {'WrstDD%':>8} {'Trl':>4}")

    for wk in windows:
        sub = df[df["window"] == wk].sort_values("return_pct", ascending=False)
        favored = WINDOWS[wk]["favored"]
        print(f"\n--- {wk} (favored: {favored}) ---")
        print(hdr)
        print("-" * 130)
        for _, r in sub.iterrows():
            trail_pnl = r["avg_trail_pnl_pct"]
            trail_str = f"{trail_pnl:+8.3f}" if trail_pnl == trail_pnl else "     nan"
            print(
                f"{r['window']:<10} {r['tag']:<28} {r['category']:>4} "
                f"{r['return_pct']:>+8.2f} {r['wr_pct']:>6.1f} "
                f"{r['avg_pnl_pct']:>+8.3f} {trail_str} "
                f"{r['sharpe']:>6.2f} {r['max_dd_pct']:>+8.2f} "
                f"{r['trail_exits']:>4d}"
            )

    # ---- Cross-quarter aggregate ----
    print("\n" + "=" * 130)
    print("Cross-quarter aggregate (median return × WR score across 8 quarters)")
    print("=" * 130)
    agg = (
        df.groupby("tag")
        .agg(
            category=("category", "first"),
            median_score=("score", "median"),
            mean_return=("return_pct", "mean"),
            median_return=("return_pct", "median"),
            mean_wr=("wr_pct", "mean"),
            mean_sharpe=("sharpe", "mean"),
            worst_dd=("max_dd_pct", "min"),
            mean_trail_exits=("trail_exits", "mean"),
            n_neg=("return_pct", lambda s: int((s < 0).sum())),
        )
        .reset_index()
        .sort_values("median_score", ascending=False, na_position="last")
    )
    agg_path = output_root / f"aggregate_{args.window}.csv"
    agg.to_csv(agg_path, index=False)

    print(f"{'Tag':<28} {'Cat':>4} {'MedSc':>8} {'MedRet%':>8} {'MeanRet%':>9} "
          f"{'MeanWR%':>8} {'Shrp':>6} {'WrstDD%':>8} {'Trl/qtr':>8} {'Negs':>5}")
    print("-" * 110)
    for _, r in agg.iterrows():
        med = r["median_score"]
        med_str = f"{med:>8.1f}" if med == med else "     nan"
        print(
            f"{r['tag']:<28} {r['category']:>4} {med_str} "
            f"{r['median_return']:>+8.2f} {r['mean_return']:>+9.2f} "
            f"{r['mean_wr']:>8.1f} {r['mean_sharpe']:>6.2f} "
            f"{r['worst_dd']:>+8.2f} {r['mean_trail_exits']:>8.1f} "
            f"{r['n_neg']:>5d}"
        )

    elapsed_total = time.time() - t_start
    print(f"\nTotal time: {elapsed_total:.0f}s")
    print(f"Per-run summary:  {summary_path}")
    print(f"Aggregate:        {agg_path}")


if __name__ == "__main__":
    main()
