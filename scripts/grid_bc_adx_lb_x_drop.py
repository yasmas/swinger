#!/usr/bin/env python3
"""Sweep adx_lookback × adx_drop_pct on top of the current ship config.

Variants:
  adx_lookback ∈ {8, 12, 16}      (4h, 6h, 8h on 30m bars)
  adx_drop_pct ∈ {2.5, 3.5, 5.0}
  = 9 BC variants × 8 quarters = 72 runs
"""
from __future__ import annotations

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

WINDOWS = {
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
}


def _bc(lookback: int, drop: float) -> dict:
    return {
        "regime_trail_mode": "combined_bc",
        "regime_exhaustion_adx_lookback": lookback,
        "regime_exhaustion_adx_drop_pct": drop,
        "regime_exhaustion_prev_adx_min": 20.0,
        "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
        "profit_exit_macd_signal_period": 9,
        "profit_exit_macd_condition": "cross",
        "profit_exit_macd_histogram_bars": 2,
        "combined_bc_window_bars": 6,
        "trail_stop_min_gain_pct": 2.0,
        "trail_stop_exit_on_signal": False,
        "trail_stop_giveback_window_bars": 2,
    }


VARIANTS: list[tuple[str, dict]] = [
    (f"lb{lb}_drop{drop}", _bc(lb, drop))
    for lb, drop in product([8, 12, 16], [2.5, 3.5, 5.0])
]


def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
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
        if pnl > 0: wins += 1
        elif pnl < 0: losses += 1
        if d.get("exit_reason") == "regime_trail_stop":
            trail_pnls.append(pnl)
    total = wins + losses
    return {
        "wr_pct": (wins / total * 100.0) if total else float("nan"),
        "trail_exits": len(trail_pnls),
    }


def _run_one(args: tuple) -> dict:
    wk, tag, params = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="bc_lbdrop_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"bc_lbdrop_{wk}_{tag}", "version": "bc-lb-drop",
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
        f"  {wk} ({win['favored']}) {tag:<18} ret={ret:+8.2f}% "
        f"WR={tm['wr_pct']:5.1f}% trl={tm['trail_exits']:3d} {elapsed:.1f}s",
        flush=True,
    )
    return {
        "window": wk, "favored": win["favored"], "tag": tag,
        "return_pct": ret, "wr_pct": tm["wr_pct"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "trail_exits": tm["trail_exits"],
    }


def main() -> None:
    tasks = [(wk, tag, p) for wk in WINDOWS for tag, p in VARIANTS]
    print(f"{len(tasks)} runs ({len(VARIANTS)} variants × {len(WINDOWS)} windows)\n")
    t0 = time.time()
    with mp.Pool(7) as pool:
        rows = pool.map(_run_one, tasks)
    df = pd.DataFrame(rows)

    def compound(rs):
        g = 1.0
        for r in rs: g *= (1.0 + r/100.0)
        return (g - 1.0) * 100.0

    pivot = df.pivot(index="tag", columns="window", values="return_pct")
    pivot = pivot[list(WINDOWS.keys())]
    pivot["compound_pct"] = pivot.apply(lambda r: compound(r.tolist()), axis=1)
    pivot["mean_pct"] = pivot.iloc[:, :-1].mean(axis=1)
    pivot["min_pct"] = pivot.iloc[:, :-2].min(axis=1)
    pivot = pivot.sort_values("compound_pct", ascending=False)

    print("\n" + "=" * 150)
    print("Compound across 8 quarters (sorted desc)")
    print("=" * 150)
    print(f"{'Tag':<20} " +
          "".join(f"{w[5:]:>8}" for w in WINDOWS) +
          f" {'Compd%':>10} {'Mean%':>8} {'Min%':>8}")
    print("-" * 150)
    for tag, r in pivot.iterrows():
        rets = [r[w] for w in WINDOWS]
        print(f"{tag:<20} " + "".join(f"{x:>+8.2f}" for x in rets)
              + f" {r.compound_pct:>+10.2f} {r.mean_pct:>+8.2f} {r.min_pct:>+8.2f}")

    out = REPO / "data" / "backtests" / "eth" / "combined_bc_grid" / "adx_lb_x_drop_8q.csv"
    pivot.to_csv(out)
    print(f"\nResults: {out}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
