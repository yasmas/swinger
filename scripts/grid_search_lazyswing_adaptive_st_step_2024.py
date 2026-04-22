#!/usr/bin/env python3
"""Stage-1 grid for LazySwing adaptive step-function Supertrend on ETH 2024.

Normal state keeps the 30m baseline ST at 25 / 1.75. When short realised-vol
ratio rises above a threshold, switch to a longer / wider ST.
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

VOL_PERIOD = 4
VOL_LONG_PERIOD = 336
HIGH_ST_GRID = [
    (30, 1.5),
    (30, 1.75),
    (30, 2.0),
    (35, 1.5),
    (35, 1.75),
    (35, 2.0),
    (40, 2.0),
]
VOL_RATIO_THRESHOLDS = [0.9, 1.0, 1.1, 1.25]


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
    for high_atr, high_mult in HIGH_ST_GRID:
        for vol_thr in VOL_RATIO_THRESHOLDS:
            tag = (
                f"hv{high_atr}_m{str(high_mult).replace('.', 'p')}"
                f"_t{str(vol_thr).replace('.', 'p')}"
            )
            params = {
                **BASE_PARAMS,
                "adaptive_st_enabled": True,
                "adaptive_st_vol_period": VOL_PERIOD,
                "adaptive_st_vol_long_period": VOL_LONG_PERIOD,
                "adaptive_st_vol_ratio_threshold": vol_thr,
                "adaptive_st_high_atr_period": high_atr,
                "adaptive_st_high_multiplier": high_mult,
            }
            runs.append((tag, params))
    return runs


def fmt_row(row: dict) -> str:
    high_atr = "-" if row["high_atr"] is None else str(row["high_atr"])
    high_mult = "-" if row["high_mult"] is None else f"{row['high_mult']:.2f}"
    vol_thr = "-" if row["vol_thr"] is None else f"{row['vol_thr']:.2f}"
    return (
        f"{row['tag']:<20} "
        f"ret={row['total_return_pct']:+8.2f}%  "
        f"WR={row['wr_pct']:5.1f}% ({row['wins']}/{row['exits_with_pnl']})  "
        f"Sh={row['sharpe']:+5.2f}  "
        f"DD={row['max_dd_pct']:+6.1f}%  "
        f"high={high_atr:>2}/{high_mult:>4}  thr={vol_thr:>4}  "
        f"trades={row['num_trades']:>4}  {row['elapsed_sec']}s"
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

    output_root = REPO / "reports" / "adaptive-st-step-eth-2024" / args.label
    output_root.mkdir(parents=True, exist_ok=True)

    runs = build_runs()
    print(
        f"LazySwing adaptive-ST step grid: {args.label}  "
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
                    "name": f"LazySwing_adaptive_st_{args.label}_{tag}",
                    "version": "adaptive-step",
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
            "high_atr": params.get("adaptive_st_high_atr_period"),
            "high_mult": params.get("adaptive_st_high_multiplier"),
            "vol_thr": params.get("adaptive_st_vol_ratio_threshold"),
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
