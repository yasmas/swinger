#!/usr/bin/env python3
"""Long-only QQQ 2026 sweep for the MACD/Vortex/ADX strategy.

Focus:
- disable shorts first
- evaluate slower indicator families on the 30m signal timeframe
- compare periods and thresholds around the first promising slow preset
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT_ROOT = REPO / "reports" / "macd-vortex-adx-qqq-2026-long-only"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

DATA_FILE = "data/QQQ-5m-2026.csv"
SYMBOL = "QQQ"
INITIAL_CASH = 100000.0
START_DATE = "2026-01-01"
END_DATE = "2026-12-31"


def _roundtrip_metrics(trade_log: pd.DataFrame) -> dict:
    closed = []
    open_trade = None
    for _, row in trade_log[trade_log["action"] != "HOLD"].iterrows():
        action = row["action"]
        price = float(row["price"])
        qty = float(row["quantity"])
        dt = row["date"]

        if action == "BUY":
            open_trade = {
                "side": "long",
                "entry_date": dt,
                "entry_price": price,
                "qty": qty,
            }
            continue

        if action != "SELL" or open_trade is None:
            continue

        gross_pct = (price / open_trade["entry_price"] - 1.0) * 100.0
        gross_cash = (price - open_trade["entry_price"]) * open_trade["qty"]
        hold_mins = (dt - open_trade["entry_date"]).total_seconds() / 60.0
        closed.append(
            {
                "gross_pct": gross_pct,
                "gross_cash": gross_cash,
                "hold_mins": hold_mins,
            }
        )
        open_trade = None

    if not closed:
        return {
            "round_trips": 0,
            "win_rate_gross": float("nan"),
            "avg_trade_pct": float("nan"),
            "median_trade_pct": float("nan"),
            "profit_factor_gross": float("nan"),
            "avg_hold_mins": float("nan"),
        }

    tr = pd.DataFrame(closed)
    gross_pos = tr.loc[tr["gross_cash"] > 0, "gross_cash"].sum()
    gross_neg = tr.loc[tr["gross_cash"] < 0, "gross_cash"].sum()
    profit_factor = gross_pos / abs(gross_neg) if gross_neg < 0 else float("inf")

    return {
        "round_trips": int(len(tr)),
        "win_rate_gross": float((tr["gross_pct"] > 0).mean() * 100.0),
        "avg_trade_pct": float(tr["gross_pct"].mean()),
        "median_trade_pct": float(tr["gross_pct"].median()),
        "profit_factor_gross": float(profit_factor),
        "avg_hold_mins": float(tr["hold_mins"].mean()),
    }


def _run_one(tag: str, params: dict) -> dict:
    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    output_dir = OUTPUT_ROOT / tag
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config(
        {
            "backtest": {
                "name": f"MACD Vortex ADX {tag}",
                "version": "exp-long-only",
                "initial_cash": INITIAL_CASH,
                "start_date": START_DATE,
                "end_date": END_DATE,
            },
            "data_source": {
                "type": "csv_file",
                "parser": "binance_kline",
                "params": {
                    "file_path": DATA_FILE,
                    "symbol": SYMBOL,
                },
            },
            "strategies": [
                {
                    "type": "macd_vortex_adx",
                    "params": params,
                }
            ],
        }
    )

    t0 = time.time()
    result = Controller(cfg, output_dir=str(output_dir)).run()[0]
    elapsed = time.time() - t0

    trade_log = TradeLogReader().read(result.trade_log_path)
    stats = compute_stats(trade_log, cfg.initial_cash, 0.05)
    rt = _roundtrip_metrics(trade_log)

    return {
        "tag": tag,
        "final_value": float(result.final_value),
        "gross_return_pct": float(stats["total_return"]),
        "after_cost_return_pct": float(stats["after_cost_return"]),
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "num_buys": int(stats["num_buys"]),
        "num_sells": int(stats["num_sells"]),
        "num_trades": int(stats["num_trades"]),
        "round_trips": rt["round_trips"],
        "win_rate_gross": rt["win_rate_gross"],
        "avg_trade_pct": rt["avg_trade_pct"],
        "median_trade_pct": rt["median_trade_pct"],
        "profit_factor_gross": rt["profit_factor_gross"],
        "avg_hold_mins": rt["avg_hold_mins"],
        "elapsed_sec": round(elapsed, 2),
        **params,
        "trade_log_path": result.trade_log_path,
    }


def build_runs() -> list[tuple[str, dict]]:
    base = {
        "symbol": SYMBOL,
        "resample_interval": "30min",
        "use_histogram_flip": True,
        "macd_fresh_bars": 2,
        "vortex_hugging_spread_mult": 1.05,
        "require_adx_rising": True,
        "breakout_lookback_bars": 3,
        "armed_breakout_expiry_bars": 2,
        "enable_short": False,
    }

    runs: list[tuple[str, dict]] = []

    # Control: same baseline, but long-only.
    runs.append(
        (
            "baseline_long_only",
            {
                **base,
                "macd_fast": 12,
                "macd_slow": 26,
                "macd_signal": 9,
                "vortex_period": 14,
                "vortex_baseline_bars": 3,
                "vortex_strong_spread_mult": 1.25,
                "adx_period": 14,
                "adx_floor": 20,
                "atr_period": 14,
                "atr_stop_multiplier": 2.0,
                "atr_trailing_multiplier": 1.5,
            },
        )
    )

    # Slow family around the first promising manual preset.
    macd_sets = [
        ("m15_33", 15, 33),
        ("m18_39", 18, 39),
        ("m21_45", 21, 45),
    ]
    vortex_periods = [18, 21, 24]
    spread_thresholds = [1.25, 1.35, 1.50]
    adx_floors = [20, 25, 30]

    for macd_tag, macd_fast, macd_slow in macd_sets:
        for vortex_period in vortex_periods:
            for spread_mult in spread_thresholds:
                for adx_floor in adx_floors:
                    tag = (
                        f"{macd_tag}_v{vortex_period}"
                        f"_s{str(spread_mult).replace('.', 'p')}"
                        f"_adx{adx_floor}"
                    )
                    runs.append(
                        (
                            tag,
                            {
                                **base,
                                "macd_fast": macd_fast,
                                "macd_slow": macd_slow,
                                "macd_signal": 9,
                                "vortex_period": vortex_period,
                                "vortex_baseline_bars": 5,
                                "vortex_strong_spread_mult": spread_mult,
                                "adx_period": 20,
                                "adx_floor": adx_floor,
                                "atr_period": 20,
                                "atr_stop_multiplier": 2.0,
                                "atr_trailing_multiplier": 1.5,
                            },
                        )
                    )
    return runs


def fmt_row(row: dict) -> str:
    return (
        f"{row['tag']:<28} "
        f"gross={row['gross_return_pct']:+7.2f}%  "
        f"net={row['after_cost_return_pct']:+7.2f}%  "
        f"WR={row['win_rate_gross']:5.1f}%  "
        f"PF={row['profit_factor_gross']:4.2f}  "
        f"trades={row['num_trades']:>3}  "
        f"DD={row['max_dd_pct']:>6.2f}%  "
        f"Sh={row['sharpe']:>5.2f}"
    )


def main() -> None:
    runs = build_runs()
    print(f"Launching {len(runs)} long-only QQQ 2026 runs...\n", flush=True)

    results: list[dict] = []
    max_workers = min(8, len(runs))
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_run_one, tag, params): tag for tag, params in runs}
            for fut in as_completed(futures):
                tag = futures[fut]
                try:
                    row = fut.result()
                except Exception as e:
                    print(f"[FAIL] {tag}: {type(e).__name__}: {e}", flush=True)
                    continue
                results.append(row)
                print(fmt_row(row), flush=True)
    except PermissionError as e:
        print(
            f"ProcessPool unavailable in this environment ({e}); falling back to sequential runs.\n",
            flush=True,
        )
        for tag, params in runs:
            try:
                row = _run_one(tag, params)
            except Exception as run_err:
                print(f"[FAIL] {tag}: {type(run_err).__name__}: {run_err}", flush=True)
                continue
            results.append(row)
            print(fmt_row(row), flush=True)

    results.sort(key=lambda x: x["after_cost_return_pct"], reverse=True)
    out_csv = OUTPUT_ROOT / "summary.csv"
    pd.DataFrame(results).to_csv(out_csv, index=False)

    print("\n=== Top 10 by after-cost return ===\n", flush=True)
    for row in results[:10]:
        print(fmt_row(row), flush=True)
    print(f"\nSaved to {out_csv}", flush=True)


if __name__ == "__main__":
    main()
