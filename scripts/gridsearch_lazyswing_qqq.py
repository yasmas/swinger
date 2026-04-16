"""
Two-stage grid search: LazySwing ST parameters for QQQ and QLD.

Dev set  : 2024-01-01 → 2025-12-31   (data/QQQ-5m-2024-2025.csv, QLD-5m-2024-2025.csv)
Live set : 2026-01-01 → 2026-04-16   (data/QQQ-5m-2026.csv, QLD-5m-2026.csv)

Stage 1 — broad sweep (12 combos per ticker):
  atr_period  : [5, 10, 15, 20]
  multiplier  : [1.5, 2.0, 2.5]

Stage 2 — narrow sweep around stage-1 winner (~9-16 combos per ticker):
  atr_period  : winner ± 2, step 1   (clipped to [3, 25])
  multiplier  : winner ± 0.25, step  (clipped to [1.0, 3.0])

Top-3 from stage 2 are run on the live set.

Ranking metric: Sharpe ratio.

Usage:
    source .venv/bin/activate
    PYTHONPATH=src python scripts/gridsearch_lazyswing_qqq.py
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
# Stage 1 grid
# ---------------------------------------------------------------------------
S1_ATR    = [5, 10, 15, 20]
S1_MULT   = [1.5, 2.0, 2.5]

# ---------------------------------------------------------------------------
# Stage 2 refinement window
# ---------------------------------------------------------------------------
S2_ATR_RADIUS  = 2      # winner ± 2, step 1
S2_MULT_RADIUS = 0.25   # winner ± 0.25, step 0.25
S2_ATR_MIN, S2_ATR_MAX   = 3, 25
S2_MULT_MIN, S2_MULT_MAX = 1.0, 3.0

TICKERS = ["QQQ", "QLD"]

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
# Data types
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


# ---------------------------------------------------------------------------
# Backtest helpers
# ---------------------------------------------------------------------------

def _make_config(symbol: str, file_path: str, start: str, end: str,
                 atr: int, mult: float) -> Config:
    return Config({
        "backtest": {
            "name": f"LazySwing_{symbol}_atr{atr}_mult{mult}",
            "version": "gs",
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
            print(f"    ERROR atr={atr} mult={mult}: {e}")
            return None
        log_path = results[0].trade_log_path
        try:
            tl = TradeLogReader.read(log_path)
        except Exception:
            return None

    if tl.empty:
        return None

    final_value = tl.iloc[-1]["portfolio_value"]
    total_ret   = (final_value / INITIAL_CASH - 1) * 100

    tl["date"] = pd.to_datetime(tl["date"])
    daily = tl.set_index("date")["portfolio_value"].resample("D").last().dropna()
    if len(daily) > 1:
        dr = daily.pct_change().dropna()
        sharpe = dr.mean() / dr.std() * math.sqrt(252) if dr.std() > 0 else 0.0
    else:
        sharpe = 0.0

    pv   = tl["portfolio_value"]
    dd   = (pv - pv.cummax()) / pv.cummax() * 100
    mdd  = dd.min()

    wr   = _compute_win_rate(tl)
    n    = int(tl["action"].isin(["BUY", "SHORT"]).sum())

    return RunResult(symbol, atr, mult, total_ret, sharpe, mdd, wr, n)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

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


def _run_sweep(symbol: str, file_path: str, combos: list[tuple[int, float]],
               label: str) -> list[RunResult]:
    total = len(combos)
    results = []
    for i, (atr, mult) in enumerate(combos, 1):
        print(f"  [{i:>3}/{total}] atr={atr:>2}  mult={mult:.2f} ...", end="", flush=True)
        r = _run_single(symbol, file_path, DEV_START, DEV_END, atr, mult)
        if r:
            results.append(r)
            print(f"  ret={r.total_return_pct:+.1f}%  sharpe={r.sharpe:.2f}  wr={r.win_rate:.0f}%")
        else:
            print("  skipped")
    results.sort(key=lambda x: x.sharpe, reverse=True)
    return results


# ---------------------------------------------------------------------------
# Stage 2 grid builder
# ---------------------------------------------------------------------------

def _stage2_combos(winner: RunResult) -> list[tuple[int, float]]:
    atr_vals = sorted(set(
        max(S2_ATR_MIN, min(S2_ATR_MAX, winner.atr + d))
        for d in range(-S2_ATR_RADIUS, S2_ATR_RADIUS + 1)
    ))
    # multiplier steps of 0.25 within ± 0.25 of winner (so winner ± 1 step = 3 values)
    mult_candidates = []
    step = S2_MULT_RADIUS
    m = winner.mult - step
    while m <= winner.mult + step + 1e-9:
        mv = round(m, 2)
        if S2_MULT_MIN <= mv <= S2_MULT_MAX:
            mult_candidates.append(mv)
        m += step
    mult_vals = sorted(set(mult_candidates))

    return [(a, m) for a in atr_vals for m in mult_vals]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_ticker(symbol: str) -> None:
    dev_path  = DEV_FILE[symbol]
    live_path = LIVE_FILE[symbol]

    if not Path(dev_path).exists():
        print(f"\nERROR: Dev data not found: {dev_path}")
        print("       Run scripts/download_qqq_qld.py first.")
        return

    # ── Stage 1 ──────────────────────────────────────────────────────────
    s1_combos = [(a, m) for a in S1_ATR for m in S1_MULT]
    print(f"\n{'=' * 75}")
    print(f"  {symbol}  Stage 1 — broad sweep  ({len(s1_combos)} combos)")
    print(f"{'=' * 75}")
    s1_results = _run_sweep(symbol, dev_path, s1_combos, "Stage 1")
    if not s1_results:
        return
    _print_table(s1_results, f"{symbol} Stage 1 results (sorted by Sharpe)", top_n=1)

    # ── Stage 2 ──────────────────────────────────────────────────────────
    winner = s1_results[0]
    s2_combos = _stage2_combos(winner)
    # Remove combos already run in stage 1
    s1_set = {(r.atr, r.mult) for r in s1_results}
    s2_new = [(a, m) for a, m in s2_combos if (a, m) not in s1_set]

    print(f"\n{'=' * 75}")
    print(f"  {symbol}  Stage 2 — narrow sweep around atr={winner.atr} mult={winner.mult}  ({len(s2_new)} new combos)")
    print(f"{'=' * 75}")
    s2_new_results = _run_sweep(symbol, dev_path, s2_new, "Stage 2")

    # Merge stage 1 results that fall in the stage 2 window with new results
    s2_window_set = set(s2_combos)
    s1_in_window = [r for r in s1_results if (r.atr, r.mult) in s2_window_set]
    all_s2 = sorted(s2_new_results + s1_in_window, key=lambda x: x.sharpe, reverse=True)

    _print_table(all_s2, f"{symbol} Stage 2 results (sorted by Sharpe)", top_n=3)

    # ── Live set: top 3 ───────────────────────────────────────────────────
    top3 = all_s2[:3]
    if not Path(live_path).exists():
        print(f"\n  Live data not found: {live_path} — skipping live run.")
        return

    print(f"\n{'=' * 75}")
    print(f"  {symbol}  Live set — top-3 combos  ({LIVE_START} → {LIVE_END})")
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
        _print_table(live_results, f"{symbol} Live results (top-3 dev combos)")


def main():
    for symbol in TICKERS:
        process_ticker(symbol)
    print("\nAll done.")


if __name__ == "__main__":
    main()
