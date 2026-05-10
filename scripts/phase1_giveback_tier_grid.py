#!/usr/bin/env python3
"""Phase 1 — profit-tier giveback grid (8 quarters).

Tests several `trail_stop_giveback_tiers` tables on top of the current ship
config. Each tier entry is [gain_floor_pct, pct, atr_mult]; above the floor,
the tier's (pct, atr_mult) override `trail_stop_pct` / `trail_stop_atr_multiple`.

Compares 8-quarter compound vs current ship (REF, no tiers).
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

# Mirrors phase0_giveback_scanner.py / grid_bc_giveback_window_8q.py.
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
}

# Each tier: [gain_floor_pct, pct, atr_mult]. Lookups pick the highest floor
# whose threshold the current peak gain meets. Below all floors → falls back
# to BASE_PARAMS trail_stop_pct / trail_stop_atr_multiple (0.75/0.75).
VARIANTS: list[tuple[str, list[list[float]]]] = [
    ("REF_ship",          []),
    # Gentle: tighten gradually, keep ATR contribution.
    ("T1_gentle",         [[3, 0.50, 0.50], [5, 0.40, 0.40], [8, 0.30, 0.30]]),
    ("T2_medium",         [[3, 0.50, 0.50], [5, 0.35, 0.35], [8, 0.25, 0.25]]),
    ("T3_aggressive",     [[3, 0.40, 0.40], [5, 0.30, 0.30], [8, 0.20, 0.20]]),
    ("T4_very_agg",       [[3, 0.35, 0.35], [5, 0.25, 0.25], [8, 0.15, 0.15]]),
    # Disable ATR multiple at high profit (pct-only floor at higher tiers).
    ("T5_pct_only_hi",    [[3, 0.50, 0.0],  [5, 0.35, 0.0],  [8, 0.25, 0.0]]),
    # Wider giveback at the low tier (avoid premature exit on noisy 2-3% peaks),
    # then sharply tighten.
    ("T6_wide_lo_tight_hi", [[3, 0.75, 0.75], [5, 0.35, 0.35], [8, 0.20, 0.20]]),
    # Tight-everywhere: only differs from REF in capping the giveback at lower
    # values across the board (no ATR scaling once we're in profit at all).
    ("T7_flat_tight",     [[2, 0.50, 0.0],  [5, 0.35, 0.0],  [8, 0.25, 0.0]]),
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
        "trail_avg_pnl": (sum(trail_pnls) / len(trail_pnls)) if trail_pnls else float("nan"),
    }


def _run_one(args: tuple) -> dict:
    wk, tag, tiers = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="phase1_")
    try:
        params = {**SHIP_PARAMS, "trail_stop_giveback_tiers": tiers}
        cfg = Config({
            "backtest": {
                "name": f"phase1_{wk}_{tag}", "version": "phase1-tier-grid",
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
        f"  {wk} {tag:<22} ret={ret:+8.2f}% WR={tm['wr_pct']:5.1f}% "
        f"trl={tm['trail_exits']:3d} trlAvg={tm['trail_avg_pnl']:+5.2f}% "
        f"{elapsed:.1f}s",
        flush=True,
    )
    return {
        "window": wk, "tag": tag, "return_pct": ret,
        "wr_pct": tm["wr_pct"], "trail_exits": tm["trail_exits"],
        "trail_avg_pnl": tm["trail_avg_pnl"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
    }


def main() -> None:
    tasks = [(wk, tag, tiers) for wk in WINDOWS for tag, tiers in VARIANTS]
    print(f"{len(tasks)} runs ({len(VARIANTS)} variants × {len(WINDOWS)} windows)\n")
    t0 = time.time()
    with mp.Pool(8) as pool:
        rows = pool.map(_run_one, tasks)
    df = pd.DataFrame(rows)

    def compound(rs):
        g = 1.0
        for r in rs: g *= (1.0 + r/100.0)
        return (g - 1.0) * 100.0

    pivot_ret = df.pivot(index="tag", columns="window", values="return_pct")
    pivot_ret = pivot_ret[list(WINDOWS.keys())]
    pivot_ret["compound_pct"] = pivot_ret.apply(lambda r: compound(r.tolist()), axis=1)
    pivot_ret["mean_pct"] = pivot_ret[list(WINDOWS.keys())].mean(axis=1)
    pivot_ret["min_pct"] = pivot_ret[list(WINDOWS.keys())].min(axis=1)
    pivot_ret = pivot_ret.sort_values("compound_pct", ascending=False)

    print("\n" + "=" * 165)
    print("Compound across 8 quarters (sorted desc)")
    print("=" * 165)
    print(f"{'Tag':<22} " +
          "".join(f"{w[5:]:>9}" for w in WINDOWS) +
          f" {'Compd%':>11} {'Mean%':>9} {'Min%':>9}")
    print("-" * 165)
    for tag, r in pivot_ret.iterrows():
        rets = [r[w] for w in WINDOWS]
        print(f"{tag:<22} " + "".join(f"{x:>+9.2f}" for x in rets)
              + f" {r.compound_pct:>+11.2f} {r.mean_pct:>+9.2f} {r.min_pct:>+9.2f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "phase1_tier_grid"
    out_dir.mkdir(parents=True, exist_ok=True)
    pivot_ret.to_csv(out_dir / "compound_per_variant.csv")
    df.to_csv(out_dir / "raw_runs.csv", index=False)

    # Trail-exit summary per variant (averaged across windows).
    print("\n" + "=" * 110)
    print("Trail-exit profile per variant (averaged across 8 quarters)")
    print("=" * 110)
    print(f"{'Tag':<22} {'avgTrl/q':>10} {'avgTrlPnL%':>12} {'avgWR%':>9} {'avgSharpe':>11} {'avgMaxDD%':>11}")
    print("-" * 110)
    for tag in pivot_ret.index:
        sub = df[df["tag"] == tag]
        print(f"{tag:<22} {sub['trail_exits'].mean():>10.1f} "
              f"{sub['trail_avg_pnl'].mean():>+12.3f} "
              f"{sub['wr_pct'].mean():>+9.2f} "
              f"{sub['sharpe'].mean():>+11.3f} "
              f"{sub['max_dd_pct'].mean():>+11.2f}")

    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
