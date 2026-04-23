#!/usr/bin/env python3
"""QQQ 2026 relaxed-entry sweep for MACD/Vortex/ADX.

Purpose:
- start from the current promoted long-only config
- relax the filters most likely to delay entry
- measure whether entries happen earlier and how win rate changes
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

OUTPUT_ROOT = REPO / "reports" / "macd-vortex-adx-qqq-2026-relaxed-entry"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

DATA_FILE = "data/QQQ-5m-2026.csv"
SYMBOL = "QQQ"
INITIAL_CASH = 100000.0
START_DATE = "2026-01-01"
END_DATE = "2026-12-31"

EARLY_MOVE_CUTOFF = pd.Timestamp("2026-04-01 16:25:00")
MAR31_SIGNAL_START = pd.Timestamp("2026-03-31 08:25:00")


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
            "profit_factor_gross": float("nan"),
            "avg_trade_pct": float("nan"),
            "avg_hold_mins": float("nan"),
        }

    tr = pd.DataFrame(closed)
    gross_pos = tr.loc[tr["gross_cash"] > 0, "gross_cash"].sum()
    gross_neg = tr.loc[tr["gross_cash"] < 0, "gross_cash"].sum()
    profit_factor = gross_pos / abs(gross_neg) if gross_neg < 0 else float("inf")
    return {
        "round_trips": int(len(tr)),
        "win_rate_gross": float((tr["gross_pct"] > 0).mean() * 100.0),
        "profit_factor_gross": float(profit_factor),
        "avg_trade_pct": float(tr["gross_pct"].mean()),
        "avg_hold_mins": float(tr["hold_mins"].mean()),
    }


def _timing_metrics(trade_log: pd.DataFrame) -> dict:
    buys = trade_log[trade_log["action"] == "BUY"].copy()
    if buys.empty:
        return {
            "first_buy_ts": "",
            "first_buy_before_cutoff": False,
            "buys_before_cutoff": 0,
            "buys_from_mar31_signal_to_cutoff": 0,
        }

    first_buy = buys["date"].iloc[0]
    buys_before_cutoff = buys[buys["date"] < EARLY_MOVE_CUTOFF]
    mar31_window = buys[
        (buys["date"] >= MAR31_SIGNAL_START) & (buys["date"] < EARLY_MOVE_CUTOFF)
    ]
    return {
        "first_buy_ts": first_buy.isoformat(sep=" "),
        "first_buy_before_cutoff": bool(first_buy < EARLY_MOVE_CUTOFF),
        "buys_before_cutoff": int(len(buys_before_cutoff)),
        "buys_from_mar31_signal_to_cutoff": int(len(mar31_window)),
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
                "version": "relaxed-entry",
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
    tm = _timing_metrics(trade_log)

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
        "elapsed_sec": round(elapsed, 2),
        **rt,
        **tm,
        **params,
        "trade_log_path": result.trade_log_path,
    }


def build_runs() -> list[tuple[str, dict]]:
    base = {
        "symbol": SYMBOL,
        "resample_interval": "30min",
        "macd_fast": 15,
        "macd_slow": 33,
        "macd_signal": 9,
        "use_histogram_flip": True,
        "vortex_period": 21,
        "vortex_baseline_bars": 5,
        "adx_period": 20,
        "breakout_lookback_bars": 3,
        "armed_breakout_expiry_bars": 2,
        "atr_period": 20,
        "atr_stop_multiplier": 2.0,
        "atr_trailing_multiplier": 1.5,
        "enable_short": False,
    }

    runs: list[tuple[str, dict]] = []

    promoted = {
        **base,
        "macd_fresh_bars": 2,
        "require_macd_above_zero_for_long": True,
        "vortex_strong_spread_mult": 1.25,
        "vortex_hugging_spread_mult": 1.05,
        "adx_floor": 30,
        "require_adx_rising": True,
    }
    runs.append(("promoted_control", promoted))

    adx_floors = [20, 24, 30]
    adx_rising_flags = [True, False]
    zero_line_flags = [True, False]
    strong_mults = [1.10, 1.25]
    hugging_mults = [0.90, 1.05]
    fresh_bars = [2, 4]

    for adx_floor in adx_floors:
        for require_adx_rising in adx_rising_flags:
            for require_zero in zero_line_flags:
                for strong_mult in strong_mults:
                    for hugging_mult in hugging_mults:
                        for fresh in fresh_bars:
                            params = {
                                **base,
                                "macd_fresh_bars": fresh,
                                "require_macd_above_zero_for_long": require_zero,
                                "vortex_strong_spread_mult": strong_mult,
                                "vortex_hugging_spread_mult": hugging_mult,
                                "adx_floor": adx_floor,
                                "require_adx_rising": require_adx_rising,
                            }
                            tag = (
                                f"adx{str(adx_floor).replace('.', 'p')}"
                                f"_rise{int(require_adx_rising)}"
                                f"_zero{int(require_zero)}"
                                f"_strong{str(strong_mult).replace('.', 'p')}"
                                f"_hug{str(hugging_mult).replace('.', 'p')}"
                                f"_fresh{fresh}"
                            )
                            if params == promoted:
                                continue
                            runs.append((tag, params))
    return runs


def fmt_row(row: dict) -> str:
    early = "Y" if row["first_buy_before_cutoff"] else "N"
    return (
        f"{row['tag']:<42} "
        f"net={row['after_cost_return_pct']:+6.2f}% "
        f"WR={row['win_rate_gross']:5.1f}% "
        f"PF={row['profit_factor_gross']:4.2f} "
        f"trades={row['num_trades']:>3} "
        f"early={early} "
        f"DD={row['max_dd_pct']:>6.2f}%"
    )


def main() -> None:
    runs = build_runs()
    print(f"Launching {len(runs)} relaxed-entry QQQ 2026 runs...\n", flush=True)

    results: list[dict] = []
    max_workers = min(8, len(runs))
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_run_one, tag, params): tag for tag, params in runs}
            for fut in as_completed(futures):
                tag = futures[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    print(f"[FAIL] {tag}: {type(exc).__name__}: {exc}", flush=True)
                    continue
                results.append(row)
                print(fmt_row(row), flush=True)
    except PermissionError as exc:
        print(
            f"ProcessPool unavailable in this environment ({exc}); falling back to sequential runs.\n",
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

    results.sort(key=lambda row: row["after_cost_return_pct"], reverse=True)
    out_csv = OUTPUT_ROOT / "summary.csv"
    pd.DataFrame(results).to_csv(out_csv, index=False)

    print("\n=== Top 15 by after-cost return ===\n", flush=True)
    for row in results[:15]:
        print(fmt_row(row), flush=True)

    print("\n=== Earliest-entry configs that bought before 2026-04-01 16:25 ===\n", flush=True)
    early_rows = [row for row in results if row["first_buy_before_cutoff"]]
    early_rows.sort(
        key=lambda row: (
            row["first_buy_ts"] or "9999-12-31 23:59:59",
            -row["after_cost_return_pct"],
        )
    )
    for row in early_rows[:15]:
        print(fmt_row(row) + f" first_buy={row['first_buy_ts']}", flush=True)

    print(f"\nSaved to {out_csv}", flush=True)


if __name__ == "__main__":
    main()
