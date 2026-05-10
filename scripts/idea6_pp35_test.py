#!/usr/bin/env python3
"""Quick test of PP3.5_ER48_32 — middle-ground profit-protect threshold."""
from __future__ import annotations

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
    "flip_protect_min_gain_pct": 3.5,
    "flip_er_gate_period": 48,
    "flip_er_gate_exclude_bars": 0,
    "flip_er_gate_threshold": 0.32,
}


def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    pp_n = er_n = safety_n = 0
    for _, row in trade_log.iterrows():
        d = row.get("details") or {}
        if not isinstance(d, dict):
            continue
        if row["action"] in ("SELL", "COVER"):
            pnl = d.get("pnl_pct")
            if pnl is None: continue
            pnl = float(pnl)
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1
            ex = d.get("exit_reason")
            if ex == "st_flip_protect": pp_n += 1
            elif ex == "st_flip_er_gate": er_n += 1
            elif ex == "st_flip_ratio_safety": safety_n += 1
    total = wins + losses
    return {
        "wr_pct": (wins / total * 100.0) if total else float("nan"),
        "pp_exits": pp_n, "er_exits": er_n, "safety_exits": safety_n,
    }


def _run_one(args: tuple) -> dict:
    wk = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="pp35_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"pp35_{wk}", "version": "pp35-test",
                "initial_cash": 100000.0,
                "start_date": win["start"], "end_date": win["end"],
            },
            "data_source": {
                "type": "csv_file", "parser": "coinbase_intx_kline",
                "params": {"file_path": slice_file, "symbol": "ETH-PERP-INTX"},
            },
            "strategies": [{"type": "lazy_swing", "params": {**BASE_PARAMS, **SHIP_PARAMS}}],
        })
        result = Controller(cfg, output_dir=tmp).run()[0]
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    stats = compute_stats(tl, cfg.initial_cash, 0.05)
    tm = _trade_metrics(tl)
    ret = float(stats["total_return"])
    print(
        f"  {wk}  ret={ret:+8.2f}% WR={tm['wr_pct']:5.1f}% "
        f"pp={tm['pp_exits']:>2d} er={tm['er_exits']:>3d} safe={tm['safety_exits']:>2d}",
        flush=True,
    )
    return {
        "window": wk, "return_pct": ret, "wr_pct": tm["wr_pct"],
        "pp_exits": tm["pp_exits"], "er_exits": tm["er_exits"],
        "safety_exits": tm["safety_exits"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
    }


def main() -> None:
    tasks = list(WINDOWS.keys())
    print(f"Running PP3.5_ER48_32 across {len(tasks)} quarters\n")
    t0 = time.time()
    with mp.Pool(min(8, len(tasks))) as pool:
        rows = pool.map(_run_one, tasks)
    df = pd.DataFrame(rows)

    g = 1.0
    for r in df["return_pct"]: g *= (1.0 + r / 100.0)
    compound = (g - 1.0) * 100.0

    print(f"\nCompound: {compound:+.2f}%")
    print(f"Mean: {df['return_pct'].mean():+.2f}%")
    print(f"Min Q: {df['return_pct'].min():+.2f}%")
    print(f"avgPP/q: {df['pp_exits'].mean():.1f}")
    print(f"avgER/q: {df['er_exits'].mean():.1f}")
    print(f"avgSafe/q: {df['safety_exits'].mean():.1f}")
    print(f"avgWR%: {df['wr_pct'].mean():.2f}")
    print(f"avgSharpe: {df['sharpe'].mean():.3f}")
    print(f"avgMaxDD%: {df['max_dd_pct'].mean():.2f}")
    print(f"\nTotal time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
