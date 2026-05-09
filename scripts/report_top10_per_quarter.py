#!/usr/bin/env python3
"""Per-quarter report: baseline + top-5 from merged grid (original + mg2.0/2.5 extension)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO / "scripts"))

from grid_search_lazyswing_profit_exit import WINDOWS  # noqa: E402

OUT_DIR = REPO / "data" / "backtests" / "eth" / "profit_exit_grid"
ORIG = OUT_DIR / "summary_all.csv"
EXT = OUT_DIR / "summary_top10_extend.csv"


def _fmt_num(v: float, fmt: str) -> str:
    if v is None or pd.isna(v):
        return "    n/a"
    return format(v, fmt)


def main() -> None:
    a = pd.read_csv(ORIG)
    b = pd.read_csv(EXT)
    df = pd.concat([a, b], ignore_index=True)
    print(f"Merged: {len(a)} + {len(b)} = {len(df)} runs")
    print(f"Quarters: {df['window'].nunique()} | Variants: {df['tag'].nunique()}\n")

    cols_show = [
        "tag", "category", "return_pct", "wr_pct",
        "avg_pnl_pct", "avg_trail_pnl_pct", "trail_exits",
        "wins", "losses", "n_trades", "sharpe", "max_dd_pct", "score",
    ]

    for wk in WINDOWS:
        sub = df[df["window"] == wk].copy()
        if sub.empty:
            continue
        baseline = sub[sub["category"] == "baseline"].copy()
        baseline = baseline.sort_values("return_pct", ascending=False, na_position="last")
        non_base = sub[sub["category"] != "baseline"].copy()
        non_base = non_base.sort_values("score", ascending=False, na_position="last")
        top5 = non_base.head(5)

        print("=" * 120)
        print(f"  {wk}")
        print("=" * 120)
        hdr = (
            f"  {'rank':<4} {'tag':<42} {'cat':>3} "
            f"{'ret%':>8} {'WR%':>6} {'avgPnL%':>8} {'trlPnL%':>8} "
            f"{'trail':>6} {'W':>4} {'L':>4} {'N':>4} {'Shrp':>5} {'maxDD%':>7} {'score':>8}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))

        # baseline first (mark with "B")
        for i, (_, r) in enumerate(baseline.iterrows(), start=1):
            print(
                f"  {'B'+str(i):<4} {r['tag']:<42} {r['category']:>3} "
                f"{r['return_pct']:>+8.2f} {r['wr_pct']:>6.1f} "
                f"{r['avg_pnl_pct']:>+8.3f} {_fmt_num(r['avg_trail_pnl_pct'], '+8.3f')} "
                f"{int(r['trail_exits']):>6} {int(r['wins']):>4} {int(r['losses']):>4} "
                f"{int(r['n_trades']):>4} {r['sharpe']:>5.2f} {r['max_dd_pct']:>+7.2f} "
                f"{_fmt_num(r['score'], '8.1f')}"
            )

        print("  " + "-" * (len(hdr) - 2))
        # top 5 non-baseline by score
        for i, (_, r) in enumerate(top5.iterrows(), start=1):
            print(
                f"  {'#'+str(i):<4} {r['tag']:<42} {r['category']:>3} "
                f"{r['return_pct']:>+8.2f} {r['wr_pct']:>6.1f} "
                f"{r['avg_pnl_pct']:>+8.3f} {_fmt_num(r['avg_trail_pnl_pct'], '+8.3f')} "
                f"{int(r['trail_exits']):>6} {int(r['wins']):>4} {int(r['losses']):>4} "
                f"{int(r['n_trades']):>4} {r['sharpe']:>5.2f} {r['max_dd_pct']:>+7.2f} "
                f"{_fmt_num(r['score'], '8.1f')}"
            )
        print()


if __name__ == "__main__":
    main()
