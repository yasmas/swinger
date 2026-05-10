#!/usr/bin/env python3
"""DMI gate full 8-quarter sweep — 4 ER_DMI winners on remaining quarters,
merged with initial sweep for full picture."""
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

ALL_WINDOWS = {
    "2024_Q1": {"start": "2024-01-01", "end": "2024-04-01"},
    "2024_Q2": {"start": "2024-04-01", "end": "2024-07-01"},
    "2024_Q3": {"start": "2024-07-01", "end": "2024-10-01"},
    "2024_Q4": {"start": "2024-10-01", "end": "2025-01-01"},
    "2025_Q1": {"start": "2025-01-01", "end": "2025-04-01"},
    "2025_Q2": {"start": "2025-04-01", "end": "2025-07-01"},
    "2026_Q1": {"start": "2026-01-01", "end": "2026-04-01"},
    "2026_Q2": {"start": "2026-04-01", "end": "2026-05-08"},
}
REMAINING = ["2024_Q1", "2024_Q3", "2025_Q1", "2026_Q2"]
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
    "flip_protect_min_gain_pct": 4.0,
}

ER_PARAMS = {
    "flip_er_gate_period": 48,
    "flip_er_gate_exclude_bars": 0,
    "flip_er_gate_threshold": 0.32,
}


def _dmi(p: int, g: float) -> dict:
    return {
        "flip_dmi_gate_enabled": True,
        "flip_dmi_gate_period": p,
        "flip_dmi_gate_min_gap_pct": float(g),
    }


VARIANTS = [
    ("PP4_REF", {}),
    ("PP4_ER48_T0.32", {**ER_PARAMS}),
    ("ER_DMI_p42_g25", {**ER_PARAMS, **_dmi(42, 25)}),
    ("ER_DMI_p56_g20", {**ER_PARAMS, **_dmi(56, 20)}),
    ("ER_DMI_p42_g20", {**ER_PARAMS, **_dmi(42, 20)}),
    ("ER_DMI_p28_g30", {**ER_PARAMS, **_dmi(28, 30)}),
]


def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    pp_n = er_n = dmi_n = safety_n = 0
    for _, row in trade_log.iterrows():
        d = row.get("details") or {}
        if not isinstance(d, dict): continue
        if row["action"] in ("SELL", "COVER"):
            pnl = d.get("pnl_pct")
            if pnl is None: continue
            pnl = float(pnl)
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1
            ex = d.get("exit_reason")
            if ex == "st_flip_protect": pp_n += 1
            elif ex == "st_flip_er_gate": er_n += 1
            elif ex == "st_flip_dmi_gate": dmi_n += 1
            elif ex == "st_flip_ratio_safety": safety_n += 1
    total = wins + losses
    return {
        "wr_pct": (wins / total * 100.0) if total else float("nan"),
        "pp_exits": pp_n, "er_exits": er_n, "dmi_exits": dmi_n, "safety_exits": safety_n,
    }


def _run_one(args: tuple) -> dict:
    wk, tag, overrides = args
    win = ALL_WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="dmifull_")
    try:
        params = {**SHIP_PARAMS, **overrides}
        cfg = Config({
            "backtest": {
                "name": f"dmifull_{wk}_{tag}", "version": "dmi-full",
                "initial_cash": 100000.0,
                "start_date": win["start"], "end_date": win["end"],
            },
            "data_source": {
                "type": "csv_file", "parser": "coinbase_intx_kline",
                "params": {"file_path": slice_file, "symbol": "ETH-PERP-INTX"},
            },
            "strategies": [{"type": "lazy_swing", "params": {**BASE_PARAMS, **params}}],
        })
        result = Controller(cfg, output_dir=tmp).run()[0]
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    stats = compute_stats(tl, cfg.initial_cash, 0.05)
    tm = _trade_metrics(tl)
    ret = float(stats["total_return"])
    print(
        f"  {wk} {tag:<22} ret={ret:+8.2f}% WR={tm['wr_pct']:5.1f}% "
        f"pp={tm['pp_exits']:>2d} er={tm['er_exits']:>3d} dmi={tm['dmi_exits']:>3d}",
        flush=True,
    )
    return {
        "window": wk, "tag": tag, "return_pct": ret, "wr_pct": tm["wr_pct"],
        "pp_exits": tm["pp_exits"], "er_exits": tm["er_exits"], "dmi_exits": tm["dmi_exits"],
        "safety_exits": tm["safety_exits"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
    }


def main() -> None:
    tasks = [(wk, tag, ov) for wk in REMAINING for tag, ov in VARIANTS]
    print(f"{len(tasks)} new runs ({len(VARIANTS)} variants × {len(REMAINING)} remaining quarters)\n")
    t0 = time.time()
    n_workers = mp.cpu_count()
    print(f"Using {n_workers} workers\n")
    with mp.Pool(n_workers) as pool:
        new_rows = pool.map(_run_one, tasks)
    new_df = pd.DataFrame(new_rows)

    init_csv = REPO / "data" / "backtests" / "eth" / "idea7_dmi_initial_sweep" / "raw_runs.csv"
    init_df = pd.read_csv(init_csv)
    keep_tags = {tag for tag, _ in VARIANTS}
    init_subset = init_df[init_df["tag"].isin(keep_tags)]

    full = pd.concat([init_subset, new_df], ignore_index=True)
    pivot = full.pivot(index="tag", columns="window", values="return_pct")
    pivot = pivot[list(ALL_WINDOWS.keys())]

    def compound(rs):
        g = 1.0
        for r in rs: g *= (1.0 + r/100.0)
        return (g - 1.0) * 100.0

    pivot["compound_pct"] = pivot.apply(lambda r: compound(r.tolist()), axis=1)
    pivot["mean_pct"] = pivot[list(ALL_WINDOWS.keys())].mean(axis=1)
    pivot["min_pct"] = pivot[list(ALL_WINDOWS.keys())].min(axis=1)
    pivot = pivot.sort_values("compound_pct", ascending=False)

    print("\n" + "=" * 175)
    print("Full 8-quarter results (sorted by compound)")
    print("=" * 175)
    print(f"{'Tag':<22} " +
          "".join(f"{w[5:]:>9}" for w in ALL_WINDOWS) +
          f" {'Compd%':>11} {'Mean%':>9} {'Min%':>9}")
    print("-" * 175)
    for tag, r in pivot.iterrows():
        rets = [r[w] for w in ALL_WINDOWS]
        print(f"{tag:<22} " + "".join(f"{x:>+9.2f}" for x in rets)
              + f" {r.compound_pct:>+11.2f} {r.mean_pct:>+9.2f} {r.min_pct:>+9.2f}")

    print("\n" + "=" * 130)
    print("Profile rollup (per-quarter averages)")
    print("=" * 130)
    print(f"{'Tag':<22} {'avgPP/q':>9} {'avgER/q':>9} {'avgDMI/q':>9} "
          f"{'avgSafe/q':>11} {'avgWR%':>8} {'avgSharpe':>11} {'avgMaxDD%':>11}")
    print("-" * 130)
    for tag in pivot.index:
        sub = full[full["tag"] == tag]
        print(f"{tag:<22} {sub['pp_exits'].mean():>9.1f} "
              f"{sub['er_exits'].mean():>9.1f} "
              f"{sub['dmi_exits'].mean():>9.1f} "
              f"{sub['safety_exits'].mean():>11.1f} "
              f"{sub['wr_pct'].mean():>+8.2f} "
              f"{sub['sharpe'].mean():>+11.3f} "
              f"{sub['max_dd_pct'].mean():>+11.2f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "idea7_dmi_full_sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(out_dir / "compound_per_variant.csv")
    full.to_csv(out_dir / "raw_runs.csv", index=False)
    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
