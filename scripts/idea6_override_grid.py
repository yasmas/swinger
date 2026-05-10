#!/usr/bin/env python3
"""Idea #6 grid — HHLL+DMI AND-rule override, backward vs delayed timing.

Tests three variants on top of ship config (override disabled = REF):
  - REF                 (override disabled, current behaviour)
  - override_backward   (at rejection bar, look back K=4 30m bars)
  - override_delayed    (after rejection, hold up to N=6 30m bars and
                         re-evaluate at each hourly close)

Both override modes require BOTH HH/LL break (price-action) AND DMI
dominance (in flip direction) to fire in the override window before
honouring the rejected flip.

Reports 8-quarter compound vs ship +3,395%.
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

VARIANTS: list[tuple[str, dict]] = [
    ("REF_ship", {}),
    # Backward: at rejection bar, look back 4 30m bars (= rejection bar + 3
    # prior). Matches the [-3, 0] tail of the research window.
    ("override_backward_K4", {
        "flip_override_enabled": True,
        "flip_override_mode": "backward",
        "flip_override_lookback_bars": 4,
        "flip_override_hhll_lookback": 10,
        "flip_override_dmi_period": 14,
    }),
    # Delayed: rejection arms a pending state; on each subsequent hourly
    # close, check if HHLL+DMI both fired in last 4 bars. Up to N=6 bars.
    ("override_delayed_N6", {
        "flip_override_enabled": True,
        "flip_override_mode": "delayed",
        "flip_override_lookback_bars": 4,
        "flip_override_forward_bars": 6,
        "flip_override_hhll_lookback": 10,
        "flip_override_dmi_period": 14,
    }),
]


def _trade_metrics(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    override_n = 0
    safety_n = 0
    rejected_hold_n = 0
    pending_now = 0
    for _, row in trade_log.iterrows():
        d = row.get("details") or {}
        if not isinstance(d, dict):
            continue
        if row["action"] in ("SELL", "COVER"):
            pnl = d.get("pnl_pct")
            if pnl is None:
                continue
            pnl = float(pnl)
            if pnl > 0: wins += 1
            elif pnl < 0: losses += 1
            ex = d.get("exit_reason")
            if ex == "st_flip_override":
                override_n += 1
            elif ex == "st_flip_ratio_safety":
                safety_n += 1
        elif row["action"] == "HOLD":
            r = d.get("reason")
            if r == "st_flip_ratio_rejected_hold":
                rejected_hold_n += 1
            elif r == "flip_override_pending":
                pending_now += 1
    total = wins + losses
    return {
        "wr_pct": (wins / total * 100.0) if total else float("nan"),
        "override_exits": override_n,
        "safety_exits": safety_n,
        "rejected_hold_bars": rejected_hold_n,
        "pending_bars": pending_now,
    }


def _run_one(args: tuple) -> dict:
    wk, tag, overrides = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="idea6g_")
    try:
        params = {**SHIP_PARAMS, **overrides}
        cfg = Config({
            "backtest": {
                "name": f"idea6g_{wk}_{tag}", "version": "idea6-override-grid",
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
        f"ovr={tm['override_exits']:>2d} safe={tm['safety_exits']:>2d} "
        f"rej_hold_bars={tm['rejected_hold_bars']:>4d} pend_bars={tm['pending_bars']:>4d} "
        f"{elapsed:.1f}s",
        flush=True,
    )
    return {
        "window": wk, "tag": tag, "return_pct": ret,
        "wr_pct": tm["wr_pct"],
        "override_exits": tm["override_exits"],
        "safety_exits": tm["safety_exits"],
        "rejected_hold_bars": tm["rejected_hold_bars"],
        "pending_bars": tm["pending_bars"],
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

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_override_grid"
    out_dir.mkdir(parents=True, exist_ok=True)
    pivot.to_csv(out_dir / "compound_per_variant.csv")
    df.to_csv(out_dir / "raw_runs.csv", index=False)

    # Per-variant override / safety profile
    print("\n" + "=" * 130)
    print("Override / safety profile per variant (averaged across 8 quarters)")
    print("=" * 130)
    print(f"{'Tag':<24} {'avgOvr/q':>10} {'avgSafe/q':>10} "
          f"{'avgWR%':>9} {'avgSharpe':>11} {'avgMaxDD%':>11}")
    print("-" * 130)
    for tag in pivot.index:
        sub = df[df["tag"] == tag]
        print(f"{tag:<24} {sub['override_exits'].mean():>10.1f} "
              f"{sub['safety_exits'].mean():>10.1f} "
              f"{sub['wr_pct'].mean():>+9.2f} "
              f"{sub['sharpe'].mean():>+11.3f} "
              f"{sub['max_dd_pct'].mean():>+11.2f}")

    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
