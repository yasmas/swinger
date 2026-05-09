#!/usr/bin/env python3
"""Probe: can we recover 2026_Q1 (B-favored) for combined_bc?

Baseline_mg1.5 captures +19.7%, pure-B captures +40.0%, but BC_n6_mg1.75 only
captures +7.5% because C (MACD cross) doesn't fire in time during B's
ADX-decay window.

Two axes tested simultaneously on 2026_Q1 only:
  - Longer N: {6 control, 16, 24}  → give C more time to confirm
  - Looser C: macd condition × periods
      • cross + f8/s21/g9   (current pinned)
      • histogram + f8/s21/g9
      • cross + f5/s13/g5   (faster MACD)
      • histogram + f5/s13/g5

mg pinned at 1.75. References: baseline_mg1.5, pure-B, pure-C (current).
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

# Reuse cached slice from main grid
SLICE_FILE = str(REPO / "data" / "backtests" / "eth" / "profit_exit_grid_slices" / "2026_Q1.csv")
WIN = {"start": "2026-01-01", "end": "2026-04-01"}

# Same HOF base as main grid
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
    "hmacd_fast": 24, "hmacd_slow": 51, "hmacd_signal": 12,
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

B_PARAMS = {
    "regime_exhaustion_adx_lookback": 6,
    "regime_exhaustion_adx_drop_pct": 3.5,
    "regime_exhaustion_prev_adx_min": 20.0,
}


def _build_variants() -> list[tuple[str, dict]]:
    variants: list[tuple[str, dict]] = []

    # References
    variants.append(("REF_baseline_mg1.5", {
        "regime_trail_mode": "strict_exhaustion",
        "regime_exhaustion_stretch_lookback": 3,
        "regime_exhaustion_kc_z_min": 1.75,
        "regime_exhaustion_bb_z_min": 2.75,
        "regime_exhaustion_adx_lookback": 2,
        "regime_exhaustion_prev_adx_min": 20.0,
        "regime_exhaustion_adx_drop_pct": 2.5,
        "trail_stop_min_gain_pct": 1.5,
    }))
    variants.append(("REF_pureB_mg1.75", {
        "regime_trail_mode": "adx_exhaustion",
        **B_PARAMS,
        "trail_stop_min_gain_pct": 1.75,
    }))
    variants.append(("REF_pureC_f8s21g9_cross_mg1.75", {
        "regime_trail_mode": "macd_exit",
        "profit_exit_macd_fast": 8, "profit_exit_macd_slow": 21,
        "profit_exit_macd_signal_period": 9,
        "profit_exit_macd_condition": "cross",
        "profit_exit_macd_histogram_bars": 2,
        "trail_stop_min_gain_pct": 1.75,
    }))

    # Sub-grid: N × C-loosening
    c_configs = [
        # (label, fast, slow, sig, cond)
        ("f8s21g9_cross",     8, 21, 9, "cross"),      # current pinned
        ("f8s21g9_hist",      8, 21, 9, "histogram"),
        ("f5s13g5_cross",     5, 13, 5, "cross"),      # faster
        ("f5s13g5_hist",      5, 13, 5, "histogram"),  # faster + histogram
    ]
    for n, (clbl, f, s, sg, cond) in product([6, 16, 24], c_configs):
        tag = f"BC_n{n}_{clbl}_mg1.75"
        variants.append((tag, {
            "regime_trail_mode": "combined_bc",
            **B_PARAMS,
            "profit_exit_macd_fast": f,
            "profit_exit_macd_slow": s,
            "profit_exit_macd_signal_period": sg,
            "profit_exit_macd_condition": cond,
            "profit_exit_macd_histogram_bars": 2,
            "combined_bc_window_bars": n,
            "trail_stop_min_gain_pct": 1.75,
        }))

    return variants


VARIANTS = _build_variants()


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
        "trail_exits": len(trail_pnls),
        "avg_pnl_pct": (sum(all_pnls) / len(all_pnls)) if all_pnls else float("nan"),
        "avg_trail_pnl_pct": (sum(trail_pnls) / len(trail_pnls)) if trail_pnls else float("nan"),
    }


def _run_one(args: tuple) -> dict:
    tag, trail_params = args
    tmp = tempfile.mkdtemp(prefix="bc_q1_probe_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"bc_q1_probe_{tag}",
                "version": "bc-q1-probe",
                "initial_cash": 100000.0,
                "start_date": WIN["start"],
                "end_date": WIN["end"],
            },
            "data_source": {
                "type": "csv_file",
                "parser": "coinbase_intx_kline",
                "params": {"file_path": SLICE_FILE, "symbol": "ETH-PERP-INTX"},
            },
            "strategies": [{"type": "lazy_swing", "params": {**BASE_PARAMS, **trail_params}}],
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
        f"  {tag:<38} ret={ret:+7.2f}% WR={tm['wr_pct']:5.1f}% "
        f"trailPnL={tm['avg_trail_pnl_pct']:+6.3f}% trail={tm['trail_exits']:3d} "
        f"Sh={float(stats['sharpe_ratio']):4.2f} {elapsed:.1f}s",
        flush=True,
    )
    return {
        "tag": tag,
        "return_pct": ret,
        "wr_pct": tm["wr_pct"],
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "avg_pnl_pct": tm["avg_pnl_pct"],
        "avg_trail_pnl_pct": tm["avg_trail_pnl_pct"],
        "trail_exits": tm["trail_exits"],
    }


def main() -> None:
    print(f"Probing 2026_Q1: {len(VARIANTS)} variants\n")
    with mp.Pool(4) as pool:
        rows = pool.map(_run_one, VARIANTS)
    df = pd.DataFrame(rows).sort_values("return_pct", ascending=False)
    out = REPO / "data" / "backtests" / "eth" / "combined_bc_grid" / "probe_2026_q1.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print()
    print("=" * 110)
    print("2026_Q1 results, sorted by return%")
    print("=" * 110)
    print(f"{'Tag':<40} {'Ret%':>8} {'WR%':>6} {'TrlPnL%':>8} {'Trl':>4} {'Shrp':>6} {'WrstDD%':>8}")
    print("-" * 110)
    for _, r in df.iterrows():
        tp = r["avg_trail_pnl_pct"]
        tp_s = f"{tp:+8.3f}" if tp == tp else "     nan"
        print(
            f"{r['tag']:<40} {r['return_pct']:>+8.2f} {r['wr_pct']:>6.1f} "
            f"{tp_s} {r['trail_exits']:>4d} {r['sharpe']:>6.2f} {r['max_dd_pct']:>+8.2f}"
        )
    print(f"\nResults: {out}")


if __name__ == "__main__":
    main()
