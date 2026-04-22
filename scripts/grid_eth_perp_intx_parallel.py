"""
Parallel grid search for ETH-PERP-INTX LazySwing: 30m ST grid + 1h ST grid.

Writes configs and CSV results under tmp/eth-grid/.

Fetch data first (Coinbase INTX, public API)::

    python scripts/download_eth_perp_intx_coinbase.py --start 2025-01-01 --end 2026-01-01

Usage::

    cd /path/to/swinger && PYTHONPATH=src python scripts/grid_eth_perp_intx_parallel.py
"""

from __future__ import annotations

import json
import sys
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from config import Config
from controller import Controller
from reporting.reporter import compute_stats

OUT_ROOT = ROOT / "tmp" / "eth-grid"
DATA_FILE = str(ROOT / "data" / "backtests" / "eth" / "coinbase" / "ETH-PERP-INTX-5m-all.csv")
SYMBOL = "ETH-PERP-INTX"

# Backtest window (grid search request: full year 2025)
BACKTEST = {
    "name": "LazySwing ETH-PERP Grid",
    "version": "grid",
    "initial_cash": 100_000,
    "start_date": "2025-01-01",
    "end_date": "2025-12-31",
}

STRAT_BASE = {
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
}

# 30m: ST 20/1.5 plus 25 with 1.5/1.75/2
GRID_30M = [
    (20, 1.5),
    (25, 1.5),
    (25, 1.75),
    (25, 2.0),
]

# 1h: around live_1h ST 10/1.5 — vary length and multiplier
GRID_1H_ATR = [8, 10, 12, 14, 16]
GRID_1H_MULT = [1.25, 1.5, 1.75, 2.0]
# Extra 1h combos (not full cross-product)
GRID_1H_EXTRA = [
    (20, 1.0),
    (20, 1.25),
]


def _make_yaml_dict(
    resample: str,
    atr: int,
    mult: float,
    tag: str,
) -> dict[str, Any]:
    name = f"ETH-PERP-INTX {resample} ST{atr}/{mult} [{tag}]"
    return {
        "backtest": {**BACKTEST, "name": name},
        "data_source": {
            "type": "csv_file",
            "parser": "coinbase_intx_kline",
            "params": {"file_path": DATA_FILE, "symbol": SYMBOL},
        },
        "strategies": [
            {
                "type": "lazy_swing",
                "params": {
                    "resample_interval": resample,
                    "supertrend_atr_period": atr,
                    "supertrend_multiplier": mult,
                    **STRAT_BASE,
                },
            }
        ],
    }


def _run_one(cfg_dict: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    cfg_dict = copy.deepcopy(cfg_dict)
    meta = cfg_dict.pop("_meta", {})
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(cfg_dict)
    controller = Controller(cfg, output_dir=str(output_dir))
    results = controller.run()
    result = results[0]
    trade_log = pd.read_csv(result.trade_log_path, parse_dates=["date"])
    initial = float(cfg_dict["backtest"]["initial_cash"])
    cost_pct = float(
        cfg_dict["strategies"][0]["params"].get("cost_per_trade_pct", 0.05)
    )
    stats = compute_stats(trade_log, initial, cost_pct)

    exits = trade_log[trade_log["action"].isin(["SELL", "COVER"])].copy()
    if len(exits) > 0:
        exits["pnl_pct"] = exits["details"].apply(
            lambda d: json.loads(d).get("pnl_pct", 0) if isinstance(d, str) else d.get("pnl_pct", 0)
        )
        win_rate = float((exits["pnl_pct"] > 0).sum() / len(exits) * 100)
    else:
        win_rate = 0.0

    n_entries = int(trade_log["action"].isin(["BUY", "SHORT"]).sum())

    return {
        **meta,
        "total_return_pct": round(stats["total_return"], 4),
        "sharpe": round(stats["sharpe_ratio"], 4),
        "max_dd_pct": round(stats["max_drawdown"], 4),
        "win_rate_pct": round(win_rate, 4),
        "num_trades": n_entries,
        "trade_log": str(result.trade_log_path),
    }


def _grid_worker(args: tuple[str, list[tuple[int, float]], str]) -> list[dict[str, Any]]:
    resample, combos, subdir = args
    base = OUT_ROOT / subdir
    base.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for atr, mult in combos:
        tag = f"atr{atr}_m{mult}".replace(".", "p")
        cfg = _make_yaml_dict(resample, atr, mult, tag)
        yaml_path = base / f"eth_{subdir}_{tag}.yaml"
        with open(yaml_path, "w") as f:
            yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False)

        cfg_run = _make_yaml_dict(resample, atr, mult, tag)
        cfg_run["_meta"] = {
            "grid": subdir,
            "resample_interval": resample,
            "supertrend_atr_period": atr,
            "supertrend_multiplier": mult,
            "config_yaml": str(yaml_path.relative_to(ROOT)),
        }
        out_run = base / "runs" / tag
        try:
            row = _run_one(cfg_run, out_run)
            rows.append(row)
        except Exception as e:
            rows.append({
                "grid": subdir,
                "resample_interval": resample,
                "supertrend_atr_period": atr,
                "supertrend_multiplier": mult,
                "config_yaml": str(yaml_path.relative_to(ROOT)),
                "total_return_pct": None,
                "sharpe": None,
                "max_dd_pct": None,
                "win_rate_pct": None,
                "num_trades": None,
                "error": str(e),
            })
    return rows


def _sort_key(r: dict[str, Any]) -> float:
    v = r.get("total_return_pct")
    return float(v) if v is not None else float("-inf")


def _md_table(rows: list[dict[str, Any]], title: str) -> str:
    lines = [
        f"### {title}",
        "",
        "| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |",
        "|----------|--------|------|----------------|--------|------------|----------|---------|",
    ]
    for r in sorted(rows, key=_sort_key, reverse=True):
        if r.get("error"):
            lines.append(
                f"| {r.get('resample_interval', '')} | {r.get('supertrend_atr_period', '')} | "
                f"{r.get('supertrend_multiplier', '')} | ERROR | — | — | — | — |"
            )
            continue
        lines.append(
            f"| {r['resample_interval']} | {r['supertrend_atr_period']} | {r['supertrend_multiplier']} | "
            f"{r['total_return_pct']} | {r['sharpe']} | {r['win_rate_pct']} | {r['max_dd_pct']} | {r['num_trades']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    combos_30m = [("30min", GRID_30M, "30m")]
    combos_1h = [
        (
            "1h",
            [(a, m) for a in GRID_1H_ATR for m in GRID_1H_MULT] + GRID_1H_EXTRA,
            "1h",
        ),
    ]

    all_rows: list[dict[str, Any]] = []
    # ThreadPoolExecutor: avoids macOS spawn pickling __main__ workers; backtest releases GIL in numpy/pandas.
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {
            ex.submit(_grid_worker, combos_30m[0]): "30m",
            ex.submit(_grid_worker, combos_1h[0]): "1h",
        }
        for fut in as_completed(futs):
            all_rows.extend(fut.result())

    df = pd.DataFrame(all_rows)
    df_30 = df[df["grid"] == "30m"].copy()
    df_1h = df[df["grid"] == "1h"].copy()

    df_30.sort_values("total_return_pct", ascending=False, na_position="last").to_csv(
        OUT_ROOT / "eth_grid_30m_results.csv", index=False
    )
    df_1h.sort_values("total_return_pct", ascending=False, na_position="last").to_csv(
        OUT_ROOT / "eth_grid_1h_results.csv", index=False
    )

    report = "\n".join(
        [
            "# ETH-PERP-INTX LazySwing grid search",
            "",
            f"Period: {BACKTEST['start_date']} → {BACKTEST['end_date']}, initial cash ${BACKTEST['initial_cash']:,}.",
            f"Data: `{DATA_FILE}`.",
            "",
            _md_table(df_30.to_dict("records"), "30m resample (sorted by return)"),
            _md_table(df_1h.to_dict("records"), "1h resample (sorted by return)"),
        ]
    )
    (OUT_ROOT / "REPORT.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
