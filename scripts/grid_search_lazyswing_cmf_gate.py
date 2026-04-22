#!/usr/bin/env python3
"""Stage-3 ST-params scan: with CMF veto p15/L=-0.15/n=4, sweep 3 ST combos.

Keeps the winning CMF gate fixed and tests whether a looser ST (faster period
or tighter multiplier) now pairs better with the veto filter.

ST combos tested:
  atr=25 mult=1.75  (HOF baseline, control)
  atr=20 mult=1.75  (faster period)
  atr=25 mult=1.50  (tighter multiplier)

Streams results as workers complete. Base strategy: ETH HOF 30m (eth_30m_hof.yaml)
resample=30min, ST atr=25, mult=1.75, 2025-01-01 to 2026-03-31.

Usage (repo root)::

  PYTHONPATH=src python scripts/grid_search_lazyswing_cmf_gate.py
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT_ROOT = REPO / "reports" / "grid-cmf-veto-eth-hof-stage3-st"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def exit_win_rate(trade_log: pd.DataFrame) -> tuple[float, int, int, int]:
    """Win rate on SELL/COVER rows that carry pnl_pct in details."""
    wins = losses = 0
    for _, r in trade_log.iterrows():
        if r["action"] not in ("SELL", "COVER"):
            continue
        d = r.get("details")
        if not isinstance(d, dict):
            continue
        pnl = d.get("pnl_pct")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    exited = wins + losses
    wr = (wins / exited * 100.0) if exited else float("nan")
    return wr, wins, losses, exited


def _run_one(tag: str, params: dict) -> dict:
    """Run a single backtest in the worker process and return summary stats."""
    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    output_dir = OUTPUT_ROOT / tag
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg_dict = {
        "backtest": {
            "name": f"LazySwing_CMF_{tag}",
            "version": "cmf-gate",
            "initial_cash": 100000.0,
            "start_date": "2025-01-01",
            "end_date": "2026-03-31",
        },
        "data_source": {
            "type": "csv_file",
            "parser": "coinbase_intx_kline",
            "params": {
                "file_path": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
                "symbol": "ETH-PERP-INTX",
            },
        },
        "strategies": [
            {
                "type": "lazy_swing",
                "params": {
                    "resample_interval": "30min",
                    "cost_per_trade_pct": 0.05,
                    "supertrend_atr_period": 25,
                    "supertrend_multiplier": 1.75,
                    **params,
                },
            }
        ],
    }
    cfg = Config(cfg_dict)

    t0 = time.time()
    controller = Controller(cfg, output_dir=str(output_dir))
    results = controller.run()
    elapsed = time.time() - t0

    r = results[0]
    trade_log = TradeLogReader().read(r.trade_log_path)
    stats = compute_stats(trade_log, cfg.initial_cash, 0.05)
    wr, wins, losses, exited = exit_win_rate(trade_log)

    num_flips = int(stats["num_sells"]) + int(stats["num_covers"])

    return {
        "tag": tag,
        "params": params,
        "final_value": float(r.final_value),
        "total_return_pct": float(stats["total_return"]),
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "flips": num_flips,
        "exits_with_pnl": exited,
        "wr_pct": wr,
        "wins": wins,
        "losses": losses,
        "elapsed_sec": round(elapsed, 1),
    }


def build_grid() -> list[tuple[str, dict]]:
    cmf = {
        "cmf_gate_enabled": True,
        "cmf_period": 15,
        "cmf_level_min": -0.15,
        "cmf_gate_max_hourly_bars": 4,
    }
    st_combos = [
        ("st25_m1.75", {"supertrend_atr_period": 25, "supertrend_multiplier": 1.75}),
        ("st20_m1.75", {"supertrend_atr_period": 20, "supertrend_multiplier": 1.75}),
        ("st25_m1.50", {"supertrend_atr_period": 25, "supertrend_multiplier": 1.50}),
    ]
    runs: list[tuple[str, dict]] = []
    for tag, st in st_combos:
        runs.append((f"{tag}_gated", {**cmf, **st}))
        runs.append((f"{tag}_nogate", {"cmf_gate_enabled": False, **st}))
    return runs


def fmt_row(r: dict) -> str:
    return (
        f"{r['tag']:<22} "
        f"ret={r['total_return_pct']:>+9.2f}%  "
        f"final=${r['final_value']:>12,.0f}  "
        f"WR={r['wr_pct']:>5.1f}% ({r['wins']}/{r['exits_with_pnl']})  "
        f"flips={r['flips']:>4}  "
        f"Sh={r['sharpe']:>+5.2f}  "
        f"DD={r['max_dd_pct']:>+6.1f}%  "
        f"{r['elapsed_sec']}s"
    )


def main() -> None:
    runs = build_grid()
    print(f"Launching {len(runs)} runs (1 baseline + {len(runs)-1} CMF-gate configs)...\n")

    results: list[dict] = []
    max_workers = min(6, len(runs))
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_run_one, tag, params): tag for tag, params in runs}
        for fut in as_completed(futures):
            tag = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                print(f"[FAIL] {tag}: {type(e).__name__}: {e}")
                continue
            results.append(r)
            print(fmt_row(r), flush=True)

    print("\n=== Final ranking (by total_return_pct) ===\n")
    results.sort(key=lambda x: x["total_return_pct"], reverse=True)
    for r in results:
        print(fmt_row(r))

    # Write summary CSV
    out_csv = OUTPUT_ROOT / "summary.csv"
    pd.DataFrame(results).to_csv(out_csv, index=False)
    print(f"\nSummary saved to {out_csv}")


if __name__ == "__main__":
    main()
