#!/usr/bin/env python3
"""Focused grid for LazySwing adaptive ST with hysteresis on ETH 2024."""

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

VOL_PERIODS = [24, 48]
LONG_PERIOD = 336
MIN_HIGH_BARS = 48  # 24 hours on 30m bars
THRESHOLD_PAIRS = [
    (0.8, 0.7),
    (0.9, 0.8),
    (1.0, 0.85),
]
HIGH_ST_PAIRS = [
    (35, 2.0),
    (40, 2.0),
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
    wr = (wins / exited * 100.0) if exited else float("nan")
    return wr, wins, losses, exited


def build_runs() -> list[tuple[str, dict]]:
    runs = [("baseline", {**BASE_PARAMS, "adaptive_st_enabled": False})]
    for vol_period in VOL_PERIODS:
        for enter_thr, exit_thr in THRESHOLD_PAIRS:
            for high_atr, high_mult in HIGH_ST_PAIRS:
                tag = (
                    f"vp{vol_period}_e{str(enter_thr).replace('.', 'p')}"
                    f"_x{str(exit_thr).replace('.', 'p')}"
                    f"_hv{high_atr}_m{str(high_mult).replace('.', 'p')}"
                )
                params = {
                    **BASE_PARAMS,
                    "adaptive_st_enabled": True,
                    "adaptive_st_vol_period": vol_period,
                    "adaptive_st_vol_long_period": LONG_PERIOD,
                    "adaptive_st_enter_ratio_threshold": enter_thr,
                    "adaptive_st_exit_ratio_threshold": exit_thr,
                    "adaptive_st_min_high_bars": MIN_HIGH_BARS,
                    "adaptive_st_high_atr_period": high_atr,
                    "adaptive_st_high_multiplier": high_mult,
                }
                runs.append((tag, params))
    return runs


def fmt_row(row: dict) -> str:
    vol_period = "-" if row["vol_period"] is None else str(row["vol_period"])
    high_atr = "-" if row["high_atr"] is None else str(row["high_atr"])
    high_mult = "-" if row["high_mult"] is None else f"{row['high_mult']:.2f}"
    enter_thr = "-" if row["enter_thr"] is None else f"{row['enter_thr']:.2f}"
    exit_thr = "-" if row["exit_thr"] is None else f"{row['exit_thr']:.2f}"
    return (
        f"{row['tag']:<32} "
        f"ret={row['total_return_pct']:+8.2f}%  "
        f"WR={row['wr_pct']:5.1f}% ({row['wins']}/{row['exits_with_pnl']})  "
        f"Sh={row['sharpe']:+5.2f}  "
        f"DD={row['max_dd_pct']:+6.1f}%  "
        f"vp={vol_period:>2}  e/x={enter_thr:>4}/{exit_thr:>4}  "
        f"high={high_atr:>2}/{high_mult:>4}  trades={row['num_trades']:>4}  "
        f"{row['elapsed_sec']}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    output_root = REPO / "reports" / "adaptive-st-hysteresis-eth-2024" / args.label
    output_root.mkdir(parents=True, exist_ok=True)

    runs = build_runs()
    print(
        f"LazySwing adaptive-ST hysteresis grid: {args.label}  "
        f"{args.start} -> {args.end}  ({len(runs)} runs)\n",
        flush=True,
    )

    results: list[dict] = []
    for tag, params in runs:
        run_dir = output_root / tag
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = Config(
            {
                "backtest": {
                    "name": f"LazySwing_adaptive_st_hysteresis_{args.label}_{tag}",
                    "version": "adaptive-hysteresis",
                    "initial_cash": INITIAL_CASH,
                    "start_date": args.start,
                    "end_date": args.end,
                },
                "data_source": {
                    "type": "csv_file",
                    "parser": "coinbase_intx_kline",
                    "params": {
                        "file_path": DATA_FILE,
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
            "vol_period": params.get("adaptive_st_vol_period"),
            "enter_thr": params.get("adaptive_st_enter_ratio_threshold"),
            "exit_thr": params.get("adaptive_st_exit_ratio_threshold"),
            "high_atr": params.get("adaptive_st_high_atr_period"),
            "high_mult": params.get("adaptive_st_high_multiplier"),
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


if __name__ == "__main__":
    main()
