#!/usr/bin/env python3
"""Grid search regime-driven ratio/stop control on top of adaptive ST for ETH 2024."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

INITIAL_CASH = 100000.0
DATA_FILE = "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv"
SYMBOL = "ETH-PERP-INTX"

BASE_PARAMS = {
    "resample_interval": "30min",
    "supertrend_atr_period": 25,
    "supertrend_multiplier": 1.75,
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
}

ADAPTIVE_PARAMS = {
    "adaptive_st_enabled": True,
    "adaptive_st_vol_period": 24,
    "adaptive_st_vol_long_period": 336,
    "adaptive_st_enter_ratio_threshold": 1.0,
    "adaptive_st_exit_ratio_threshold": 0.85,
    "adaptive_st_min_high_bars": 48,  # 24 hours on 30m bars
    "adaptive_st_high_atr_period": 40,
    "adaptive_st_high_multiplier": 2.0,
}

RATIO_SHORT_PERIOD = 4
RATIO_LONG_PERIOD = 336

GRID_PRESETS = {
    "refined": {
        "modes": ["step", "linear"],
        "low_ratio": [0.78, 0.8, 0.82],
        "high_ratio": [0.925, 0.95, 0.975],
        "low_stop": [1.0],
        "high_stop": [2.5, 3.0],
        "occupancy_blend_bars": [12],
    },
    "focused": {
        "modes": ["step", "linear", "occupancy"],
        "low_ratio": [0.75, 0.8],
        "high_ratio": [0.95, 1.0],
        "low_stop": [1.0],
        "high_stop": [2.0, 3.0],
        "occupancy_blend_bars": [12, 24],
    },
    "broad": {
        "modes": ["step", "linear", "occupancy"],
        "low_ratio": [0.7, 0.75, 0.8],
        "high_ratio": [0.9, 1.0, 1.1],
        "low_stop": [0.5, 1.0],
        "high_stop": [2.0, 3.0],
        "occupancy_blend_bars": [6, 12, 24],
    },
}


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
    wr = (wins / exited * 100.0) if exited else float("nan")
    return wr, wins, losses, exited


def build_runs(stage: str) -> list[tuple[str, dict, dict]]:
    preset = GRID_PRESETS[stage]
    low_ratio_grid = preset["low_ratio"]
    high_ratio_grid = preset["high_ratio"]
    low_stop_grid = preset["low_stop"]
    high_stop_grid = preset["high_stop"]
    occupancy_blend_bars = preset["occupancy_blend_bars"]
    modes = preset["modes"]
    runs: list[tuple[str, dict, dict]] = [
        ("baseline", {**BASE_PARAMS}, {"family": "baseline"}),
        ("adaptive_only", {**BASE_PARAMS, **ADAPTIVE_PARAMS}, {"family": "adaptive_only"}),
        (
            "fixed_h1_ref",
            {
                **BASE_PARAMS,
                **ADAPTIVE_PARAMS,
                "flip_vol_ratio_enabled": True,
                "flip_vol_ratio_short_period": RATIO_SHORT_PERIOD,
                "flip_vol_ratio_long_period": RATIO_LONG_PERIOD,
                "flip_vol_ratio_min": 0.75,
                "flip_vol_ratio_safety_stop_pct": 1.0,
            },
            {"family": "fixed_ref", "ratio_min": 0.75, "safety_stop_pct": 1.0},
        ),
        (
            "fixed_h2_ref",
            {
                **BASE_PARAMS,
                **ADAPTIVE_PARAMS,
                "flip_vol_ratio_enabled": True,
                "flip_vol_ratio_short_period": RATIO_SHORT_PERIOD,
                "flip_vol_ratio_long_period": RATIO_LONG_PERIOD,
                "flip_vol_ratio_min": 1.0,
                "flip_vol_ratio_safety_stop_pct": 3.0,
            },
            {"family": "fixed_ref", "ratio_min": 1.0, "safety_stop_pct": 3.0},
        ),
    ]

    for mode in modes:
        for low_ratio in low_ratio_grid:
            for high_ratio in high_ratio_grid:
                if high_ratio < low_ratio:
                    continue
                for low_stop in low_stop_grid:
                    for high_stop in high_stop_grid:
                        if high_stop < low_stop:
                            continue
                        blend_grid = occupancy_blend_bars if mode == "occupancy" else [None]
                        for blend_bars in blend_grid:
                            tag = (
                                f"{mode}_lr{str(low_ratio).replace('.', 'p')}"
                                f"_hr{str(high_ratio).replace('.', 'p')}"
                                f"_ls{str(low_stop).replace('.', 'p')}"
                                f"_hs{str(high_stop).replace('.', 'p')}"
                            )
                            meta = {
                                "family": "regime_dynamic",
                                "mode": mode,
                                "low_ratio_min": low_ratio,
                                "high_ratio_min": high_ratio,
                                "low_stop_pct": low_stop,
                                "high_stop_pct": high_stop,
                            }
                            params = {
                                **BASE_PARAMS,
                                **ADAPTIVE_PARAMS,
                                "flip_vol_ratio_enabled": True,
                                "flip_vol_ratio_short_period": RATIO_SHORT_PERIOD,
                                "flip_vol_ratio_long_period": RATIO_LONG_PERIOD,
                                "flip_vol_ratio_regime_mode": mode,
                                "flip_vol_ratio_regime_low_min": low_ratio,
                                "flip_vol_ratio_regime_high_min": high_ratio,
                                "flip_vol_ratio_regime_low_stop_pct": low_stop,
                                "flip_vol_ratio_regime_high_stop_pct": high_stop,
                            }
                            if blend_bars is not None:
                                tag += f"_bb{blend_bars}"
                                params["flip_vol_ratio_regime_blend_bars"] = blend_bars
                                meta["blend_bars"] = blend_bars
                            runs.append((tag, params, meta))
    return runs


def fmt_row(row: dict) -> str:
    mode = row.get("mode") or "-"
    low_ratio = "-" if row.get("low_ratio_min") is None else f"{row['low_ratio_min']:.2f}"
    high_ratio = "-" if row.get("high_ratio_min") is None else f"{row['high_ratio_min']:.2f}"
    low_stop = "-" if row.get("low_stop_pct") is None else f"{row['low_stop_pct']:.2f}"
    high_stop = "-" if row.get("high_stop_pct") is None else f"{row['high_stop_pct']:.2f}"
    blend = "-" if row.get("blend_bars") is None else str(int(row["blend_bars"]))
    return (
        f"{row['tag']:<40} "
        f"ret={row['total_return_pct']:+8.2f}%  "
        f"WR={row['wr_pct']:5.1f}% ({row['wins']}/{row['exits_with_pnl']})  "
        f"Sh={row['sharpe']:+5.2f}  "
        f"DD={row['max_dd_pct']:+6.1f}%  "
        f"mode={mode:<9}  "
        f"lr={low_ratio:>4} hr={high_ratio:>4}  "
        f"ls={low_stop:>4} hs={high_stop:>4}  "
        f"bb={blend:>2}  trades={row['num_trades']:>4}  {row['elapsed_sec']}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--data-file",
        default=DATA_FILE,
        help="CSV feed file to backtest against.",
    )
    parser.add_argument(
        "--stage",
        choices=sorted(GRID_PRESETS),
        default="focused",
        help="Preset search size. Default keeps the grid around the known H1/H2 winners.",
    )
    args = parser.parse_args()

    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    output_root = REPO / "reports" / "adaptive-regime-ratio-stop-eth" / args.label
    output_root.mkdir(parents=True, exist_ok=True)

    runs = build_runs(args.stage)
    print(
        f"LazySwing adaptive regime ratio/stop grid: {args.label}  "
        f"{args.start} -> {args.end}  stage={args.stage}  ({len(runs)} runs)\n",
        flush=True,
    )

    results: list[dict] = []
    for tag, params, meta in runs:
        run_dir = output_root / tag
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = Config(
            {
                "backtest": {
                    "name": f"LazySwing_adaptive_regime_ratio_stop_{args.label}_{tag}",
                    "version": "adaptive-regime-ratio-stop",
                    "initial_cash": INITIAL_CASH,
                    "start_date": args.start,
                    "end_date": args.end,
                },
                "data_source": {
                    "type": "csv_file",
                    "parser": "coinbase_intx_kline",
                    "params": {
                        "file_path": args.data_file,
                        "symbol": SYMBOL,
                    },
                },
                "strategies": [{"type": "lazy_swing", "params": params}],
            }
        )

        t0 = time.time()
        result = Controller(cfg, output_dir=str(run_dir)).run()[0]
        elapsed = time.time() - t0
        trade_log = TradeLogReader().read(result.trade_log_path)
        stats = compute_stats(trade_log, cfg.initial_cash, 0.05)
        wr, wins, losses, exited = exit_win_rate(trade_log)
        num_trades = int(stats["num_buys"]) + int(stats["num_shorts"])

        row = {
            "tag": tag,
            "total_return_pct": float(stats["total_return"]),
            "final_value": float(result.final_value),
            "sharpe": float(stats["sharpe_ratio"]),
            "max_dd_pct": float(stats["max_drawdown"]),
            "wr_pct": wr,
            "wins": wins,
            "losses": losses,
            "exits_with_pnl": exited,
            "num_trades": num_trades,
            "elapsed_sec": round(elapsed, 1),
            "trade_log_path": result.trade_log_path,
            **meta,
        }
        results.append(row)
        print(fmt_row(row), flush=True)

    results.sort(key=lambda x: x["total_return_pct"], reverse=True)
    summary_path = output_root / "summary.csv"
    pd.DataFrame(results).to_csv(summary_path, index=False)

    print("\n=== Final ranking ===\n", flush=True)
    for row in results:
        print(fmt_row(row), flush=True)
    print(f"\nSaved to {summary_path}", flush=True)


if __name__ == "__main__":
    main()
