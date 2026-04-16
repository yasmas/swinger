"""
Stage-2-only grid search: LazySwing with 30m resample for QQQ and QLD.

Centers on known 1h winners:
  QQQ: atr=10, mult=1.5
  QLD: atr=15, mult=1.5

Grid (atr ± 2 step 1, mult ± 0.25 step 0.25):
  QQQ: atr=[8..12], mult=[1.25, 1.50, 1.75]
  QLD: atr=[13..17], mult=[1.25, 1.50, 1.75]

Dev set : 2024-01-01 → 2025-12-31
Live set: 2026-01-01 → 2026-04-16  (top-3 per ticker)

Usage:
    source .venv/bin/activate
    PYTHONPATH=src python scripts/gridsearch_lazyswing_30m.py
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

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RESAMPLE = "30min"

CENTERS = {
    "QQQ": (10, 1.5),
    "QLD": (15, 1.5),
}

ATR_RADIUS  = 2
MULT_RADIUS = 0.25
MULT_STEP   = 0.25

DEV_FILE = {
    "QQQ": str(ROOT / "data" / "QQQ-5m-2024-2025.csv"),
    "QLD": str(ROOT / "data" / "QLD-5m-2024-2025.csv"),
}
LIVE_FILE = {
    "QQQ": str(ROOT / "data" / "QQQ-5m-2026.csv"),
    "QLD": str(ROOT / "data" / "QLD-5m-2026.csv"),
}

DEV_START  = "2024-01-01"
DEV_END    = "2025-12-31"
LIVE_START = "2026-01-01"
LIVE_END   = "2026-04-16"

INITIAL_CASH = 100_000.0
COST_PCT     = 0.05


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class RunResult(NamedTuple):
    symbol: str
    atr: int
    mult: float
    total_return_pct: float
    sharpe: float
    max_dd: float
    win_rate: float
    num_trades: int


def _make_config(symbol: str, file_path: str, start: str, end: str,
                 atr: int, mult: float) -> Config:
    return Config({
        "backtest": {
            "name": f"LazySwing_{symbol}_30m_atr{atr}_mult{mult}",
            "version": "gs30m",
            "initial_cash": INITIAL_CASH,
            "start_date": start,
            "end_date": end,
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


def _compute_win_rate(tl: pd.DataFrame) -> float:
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


def _run_single(symbol: str, file_path: str, start: str, end: str,
                atr: int, mult: float) -> RunResult | None:
    cfg = _make_config(symbol, file_path, start, end, atr, mult)
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


def _build_combos(center_atr: int, center_mult: float) -> list[tuple[int, float]]:
    atrs = range(center_atr - ATR_RADIUS, center_atr + ATR_RADIUS + 1)
    mults = []
    m = center_mult - MULT_RADIUS
    while m <= center_mult + MULT_RADIUS + 1e-9:
        mults.append(round(m, 2))
        m += MULT_STEP
    return [(a, m) for a in atrs for m in mults]


def _print_table(rows: list[RunResult], title: str, top_n: int | None = None) -> None:
    print(f"\n{'─' * 75}")
    print(f"  {title}")
    print(f"{'─' * 75}")
    print(f"  {'ATR':>4}  {'Mult':>5}  {'Return%':>10}  {'Sharpe':>7}  {'MaxDD%':>8}  {'WR%':>6}  {'#Trades':>8}")
    print(f"  {'─'*4}  {'─'*5}  {'─'*10}  {'─'*7}  {'─'*8}  {'─'*6}  {'─'*8}")
    for i, r in enumerate(rows):
        marker = " *" if top_n and i < top_n else "  "
        print(
            f"{marker}{r.atr:>4}  {r.mult:>5.2f}  "
            f"{r.total_return_pct:>+10.1f}  {r.sharpe:>7.2f}  "
            f"{r.max_dd:>8.2f}  {r.win_rate:>6.1f}  {r.num_trades:>8}"
        )


def process_ticker(symbol: str) -> None:
    center_atr, center_mult = CENTERS[symbol]
    combos = _build_combos(center_atr, center_mult)
    dev_path  = DEV_FILE[symbol]
    live_path = LIVE_FILE[symbol]

    print(f"\n{'=' * 75}")
    print(f"  {symbol}  30m resample — Stage 2 grid  ({len(combos)} combos, center atr={center_atr} mult={center_mult})")
    print(f"  Dev: {DEV_START} → {DEV_END}")
    print(f"{'=' * 75}")

    dev_results = []
    for i, (atr, mult) in enumerate(combos, 1):
        print(f"  [{i:>2}/{len(combos)}] atr={atr:>2}  mult={mult:.2f} ...", end="", flush=True)
        r = _run_single(symbol, dev_path, DEV_START, DEV_END, atr, mult)
        if r:
            dev_results.append(r)
            print(f"  ret={r.total_return_pct:+.1f}%  sharpe={r.sharpe:.2f}  wr={r.win_rate:.0f}%  trades={r.num_trades}")
        else:
            print("  skipped")

    if not dev_results:
        return

    dev_results.sort(key=lambda x: x.sharpe, reverse=True)
    _print_table(dev_results, f"{symbol} 30m Dev results (sorted by Sharpe)", top_n=3)

    # Live: top 3
    top3 = dev_results[:3]
    print(f"\n{'=' * 75}")
    print(f"  {symbol}  30m — Live set top-3  ({LIVE_START} → {LIVE_END})")
    print(f"{'=' * 75}")
    live_results = []
    for dev_r in top3:
        print(f"  atr={dev_r.atr}  mult={dev_r.mult:.2f} ...", end="", flush=True)
        r = _run_single(symbol, live_path, LIVE_START, LIVE_END, dev_r.atr, dev_r.mult)
        if r:
            live_results.append(r)
            print(f"  ret={r.total_return_pct:+.1f}%  sharpe={r.sharpe:.2f}  wr={r.win_rate:.0f}%")
        else:
            print("  skipped")

    if live_results:
        live_results.sort(key=lambda x: x.sharpe, reverse=True)
        _print_table(live_results, f"{symbol} 30m Live results (top-3 dev combos)")


def main():
    for symbol in ["QQQ", "QLD"]:
        process_ticker(symbol)
    print("\nAll done.")


if __name__ == "__main__":
    main()
