"""
Run top 30m (2) + top 1h (3) LazySwing configs on forward windows (2026 YTD + April 2026).

Writes rows to tmp/eth-grid/eth_oos_forward_results.csv and appends sections to REPORT.md.

Requires: data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv (Coinbase download for the range).

Usage (repo root)::

    python scripts/download_eth_perp_intx_coinbase.py --start 2026-01-01 --end 2026-05-01 \\
        --out data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv
    PYTHONPATH=src python scripts/eth_grid_top_forward.py
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

REPORT_PATH = ROOT / "tmp" / "eth-grid" / "REPORT.md"
OOS_CSV = ROOT / "tmp" / "eth-grid" / "eth_oos_forward_results.csv"
DATA_2026 = str(ROOT / "data" / "backtests" / "eth" / "coinbase" / "ETH-PERP-INTX-5m-2026.csv")

# Best two (30m) and best three (1h) from 2025 grid sort-by-return
TOP_30M: list[tuple[str, int, float]] = [
    ("30min", 20, 1.5),
    ("30min", 25, 1.5),
]
TOP_1H: list[tuple[str, int, float]] = [
    ("1h", 20, 1.0),
    ("1h", 20, 1.25),
    ("1h", 16, 1.25),
]

# User session date (YTD through inclusive calendar end)
PERIODS = [
    ("2026 YTD", "2026-01-01", "2026-04-17"),
    ("April 2026", "2026-04-01", "2026-04-30"),
]


def _load_grid_module():
    path = ROOT / "scripts" / "grid_eth_perp_intx_parallel.py"
    spec = importlib.util.spec_from_file_location("grid_eth", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_combo(
    mod,
    resample: str,
    atr: int,
    mult: float,
    start: str,
    end: str,
    period_label: str,
    out_sub: Path,
) -> dict:
    tag = f"{period_label}_{resample}_atr{atr}_m{str(mult).replace('.', 'p')}"
    tag = tag.replace(" ", "_").replace("/", "-")
    cfg = mod._make_yaml_dict(resample, atr, mult, tag)
    cfg["backtest"]["start_date"] = start
    cfg["backtest"]["end_date"] = end
    cfg["backtest"]["name"] = f"ETH-PERP-INTX OOS {period_label} {resample} ST{atr}/{mult}"
    cfg["data_source"]["params"]["file_path"] = DATA_2026

    cfg_run = copy.deepcopy(cfg)
    cfg_run["_meta"] = {
        "period_label": period_label,
        "start_date": start,
        "end_date": end,
        "resample_interval": resample,
        "supertrend_atr_period": atr,
        "supertrend_multiplier": mult,
    }
    out_dir = out_sub / tag
    row = mod._run_one(cfg_run, out_dir)
    row["period_label"] = period_label
    row["start_date"] = start
    row["end_date"] = end
    return row


def main() -> None:
    if not Path(DATA_2026).is_file():
        print(f"Missing data file: {DATA_2026}", file=sys.stderr)
        print("Run:", file=sys.stderr)
        print(
            "  python scripts/download_eth_perp_intx_coinbase.py "
            "--start 2026-01-01 --end 2026-05-01 --out data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
            file=sys.stderr,
        )
        sys.exit(1)

    mod = _load_grid_module()
    out_root = ROOT / "tmp" / "eth-grid" / "oos_forward"
    rows: list[dict] = []

    combos = [(r, a, m) for r, a, m in TOP_30M + TOP_1H]
    for period_label, start, end in PERIODS:
        sub = out_root / period_label.replace(" ", "_")
        for resample, atr, mult in combos:
            print(f"Running {period_label} {resample} ST{atr}/{mult} ...", flush=True)
            row = _run_combo(mod, resample, atr, mult, start, end, period_label, sub)
            rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(OOS_CSV, index=False)

    def md_block(title: str, frame: pd.DataFrame) -> str:
        lines = [
            f"### {title}",
            "",
            "| resample | ST len | mult | total return % | sharpe | win rate % | max DD % | #trades |",
            "|----------|--------|------|----------------|--------|------------|----------|---------|",
        ]
        frame = frame.sort_values("total_return_pct", ascending=False, na_position="last")
        for _, r in frame.iterrows():
            lines.append(
                f"| {r['resample_interval']} | {int(r['supertrend_atr_period'])} | "
                f"{r['supertrend_multiplier']} | {r['total_return_pct']} | {r['sharpe']} | "
                f"{r['win_rate_pct']} | {r['max_dd_pct']} | {int(r['num_trades'])} |"
            )
        lines.append("")
        return "\n".join(lines)

    ytd_title = "Out-of-sample: 2026 YTD (2026-01-01 → 2026-04-17)"
    apr_title = "Out-of-sample: April 2026 (2026-04-01 → 2026-04-30)"
    ytd_df = df[df["period_label"] == "2026 YTD"]
    apr_df = df[df["period_label"] == "April 2026"]

    append = "\n".join(
        [
            "",
            "---",
            "",
            "Forward tests use `data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv` (Coinbase INTX 5m). "
            "Top configs: best **two** from 30m grid and best **three** from 1h grid (2025 sort-by-return).",
            "",
            "*2026 YTD ends 2026-04-17 (last bar available in the download). "
            "April 2026 uses 2026-04-01–2026-04-30 but bars exist only through that same last timestamp.*",
            "",
            md_block(ytd_title, ytd_df),
            md_block(apr_title, apr_df),
        ]
    )

    base = REPORT_PATH.read_text(encoding="utf-8").rstrip()
    if "---" in base and "Out-of-sample: 2026 YTD" in base:
        base = base.split("\n---\n")[0].rstrip()
    REPORT_PATH.write_text(base + append + "\n", encoding="utf-8")
    print(f"Wrote {OOS_CSV} and updated {REPORT_PATH}")


if __name__ == "__main__":
    main()
