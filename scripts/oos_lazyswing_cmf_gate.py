#!/usr/bin/env python3
"""Out-of-sample test: top-3 CMF gate configs on 2024 and forward 2026.

Training window was 2025-01-01 → 2026-01-01 (data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv).
This script validates the top 3 gate configs from the stage-2 sweep on
truly unseen data:

  - 2024 OOS: 2023-08-31 → 2024-12-31 (data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv)
  - 2026 forward OOS: 2026-01-01 → 2026-04-17 (data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv)

Top 3 gate configs (HOF ST atr=25/mult=1.75, 30m):
  p15_L-0.15_n4   → stage-2 winner  (+438.48% in-sample)
  p15_L-0.20_n4   → stage-1 winner  (+433.54% in-sample)
  p15_L-0.20_n12  →                 (+430.61% in-sample)

Plus an ungated baseline per period for comparison. 4 runs × 2 periods = 8 runs.

Usage (repo root)::

  PYTHONPATH=src python scripts/oos_lazyswing_cmf_gate.py
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

OUTPUT_ROOT = REPO / "reports" / "oos-cmf-gate-eth-hof"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


def exit_win_rate(trade_log: pd.DataFrame) -> tuple[float, int, int, int]:
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


def _run_one(
    tag: str,
    period: dict,
    strat_params: dict,
) -> dict:
    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    output_dir = OUTPUT_ROOT / tag
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg_dict = {
        "backtest": {
            "name": f"LazySwing_OOS_{tag}",
            "version": "cmf-oos",
            "initial_cash": 100000.0,
            "start_date": period["start"],
            "end_date": period["end"],
        },
        "data_source": {
            "type": "csv_file",
            "parser": "coinbase_intx_kline",
            "params": {
                "file_path": period["file"],
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
                    **strat_params,
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
        "period": period["label"],
        "strat_params": strat_params,
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


def build_runs() -> list[tuple[str, dict, dict]]:
    periods = [
        {
            "label": "2024_OOS",
            "file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
            "start": "2023-08-31",
            "end": "2024-12-31",
        },
        {
            "label": "2026_FWD",
            "file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
            "start": "2026-01-01",
            "end": "2026-04-17",
        },
    ]

    gate_configs = [
        (
            "p15_L-0.15_n4",
            {
                "cmf_gate_enabled": True,
                "cmf_period": 15,
                "cmf_level_min": -0.15,
                "cmf_gate_max_hourly_bars": 4,
            },
        ),
        (
            "p15_L-0.20_n4",
            {
                "cmf_gate_enabled": True,
                "cmf_period": 15,
                "cmf_level_min": -0.20,
                "cmf_gate_max_hourly_bars": 4,
            },
        ),
        (
            "p15_L-0.20_n12",
            {
                "cmf_gate_enabled": True,
                "cmf_period": 15,
                "cmf_level_min": -0.20,
                "cmf_gate_max_hourly_bars": 12,
            },
        ),
        ("baseline_nogate", {"cmf_gate_enabled": False}),
    ]

    runs: list[tuple[str, dict, dict]] = []
    for period in periods:
        for cfg_tag, params in gate_configs:
            tag = f"{period['label']}__{cfg_tag}"
            runs.append((tag, period, params))
    return runs


def fmt_row(r: dict) -> str:
    return (
        f"{r['tag']:<34} "
        f"ret={r['total_return_pct']:>+9.2f}%  "
        f"final=${r['final_value']:>12,.0f}  "
        f"WR={r['wr_pct']:>5.1f}% ({r['wins']}/{r['exits_with_pnl']})  "
        f"flips={r['flips']:>4}  "
        f"Sh={r['sharpe']:>+5.2f}  "
        f"DD={r['max_dd_pct']:>+6.1f}%  "
        f"{r['elapsed_sec']}s"
    )


def main() -> None:
    runs = build_runs()
    print(f"Launching {len(runs)} OOS runs (2 periods × 4 configs)...\n")

    results: list[dict] = []
    max_workers = min(8, len(runs))
    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_run_one, tag, period, params): tag
            for tag, period, params in runs
        }
        for fut in as_completed(futures):
            tag = futures[fut]
            try:
                r = fut.result()
            except Exception as e:
                print(f"[FAIL] {tag}: {type(e).__name__}: {e}")
                continue
            results.append(r)
            print(fmt_row(r), flush=True)

    # Group by period, rank by return within each period
    print("\n=== 2024 OOS ranking ===\n")
    for r in sorted(
        [x for x in results if x["period"] == "2024_OOS"],
        key=lambda x: x["total_return_pct"],
        reverse=True,
    ):
        print(fmt_row(r))

    print("\n=== 2026 Forward OOS ranking ===\n")
    for r in sorted(
        [x for x in results if x["period"] == "2026_FWD"],
        key=lambda x: x["total_return_pct"],
        reverse=True,
    ):
        print(fmt_row(r))

    out_csv = OUTPUT_ROOT / "summary.csv"
    pd.DataFrame(results).to_csv(out_csv, index=False)
    print(f"\nSummary saved to {out_csv}")


if __name__ == "__main__":
    main()
