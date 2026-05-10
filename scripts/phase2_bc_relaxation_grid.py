#!/usr/bin/env python3
"""Phase 2 — BC-relaxation profit-tier grid (8 quarters).

Tests three thresholds on top of the current ship config:
  - bc_single_signal_gain_pct: at this peak gain, single B or C triggers
  - bc_bypass_gain_pct: at this peak gain, BC is bypassed entirely
  - bc_bypass_giveback_pct: required giveback for bypass-tier exit

User's nominated config:
  gain ∈ [2%, 4%): full B AND C   (current)
  gain ∈ [4%, 7%): single B or C
  gain ≥ 7%:        any 0.5% giveback exits, BC bypassed

Reports 8-quarter compound vs current ship REF.
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

# (tag, single_signal_gain, bypass_gain, bypass_giveback)
# 1e9 disables that tier.
VARIANTS: list[tuple[str, dict]] = [
    ("REF_ship",                {}),
    # User's spec: 4% / 7% / 0.5%
    ("user_4_7_gb50",  {"bc_single_signal_gain_pct": 4.0, "bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.50}),
    # Mechanism isolations
    ("only_single_4",  {"bc_single_signal_gain_pct": 4.0}),
    ("only_bypass_7_gb50",  {"bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.50}),
    # Sensitivity around bypass threshold
    ("bypass_5_gb50",  {"bc_single_signal_gain_pct": 4.0, "bc_bypass_gain_pct": 5.0, "bc_bypass_giveback_pct": 0.50}),
    ("bypass_6_gb50",  {"bc_single_signal_gain_pct": 4.0, "bc_bypass_gain_pct": 6.0, "bc_bypass_giveback_pct": 0.50}),
    ("bypass_8_gb50",  {"bc_single_signal_gain_pct": 4.0, "bc_bypass_gain_pct": 8.0, "bc_bypass_giveback_pct": 0.50}),
    # Sensitivity around bypass giveback floor
    ("bypass_7_gb35",  {"bc_single_signal_gain_pct": 4.0, "bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.35}),
    ("bypass_7_gb75",  {"bc_single_signal_gain_pct": 4.0, "bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.75}),
    # Sensitivity around single threshold
    ("single_3_byp7_gb50",  {"bc_single_signal_gain_pct": 3.0, "bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.50}),
    ("single_5_byp7_gb50",  {"bc_single_signal_gain_pct": 5.0, "bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.50}),
]


def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    trail_pnls: list[float] = []
    bypass_exits = 0
    single_exits = 0
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
            tier = d.get("combined_bc_tier")
            if tier == "bypass": bypass_exits += 1
            elif tier == "single": single_exits += 1
    total = wins + losses
    return {
        "wr_pct": (wins / total * 100.0) if total else float("nan"),
        "trail_exits": len(trail_pnls),
        "trail_avg_pnl": (sum(trail_pnls) / len(trail_pnls)) if trail_pnls else float("nan"),
        "bypass_exits": bypass_exits,
        "single_exits": single_exits,
    }


def _run_one(args: tuple) -> dict:
    wk, tag, overrides = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="phase2_")
    try:
        params = {**SHIP_PARAMS, **overrides}
        cfg = Config({
            "backtest": {
                "name": f"phase2_{wk}_{tag}", "version": "phase2-bc-relax",
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
        f"  {wk} {tag:<24} ret={ret:+8.2f}% WR={tm['wr_pct']:5.1f}% "
        f"trl={tm['trail_exits']:2d} (byp={tm['bypass_exits']:2d} sng={tm['single_exits']:2d}) "
        f"trlAvg={tm['trail_avg_pnl']:+5.2f}% {elapsed:.1f}s",
        flush=True,
    )
    return {
        "window": wk, "tag": tag, "return_pct": ret,
        "wr_pct": tm["wr_pct"], "trail_exits": tm["trail_exits"],
        "bypass_exits": tm["bypass_exits"], "single_exits": tm["single_exits"],
        "trail_avg_pnl": tm["trail_avg_pnl"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
    }


def main() -> None:
    tasks = [(wk, tag, ov) for wk in WINDOWS for tag, ov in VARIANTS]
    print(f"{len(tasks)} runs ({len(VARIANTS)} variants × {len(WINDOWS)} windows)\n")
    t0 = time.time()
    with mp.Pool(8) as pool:
        rows = pool.map(_run_one, tasks)
    df = pd.DataFrame(rows)

    def compound(rs):
        g = 1.0
        for r in rs: g *= (1.0 + r/100.0)
        return (g - 1.0) * 100.0

    pivot = df.pivot(index="tag", columns="window", values="return_pct")
    pivot = pivot[list(WINDOWS.keys())]
    pivot["compound_pct"] = pivot.apply(lambda r: compound(r.tolist()), axis=1)
    pivot["mean_pct"] = pivot[list(WINDOWS.keys())].mean(axis=1)
    pivot["min_pct"] = pivot[list(WINDOWS.keys())].min(axis=1)
    pivot = pivot.sort_values("compound_pct", ascending=False)

    print("\n" + "=" * 165)
    print("Compound across 8 quarters (sorted desc)")
    print("=" * 165)
    print(f"{'Tag':<24} " +
          "".join(f"{w[5:]:>9}" for w in WINDOWS) +
          f" {'Compd%':>11} {'Mean%':>9} {'Min%':>9}")
    print("-" * 165)
    for tag, r in pivot.iterrows():
        rets = [r[w] for w in WINDOWS]
        print(f"{tag:<24} " + "".join(f"{x:>+9.2f}" for x in rets)
              + f" {r.compound_pct:>+11.2f} {r.mean_pct:>+9.2f} {r.min_pct:>+9.2f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "phase2_bc_relax"
    out_dir.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(out_dir / "compound_per_variant.csv")
    df.to_csv(out_dir / "raw_runs.csv", index=False)

    # Trail-exit profile per variant.
    print("\n" + "=" * 130)
    print("Trail-exit profile per variant (averaged across 8 quarters)")
    print("=" * 130)
    print(f"{'Tag':<24} {'avgTrl/q':>10} {'avgByp/q':>10} {'avgSng/q':>10} "
          f"{'avgTrlPnL%':>12} {'avgWR%':>9} {'avgSharpe':>11} {'avgMaxDD%':>11}")
    print("-" * 130)
    for tag in pivot.index:
        sub = df[df["tag"] == tag]
        print(f"{tag:<24} {sub['trail_exits'].mean():>10.1f} "
              f"{sub['bypass_exits'].mean():>10.1f} "
              f"{sub['single_exits'].mean():>10.1f} "
              f"{sub['trail_avg_pnl'].mean():>+12.3f} "
              f"{sub['wr_pct'].mean():>+9.2f} "
              f"{sub['sharpe'].mean():>+11.3f} "
              f"{sub['max_dd_pct'].mean():>+11.2f}")

    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
