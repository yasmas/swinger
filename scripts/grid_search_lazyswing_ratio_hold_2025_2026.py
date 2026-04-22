#!/usr/bin/env python3
"""Compact validation sweep for the LazySwing ratio-hold filter on 2025 and 2026 YTD.

Uses the 30m HOF baseline (ST 25 / 1.75) and tests a short list of ratio/stop
values centered around the 2024 H1 and H2 winners.
"""

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
SYMBOL = "ETH-PERP-INTX"
SHORT_PERIODS = [4]
RATIO_MIN_GRID = [0.65, 0.75, 0.85, 0.90, 1.00, 1.10]
SAFETY_STOPS_PCT = [0.5, 1.0, 1.5, 2.0, 3.0]
LONG_PERIOD = 336

PERIODS = {
    "2025": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-01-01",
        "end": "2025-12-31",
    },
    "2026_ytd": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
        "start": "2026-01-01",
        "end": "2026-04-17",
    },
}

BASE_PARAMS = {
    "resample_interval": "30min",
    "supertrend_atr_period": 25,
    "supertrend_multiplier": 1.75,
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
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


def build_runs() -> list[tuple[str, dict]]:
    runs = [("baseline", {**BASE_PARAMS, "flip_vol_ratio_enabled": False})]
    for short_period in SHORT_PERIODS:
        for ratio_min in RATIO_MIN_GRID:
            for stop_pct in SAFETY_STOPS_PCT:
                tag = (
                    f"n{short_period}_r{str(ratio_min).replace('.', 'p')}"
                    f"_s{str(stop_pct).replace('.', 'p')}"
                )
                params = {
                    **BASE_PARAMS,
                    "flip_vol_ratio_enabled": True,
                    "flip_vol_ratio_short_period": short_period,
                    "flip_vol_ratio_long_period": LONG_PERIOD,
                    "flip_vol_ratio_min": ratio_min,
                    "flip_vol_ratio_safety_stop_pct": stop_pct,
                }
                runs.append((tag, params))
    return runs


def fmt_row(row: dict) -> str:
    ratio_str = "-" if row["ratio_min"] is None else f"{row['ratio_min']:.2f}"
    stop_str = "-" if row["safety_stop_pct"] is None else f"{row['safety_stop_pct']:.2f}"
    short_str = "-" if row["short_period"] is None else str(row["short_period"])
    return (
        f"{row['tag']:<18} "
        f"ret={row['total_return_pct']:+8.2f}%  "
        f"WR={row['wr_pct']:5.1f}% ({row['wins']}/{row['exits_with_pnl']})  "
        f"Sh={row['sharpe']:+5.2f}  "
        f"DD={row['max_dd_pct']:+6.1f}%  "
        f"n={short_str:>2}  ratio={ratio_str:>4}  stop={stop_str:>4}  "
        f"trades={row['num_trades']:>4}  {row['elapsed_sec']}s"
    )


def run_period(label: str) -> Path:
    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    period = PERIODS[label]
    output_root = REPO / "reports" / "ratio-hold-validation-eth" / label
    output_root.mkdir(parents=True, exist_ok=True)

    runs = build_runs()
    print(
        f"LazySwing ratio-hold validation: {label}  "
        f"{period['start']} -> {period['end']}  ({len(runs)} runs)\n",
        flush=True,
    )

    results: list[dict] = []
    for tag, params in runs:
        run_dir = output_root / tag
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = Config(
            {
                "backtest": {
                    "name": f"LazySwing_ratio_hold_{label}_{tag}",
                    "version": "ratio-validation",
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
            "short_period": params.get("flip_vol_ratio_short_period"),
            "ratio_min": params.get("flip_vol_ratio_min"),
            "safety_stop_pct": params.get("flip_vol_ratio_safety_stop_pct"),
            "trade_log_path": result.trade_log_path,
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
    return summary_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--period",
        choices=["2025", "2026_ytd", "all"],
        default="all",
    )
    args = parser.parse_args()

    labels = list(PERIODS) if args.period == "all" else [args.period]
    for label in labels:
        run_period(label)


if __name__ == "__main__":
    main()
