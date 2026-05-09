#!/usr/bin/env python3
"""Idea #1: replace exit_on_signal=True with exit_on_signal=False so the BC
trigger only ARMS the trail; exit fires when price has additionally given back
trail_stop_pct (0.75%) from the trade's high-water mark.

Hypothesis: this turns combined-BC's accidental selectivity (alignment luck)
into structural selectivity (price has actually moved meaningfully against
the position). Should preserve cross's wins on big runners while letting
hist variants stop firing prematurely on consolidation pauses.

Test on 2026_Q1 and 2024_Q3 (both B-favored, both quarters where BC trailed
single-signal alternatives by 12-35pp).
"""
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
    "2024_Q3": {"start": "2024-07-01", "end": "2024-10-01"},
    "2026_Q1": {"start": "2026-01-01", "end": "2026-04-01"},
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

B_PARAMS = {
    "regime_exhaustion_adx_lookback": 6,
    "regime_exhaustion_adx_drop_pct": 3.5,
    "regime_exhaustion_prev_adx_min": 20.0,
}


def _make_variants() -> list[tuple[str, dict]]:
    v: list[tuple[str, dict]] = []

    # References (exit_on_signal=True — current setup)
    v.append(("REF_baseline_mg1.5", {
        "regime_trail_mode": "strict_exhaustion",
        "regime_exhaustion_stretch_lookback": 3,
        "regime_exhaustion_kc_z_min": 1.75, "regime_exhaustion_bb_z_min": 2.75,
        "regime_exhaustion_adx_lookback": 2,
        "regime_exhaustion_prev_adx_min": 20.0,
        "regime_exhaustion_adx_drop_pct": 2.5,
        "trail_stop_min_gain_pct": 1.5,
        "trail_stop_exit_on_signal": True,
    }))
    v.append(("REF_pureB_mg1.75", {
        "regime_trail_mode": "adx_exhaustion", **B_PARAMS,
        "trail_stop_min_gain_pct": 1.75,
        "trail_stop_exit_on_signal": True,
    }))
    v.append(("REF_BC_cross_mg2.0_signal", {
        "regime_trail_mode": "combined_bc", **B_PARAMS,
        "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
        "profit_exit_macd_signal_period": 9,
        "profit_exit_macd_condition": "cross",
        "profit_exit_macd_histogram_bars": 2,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 2.0,
        "trail_stop_exit_on_signal": True,
    }))
    v.append(("REF_BC_hist_f8_x0_mg2.0_signal", {
        "regime_trail_mode": "combined_bc", **B_PARAMS,
        "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
        "profit_exit_macd_signal_period": 9,
        "profit_exit_macd_condition": "histogram",
        "profit_exit_macd_histogram_bars": 2,
        "profit_exit_macd_histogram_peak_drop_pct": 0.0,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 2.0,
        "trail_stop_exit_on_signal": True,
    }))
    v.append(("REF_BC_hist_f5_x0_mg1.75_signal", {
        "regime_trail_mode": "combined_bc", **B_PARAMS,
        "profit_exit_macd_fast": 5, "profit_exit_macd_slow": 13,
        "profit_exit_macd_signal_period": 5,
        "profit_exit_macd_condition": "histogram",
        "profit_exit_macd_histogram_bars": 2,
        "profit_exit_macd_histogram_peak_drop_pct": 0.0,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 1.75,
        "trail_stop_exit_on_signal": True,
    }))

    # NEW: same configs but with exit_on_signal=False (giveback gate active)
    v.append(("BC_cross_mg2.0_GIVEBACK", {
        "regime_trail_mode": "combined_bc", **B_PARAMS,
        "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
        "profit_exit_macd_signal_period": 9,
        "profit_exit_macd_condition": "cross",
        "profit_exit_macd_histogram_bars": 2,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 2.0,
        "trail_stop_exit_on_signal": False,
    }))
    v.append(("BC_hist_f8_x0_mg2.0_GIVEBACK", {
        "regime_trail_mode": "combined_bc", **B_PARAMS,
        "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
        "profit_exit_macd_signal_period": 9,
        "profit_exit_macd_condition": "histogram",
        "profit_exit_macd_histogram_bars": 2,
        "profit_exit_macd_histogram_peak_drop_pct": 0.0,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 2.0,
        "trail_stop_exit_on_signal": False,
    }))
    v.append(("BC_hist_f5_x0_mg1.75_GIVEBACK", {
        "regime_trail_mode": "combined_bc", **B_PARAMS,
        "profit_exit_macd_fast": 5, "profit_exit_macd_slow": 13,
        "profit_exit_macd_signal_period": 5,
        "profit_exit_macd_condition": "histogram",
        "profit_exit_macd_histogram_bars": 2,
        "profit_exit_macd_histogram_peak_drop_pct": 0.0,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 1.75,
        "trail_stop_exit_on_signal": False,
    }))
    v.append(("BC_hist_f8_x0_mg1.75_GIVEBACK", {
        "regime_trail_mode": "combined_bc", **B_PARAMS,
        "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
        "profit_exit_macd_signal_period": 9,
        "profit_exit_macd_condition": "histogram",
        "profit_exit_macd_histogram_bars": 2,
        "profit_exit_macd_histogram_peak_drop_pct": 0.0,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 1.75,
        "trail_stop_exit_on_signal": False,
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
        "avg_trail_pnl_pct": (sum(trail_pnls) / len(trail_pnls)) if trail_pnls else float("nan"),
    }


def _run_one(args: tuple) -> dict:
    wk, tag, params = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="bc_gb_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"bc_gb_{wk}_{tag}", "version": "bc-giveback",
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
        f"  {wk} {tag:<38} ret={ret:+8.2f}% WR={tm['wr_pct']:5.1f}% "
        f"trl={tm['trail_exits']:3d} {elapsed:.1f}s",
        flush=True,
    )
    return {
        "window": wk, "tag": tag,
        "return_pct": ret, "wr_pct": tm["wr_pct"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "avg_trail_pnl_pct": tm["avg_trail_pnl_pct"],
        "trail_exits": tm["trail_exits"],
    }


def main() -> None:
    tasks = [(wk, tag, p) for wk in WINDOWS for tag, p in VARIANTS]
    print(f"{len(tasks)} runs ({len(VARIANTS)} variants × 2 windows)\n")
    with mp.Pool(6) as pool:
        rows = pool.map(_run_one, tasks)
    df = pd.DataFrame(rows)
    print()
    for wk in WINDOWS:
        sub = df[df.window == wk].sort_values("return_pct", ascending=False)
        print(f"=== {wk} ===")
        print(f"{'Tag':<40} {'Ret%':>8} {'WR%':>6} {'TrlPnL%':>8} {'Trl':>4} {'Shrp':>6} {'WrstDD%':>8}")
        print("-" * 100)
        for _, r in sub.iterrows():
            tp = r.avg_trail_pnl_pct
            tps = f"{tp:+8.3f}" if tp == tp else "     nan"
            print(f"{r.tag:<40} {r.return_pct:>+8.2f} {r.wr_pct:>6.1f} {tps} "
                  f"{r.trail_exits:>4d} {r.sharpe:>6.2f} {r.max_dd_pct:>+8.2f}")
        print()


if __name__ == "__main__":
    main()
