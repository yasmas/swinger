"""
Run additional 30m combos: QQQ atr=15, QLD atr=20 (mult 1.25/1.50/1.75 each),
then print a combined table with all previous 30m results sorted by ATR.

Usage:
    source .venv/bin/activate
    PYTHONPATH=src python scripts/gridsearch_lazyswing_30m_extend.py
"""

import json
import math
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import Config
from controller import Controller
from trade_log import TradeLogReader

RESAMPLE     = "30min"
INITIAL_CASH = 100_000.0
COST_PCT     = 0.05
DEV_START    = "2024-01-01"
DEV_END      = "2025-12-31"

DEV_FILE = {
    "QQQ": str(ROOT / "data" / "QQQ-5m-2024-2025.csv"),
    "QLD": str(ROOT / "data" / "QLD-5m-2024-2025.csv"),
}

MULTS = [1.25, 1.50, 1.75]

NEW_COMBOS = {
    "QQQ": [(15, m) for m in MULTS],
    "QLD": [(20, m) for m in MULTS],
}

# ── Prior results from gridsearch_lazyswing_30m.py ──────────────────────────

PRIOR = {
    "QQQ": [
        # (atr, mult, return_pct, sharpe, max_dd, win_rate, num_trades)
        ( 8, 1.25,  8473.1, 12.85, -6.38, 57.1, 1181),
        ( 8, 1.50,  5414.3, 11.43, -6.96, 57.9,  961),
        ( 8, 1.75,  3433.7, 10.35, -6.96, 55.5,  835),
        ( 9, 1.25,  8950.9, 13.03, -6.38, 58.0, 1174),
        ( 9, 1.50,  5698.4, 11.60, -6.96, 57.8,  959),
        ( 9, 1.75,  3666.9, 10.43, -6.96, 56.0,  823),
        (10, 1.25,  9153.4, 12.97, -6.38, 58.2, 1180),
        (10, 1.50,  5789.6, 11.61, -6.96, 58.6,  955),
        (10, 1.75,  3981.5, 10.63, -6.02, 57.7,  810),
        (11, 1.25,  9196.1, 12.93, -6.38, 58.3, 1171),
        (11, 1.50,  5890.9, 11.62, -6.96, 59.2,  958),
        (11, 1.75,  3914.9, 10.61, -6.02, 57.3,  815),
        (12, 1.25,  9606.9, 12.83, -6.38, 59.2, 1163),
        (12, 1.50,  5563.7, 11.87, -6.96, 58.7,  975),
        (12, 1.75,  3970.0, 10.49, -6.02, 57.0,  817),
    ],
    "QLD": [
        (13, 1.25, 1345182.9, 13.20, -12.09, 62.2, 1328),
        (13, 1.50,  444141.8, 12.45, -13.08, 60.4, 1110),
        (13, 1.75,  212523.1, 11.52, -13.08, 60.9,  935),
        (14, 1.25, 1440008.8, 13.22, -12.09, 62.2, 1330),
        (14, 1.50,  469336.9, 12.46, -13.08, 60.6, 1106),
        (14, 1.75,  202957.9, 11.70, -13.08, 61.2,  936),
        (15, 1.25, 1432505.4, 13.32, -12.09, 61.8, 1333),
        (15, 1.50,  478198.7, 12.57, -13.08, 61.2, 1104),
        (15, 1.75,  189614.7, 11.48, -13.08, 61.0,  940),
        (16, 1.25, 1604400.7, 13.36, -12.09, 62.3, 1332),
        (16, 1.50,  499499.2, 12.63, -13.08, 61.5, 1104),
        (16, 1.75,  188153.8, 11.42, -13.08, 61.3,  948),
        (17, 1.25, 1704142.2, 13.41, -12.09, 62.7, 1324),
        (17, 1.50,  602379.1, 12.71, -13.08, 62.2, 1098),
        (17, 1.75,  201010.5, 11.59, -13.15, 61.0,  952),
    ],
}


class RunResult(NamedTuple):
    symbol: str
    atr: int
    mult: float
    total_return_pct: float
    sharpe: float
    max_dd: float
    win_rate: float
    num_trades: int


def _make_config(symbol, file_path, atr, mult):
    return Config({
        "backtest": {
            "name": f"LazySwing_{symbol}_30m_atr{atr}_mult{mult}",
            "version": "gs30m",
            "initial_cash": INITIAL_CASH,
            "start_date": DEV_START,
            "end_date": DEV_END,
        },
        "data_source": {
            "type": "csv_file",
            "parser": "binance_kline",
            "params": {"file_path": file_path, "symbol": symbol},
        },
        "strategies": [{
            "type": "lazy_swing",
            "params": {
                "supertrend_atr_period": atr,
                "supertrend_multiplier": mult,
                "resample_interval": RESAMPLE,
                "cost_per_trade_pct": COST_PCT,
            },
        }],
    })


def _compute_win_rate(tl):
    exits = tl[tl["action"].isin(["SELL", "COVER"])]
    pnls = []
    for _, row in exits.iterrows():
        try:
            d = row["details"] if isinstance(row["details"], dict) else json.loads(row["details"])
            pnl = d.get("pnl_pct")
            if pnl is not None:
                pnls.append(float(pnl))
        except Exception:
            pass
    if not pnls:
        return 0.0
    return sum(1 for p in pnls if p > 0) / len(pnls) * 100


def _run_single(symbol, atr, mult):
    cfg = _make_config(symbol, DEV_FILE[symbol], atr, mult)
    with tempfile.TemporaryDirectory() as tmpdir:
        ctrl = Controller(cfg, output_dir=tmpdir)
        try:
            results = ctrl.run()
        except Exception as e:
            print(f" ERROR: {e}")
            return None
        try:
            tl = TradeLogReader.read(results[0].trade_log_path)
        except Exception:
            return None

    if tl.empty:
        return None

    final_value = tl.iloc[-1]["portfolio_value"]
    total_ret   = (final_value / INITIAL_CASH - 1) * 100

    tl["date"] = pd.to_datetime(tl["date"])
    daily = tl.set_index("date")["portfolio_value"].resample("D").last().dropna()
    sharpe = 0.0
    if len(daily) > 1:
        dr = daily.pct_change().dropna()
        sharpe = dr.mean() / dr.std() * math.sqrt(252) if dr.std() > 0 else 0.0

    pv  = tl["portfolio_value"]
    mdd = ((pv - pv.cummax()) / pv.cummax() * 100).min()
    wr  = _compute_win_rate(tl)
    n   = int(tl["action"].isin(["BUY", "SHORT"]).sum())

    return RunResult(symbol, atr, mult, total_ret, sharpe, mdd, wr, n)


def _print_table(rows: list[RunResult], title: str) -> None:
    print(f"\n{'─' * 83}")
    print(f"  {title}")
    print(f"{'─' * 83}")
    print(f"  {'ATR':>4}  {'Mult':>5}  {'Return%':>14}  {'Sharpe':>7}  {'MaxDD%':>8}  {'WR%':>6}  {'#Trades':>8}")
    print(f"  {'─'*4}  {'─'*5}  {'─'*14}  {'─'*7}  {'─'*8}  {'─'*6}  {'─'*8}")
    for r in rows:
        ret_str = f"+{r.total_return_pct:,.1f}%" if r.total_return_pct >= 0 else f"{r.total_return_pct:,.1f}%"
        print(
            f"  {r.atr:>4}  {r.mult:>5.2f}  {ret_str:>14}  {r.sharpe:>7.2f}  "
            f"{r.max_dd:>8.2f}  {r.win_rate:>6.1f}  {r.num_trades:>8}"
        )


def main():
    new_results: dict[str, list[RunResult]] = {"QQQ": [], "QLD": []}

    for symbol, combos in NEW_COMBOS.items():
        print(f"\n{'=' * 60}")
        print(f"  {symbol}  new combos (30m)")
        print(f"{'=' * 60}")
        for atr, mult in combos:
            print(f"  atr={atr}  mult={mult:.2f} ...", end="", flush=True)
            r = _run_single(symbol, atr, mult)
            if r:
                new_results[symbol].append(r)
                print(f"  ret={r.total_return_pct:+,.1f}%  sharpe={r.sharpe:.2f}  "
                      f"maxdd={r.max_dd:.2f}  wr={r.win_rate:.0f}%  trades={r.num_trades}")
            else:
                print("  skipped")

    # Combine prior + new, sort by (atr, mult)
    for symbol in ["QQQ", "QLD"]:
        prior_rows = [
            RunResult(symbol, atr, mult, ret, sharpe, maxdd, wr, trades)
            for atr, mult, ret, sharpe, maxdd, wr, trades in PRIOR[symbol]
        ]
        all_rows = sorted(
            prior_rows + new_results[symbol],
            key=lambda r: (r.atr, r.mult),
        )
        _print_table(all_rows, f"{symbol} — all 30m combos (sorted by ATR, dev {DEV_START}→{DEV_END})")

    print()


if __name__ == "__main__":
    main()
