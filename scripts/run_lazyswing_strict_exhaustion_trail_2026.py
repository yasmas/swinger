#!/usr/bin/env python3
"""Strict-exhaustion trailing-stop comparison for LazySwing."""

from __future__ import annotations

import sys
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


INITIAL_CASH = 100000.0
SYMBOL = "ETH-PERP-INTX"

PERIODS = [
    {
        "period": "2024h1",
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-01-01",
        "end": "2024-07-01",
    },
    {
        "period": "2025",
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-01-01",
        "end": "2026-01-01",
    },
]

BASE_PARAMS = {
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
}

STRICT_EXHAUSTION_PARAMS = {
    "regime_trail_enabled": True,
    "regime_trail_mode": "strict_exhaustion",
    "regime_momentum_adx_period": 14,
    "regime_momentum_adx_min": 40.0,
    "regime_momentum_er_period": 24,
    "regime_momentum_er_min": 0.40,
    "regime_momentum_adx_delta_bars": 2,
    "regime_momentum_adx_delta_min": 1.0,
    "regime_momentum_vol_period": 24,
    "regime_momentum_vol_long_period": 336,
    "regime_momentum_vol_ratio_max": 1.0,
    "regime_exhaustion_stretch_lookback": 3,
    "regime_exhaustion_kc_z_min": 2.0,
    "regime_exhaustion_bb_z_min": 3.0,
    "regime_exhaustion_adx_lookback": 2,
    "regime_exhaustion_prev_adx_min": 30.0,
    "regime_exhaustion_adx_drop_pct": 2.5,
    "trail_stop_pct": 0.75,
    "trail_stop_atr_multiple": 0.75,
    "trail_stop_min_gain_pct": 1.5,
    "trail_stop_cooldown_bars": 0,
    "trail_stop_reentry_pct": 0.5,
}

RUNS = [
    ("baseline", {}),
    (
        "signal_reentry",
        {
            **STRICT_EXHAUSTION_PARAMS,
            "trail_stop_exit_on_signal": True,
            "trail_stop_reentry_enabled": True,
        },
    ),
    (
        "signal_no_reentry",
        {
            **STRICT_EXHAUSTION_PARAMS,
            "trail_stop_exit_on_signal": True,
            "trail_stop_reentry_enabled": False,
        },
    ),
]


def exit_win_rate(trade_log: pd.DataFrame) -> tuple[float, int, int, int]:
    wins = losses = 0
    for _, row in trade_log.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        pnl = details.get("pnl_pct")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    exited = wins + losses
    return (wins / exited * 100.0 if exited else float("nan")), wins, losses, exited


def trail_stats(trade_log: pd.DataFrame) -> dict:
    trail_exits = 0
    trail_reentries = 0
    strict_exits = 0
    trail_pnls = []
    modes = {}
    for _, row in trade_log.iterrows():
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        if details.get("entry_reason") == "regime_trail_reentry":
            trail_reentries += 1
        if details.get("exit_reason") != "regime_trail_stop":
            continue
        trail_exits += 1
        pnl = details.get("pnl_pct")
        if pnl is not None:
            trail_pnls.append(float(pnl))
        regime = ((details.get("indicators") or {}).get("regime_trail") or {})
        if regime.get("strict_exhaustion"):
            strict_exits += 1
        mode = regime.get("mode", "unknown")
        modes[mode] = modes.get(mode, 0) + 1
    return {
        "trail_exits": trail_exits,
        "trail_reentries": trail_reentries,
        "strict_exhaustion_exits": strict_exits,
        "avg_trail_pnl_pct": sum(trail_pnls) / len(trail_pnls) if trail_pnls else float("nan"),
        "trail_modes": modes,
    }


def build_config(period: dict, tag: str, params_extra: dict) -> Config:
    params = {**BASE_PARAMS, **params_extra}
    return Config(
        {
            "backtest": {
                "name": f"LazySwing_strict_exhaustion_{period['period']}_{tag}",
                "version": "strict-exhaustion-trail",
                "initial_cash": INITIAL_CASH,
                "start_date": period["start"],
                "end_date": period["end"],
            },
            "data_source": {
                "type": "csv_file",
                "parser": "coinbase_intx_kline",
                "params": {
                    "file_path": period["data_file"],
                    "symbol": SYMBOL,
                },
            },
            "strategies": [{"type": "lazy_swing", "params": params}],
        }
    )


def run_one(period: dict, tag: str, params_extra: dict, output_root: Path) -> dict:
    run_dir = output_root / period["period"] / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = build_config(period, tag, params_extra)
    t0 = time.time()
    result = Controller(cfg, output_dir=str(run_dir)).run()[0]
    elapsed = time.time() - t0
    trade_log = TradeLogReader().read(result.trade_log_path)
    stats = compute_stats(trade_log, cfg.initial_cash, 0.05)
    wr, wins, losses, exited = exit_win_rate(trade_log)
    trails = trail_stats(trade_log)
    row = {
        "period": period["period"],
        "tag": tag,
        "total_return_pct": float(stats["total_return"]),
        "final_value": float(result.final_value),
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "wr_pct": wr,
        "wins": wins,
        "losses": losses,
        "exits_with_pnl": exited,
        "num_entries": int(stats["num_buys"]) + int(stats["num_shorts"]),
        "elapsed_sec": round(elapsed, 1),
        "trade_log_path": result.trade_log_path,
        **trails,
        **params_extra,
    }
    print(
        f"{period['period']:<8} {tag:<24} ret={row['total_return_pct']:+8.2f}% "
        f"WR={wr:5.1f}% DD={row['max_dd_pct']:+6.1f}% "
        f"trail={trails['trail_exits']:3d}/{trails['trail_reentries']:3d} "
        f"avg_trail_pnl={trails['avg_trail_pnl_pct']:5.2f}% {row['elapsed_sec']}s",
        flush=True,
    )
    return row


def main() -> None:
    output_root = REPO / "reports" / "lazyswing-strict-exhaustion-signal-selected"
    rows = [
        run_one(period, tag, params, output_root)
        for period in PERIODS
        for tag, params in RUNS
    ]
    df = pd.DataFrame(rows)
    summary_path = output_root / "summary.csv"
    df.to_csv(summary_path, index=False)
    print(f"\nSaved summary: {summary_path}")


if __name__ == "__main__":
    main()
