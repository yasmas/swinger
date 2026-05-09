#!/usr/bin/env python3
"""Per-quarter full detail report.

For each window:
  - All 4 baseline rows
  - Top 5 trail-exit strategies from the ORIGINAL grid (summary_all.csv only)
  - Best variant of any type from the MERGED set (orig + mg2.0/2.5 extension),
    only printed as an extra row if it differs from the original #1.
Each row prints all stats.
"""
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


def _fmt(v, fmt: str) -> str:
    if v is None or pd.isna(v):
        return "n/a".rjust(7)
    return format(v, fmt)


def _row_line(label: str, r: pd.Series, suffix: str = "") -> str:
    score = r["score"] if not pd.isna(r["score"]) else None
    return (
        f"  {label:<8} {(r['tag']+suffix):<42} {r['category']:>8} "
        f"{r['return_pct']:>+8.2f} {r['wr_pct']:>6.1f} "
        f"{r['avg_pnl_pct']:>+8.3f} {_fmt(r['avg_trail_pnl_pct'], '+8.3f')} "
        f"{int(r['trail_exits']):>5} {int(r['wins']):>4} {int(r['losses']):>4} "
        f"{int(r['n_trades']):>4} {r['sharpe']:>5.2f} {r['max_dd_pct']:>+7.2f} "
        f"{_fmt(score, '7.1f')}"
    )


def main() -> None:
    orig = pd.read_csv(ORIG)
    ext = pd.read_csv(EXT)
    merged = pd.concat([orig, ext], ignore_index=True)
    print(f"Original runs: {len(orig)}    Extension (mg2.0/2.5): {len(ext)}    Merged: {len(merged)}\n")

    for wk in WINDOWS:
        print("=" * 138)
        print(f"  {wk}")
        print("=" * 138)
        hdr = (
            f"  {'rank':<8} {'tag':<42} {'cat':>8} "
            f"{'ret%':>8} {'WR%':>6} {'avgPnL%':>8} {'trlPnL%':>8} "
            f"{'trail':>5} {'W':>4} {'L':>4} {'N':>4} {'Shrp':>5} {'maxDD%':>7} {'score':>7}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))

        # --- baseline ---
        sub_o = orig[orig["window"] == wk].copy()
        baseline = sub_o[sub_o["category"] == "baseline"].sort_values(
            "return_pct", ascending=False, na_position="last"
        )
        for i, (_, r) in enumerate(baseline.iterrows(), start=1):
            print(_row_line(f"B{i}", r))

        print("  " + "-" * (len(hdr) - 2))

        # --- original top 5 (non-baseline) ---
        non_base_orig = sub_o[sub_o["category"] != "baseline"].sort_values(
            "score", ascending=False, na_position="last"
        )
        top5_orig = non_base_orig.head(5)
        top5_tags = set(top5_orig["tag"].tolist())
        for i, (_, r) in enumerate(top5_orig.iterrows(), start=1):
            print(_row_line(f"#{i}", r))

        # --- merged best of any type (only print if differs from original #1) ---
        sub_m = merged[merged["window"] == wk].copy()
        non_base_m = sub_m[sub_m["category"] != "baseline"].sort_values(
            "score", ascending=False, na_position="last"
        )
        if not non_base_m.empty:
            best_m = non_base_m.iloc[0]
            in_top5_orig = best_m["tag"] in top5_tags
            if not in_top5_orig:
                print("  " + "-" * (len(hdr) - 2))
                print(_row_line("MERGED", best_m, suffix=" (post-mg-extension)"))
            else:
                # If post-extension best is the same as original #1, no new row,
                # but flag for clarity.
                print("  " + "-" * (len(hdr) - 2))
                print(f"  Merged best of any type = #1 above ({best_m['tag']})")
        print()


if __name__ == "__main__":
    main()
