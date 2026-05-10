#!/usr/bin/env python3
"""Phase 3 — pullback re-entry on top of BC-relaxation (8 quarters).

Tests the user's hypothesis: after a trail exit, if ST is still aligned with
the original direction, wait for price to retrace (lower for long / higher
for short) by `trail_stop_reentry_pct` and re-enter at a better cost basis.

Stacks on top of two BC-relaxation bases:
  - BASE_USER:        full user spec (4/7/gb50) — Phase 2 worst regression
  - BASE_ONLY_BYPASS: 7%/gb50 only — Phase 2 best (least bad) regression

Compares 8-quarter compound vs current ship REF (+3,395%) and the bare
BC-relaxation bases.
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

# Two BC-relaxation bases to stack pullback re-entry on top of.
BC_BASES: dict[str, dict] = {
    "USER":     {"bc_single_signal_gain_pct": 4.0, "bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.50},
    "BYPASS7":  {"bc_bypass_gain_pct": 7.0, "bc_bypass_giveback_pct": 0.50},
}

# Pullback re-entry configs to test (rentry_pct, max_wait_bars, cooldown_bars).
# 5m bars: 12 = 1h, 48 = 4h, 144 = 12h, 288 = 24h.
PULLBACKS: list[tuple[str, float, int, int]] = [
    # tag,             rentry_pct, max_wait, cooldown
    ("pb_off",         0.0,        0,        0),  # no re-entry (= base only)
    ("pb_25_4h",       0.25,       48,       0),
    ("pb_50_4h",       0.50,       48,       0),
    ("pb_75_4h",       0.75,       48,       0),
    ("pb_50_12h",      0.50,       144,      0),
    ("pb_50_24h",      0.50,       288,      0),
    ("pb_50_unl",      0.50,       0,        0),  # unlimited wait
    ("pb_100_4h",      1.00,       48,       0),
    ("pb_50_4h_cd12",  0.50,       48,       12),  # 1h cooldown then look for pullback
]


def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    trail_pnls: list[float] = []
    pullback_reentries = 0
    for _, row in trade_log.iterrows():
        d = row.get("details")
        if not isinstance(d, dict):
            continue
        if row["action"] in ("BUY", "SHORT"):
            if d.get("entry_reason") == "regime_trail_reentry":
                pullback_reentries += 1
            continue
        if row["action"] not in ("SELL", "COVER"):
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
        "reentries": pullback_reentries,
    }


def _run_one(args: tuple) -> dict:
    wk, base_tag, pb_tag, base_overrides, pb_overrides = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="phase3_")
    tag = f"{base_tag}+{pb_tag}"
    try:
        params = {**SHIP_PARAMS, **base_overrides, **pb_overrides}
        cfg = Config({
            "backtest": {
                "name": f"phase3_{wk}_{tag}", "version": "phase3-pullback-reentry",
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
        f"  {wk} {tag:<28} ret={ret:+8.2f}% WR={tm['wr_pct']:5.1f}% "
        f"trl={tm['trail_exits']:2d} re={tm['reentries']:2d} "
        f"trlAvg={tm['trail_avg_pnl']:+5.2f}% {elapsed:.1f}s",
        flush=True,
    )
    return {
        "window": wk, "tag": tag, "return_pct": ret,
        "wr_pct": tm["wr_pct"], "trail_exits": tm["trail_exits"],
        "reentries": tm["reentries"],
        "trail_avg_pnl": tm["trail_avg_pnl"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
    }


def main() -> None:
    # Add REF (no BC relaxation, no re-entry) for baseline.
    tasks = [(wk, "REF", "ship", {}, {}) for wk in WINDOWS]
    for base_tag, base_overrides in BC_BASES.items():
        for pb_tag, rentry_pct, max_wait, cooldown in PULLBACKS:
            if pb_tag == "pb_off":
                pb_overrides = {}
            else:
                pb_overrides = {
                    "trail_stop_reentry_enabled": True,
                    "trail_stop_reentry_mode": "pullback",
                    "trail_stop_reentry_pct": rentry_pct,
                    "trail_stop_reentry_max_wait_bars": max_wait,
                    "trail_stop_cooldown_bars": cooldown,
                }
            for wk in WINDOWS:
                tasks.append((wk, base_tag, pb_tag, base_overrides, pb_overrides))

    print(f"{len(tasks)} runs ({len(tasks)//len(WINDOWS)} variants × {len(WINDOWS)} windows)\n")
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

    print("\n" + "=" * 175)
    print("Compound across 8 quarters (sorted desc)")
    print("=" * 175)
    print(f"{'Tag':<28} " +
          "".join(f"{w[5:]:>9}" for w in WINDOWS) +
          f" {'Compd%':>11} {'Mean%':>9} {'Min%':>9}")
    print("-" * 175)
    for tag, r in pivot.iterrows():
        rets = [r[w] for w in WINDOWS]
        print(f"{tag:<28} " + "".join(f"{x:>+9.2f}" for x in rets)
              + f" {r.compound_pct:>+11.2f} {r.mean_pct:>+9.2f} {r.min_pct:>+9.2f}")

    out_dir = REPO / "data" / "backtests" / "eth" / "phase3_pullback_reentry"
    out_dir.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(out_dir / "compound_per_variant.csv")
    df.to_csv(out_dir / "raw_runs.csv", index=False)

    # Profile per variant.
    print("\n" + "=" * 130)
    print("Trail/re-entry profile per variant (averaged across 8 quarters)")
    print("=" * 130)
    print(f"{'Tag':<28} {'avgTrl/q':>10} {'avgRE/q':>10} {'avgTrlPnL%':>12} "
          f"{'avgWR%':>9} {'avgSharpe':>11} {'avgMaxDD%':>11}")
    print("-" * 130)
    for tag in pivot.index:
        sub = df[df["tag"] == tag]
        print(f"{tag:<28} {sub['trail_exits'].mean():>10.1f} "
              f"{sub['reentries'].mean():>10.1f} "
              f"{sub['trail_avg_pnl'].mean():>+12.3f} "
              f"{sub['wr_pct'].mean():>+9.2f} "
              f"{sub['sharpe'].mean():>+11.3f} "
              f"{sub['max_dd_pct'].mean():>+11.2f}")

    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
