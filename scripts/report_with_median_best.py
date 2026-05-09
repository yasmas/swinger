#!/usr/bin/env python3
"""Per-quarter report enriched with median-best-of-category variants.

For each category in {B, C, D}:
  1. Identify quarters where that category was the per-quarter winner (top-1).
  2. Among those quarters, find the single variant with the highest median score
     (i.e., the best variant if we had to commit to one config across the
     quarters the category dominated).
  3. Add that variant as an extra row to every quarter's table, even quarters
     the category didn't win, so we can see how well it travels.

Output: per-quarter table with baseline, top-5, and the 3 median-best rows,
plus gap-to-best columns.
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
        return "n/a".rjust(int(fmt.split('.')[0].lstrip('+')) if '.' in fmt else 6)
    return format(v, fmt)


def main() -> None:
    a = pd.read_csv(ORIG)
    b = pd.read_csv(EXT)
    df = pd.concat([a, b], ignore_index=True)

    # --- Step 1: per-quarter winner (top-1 by score among non-baseline) ---
    qtr_winners: dict[str, tuple[str, str]] = {}  # window → (tag, category)
    for wk in WINDOWS:
        sub = df[(df["window"] == wk) & (df["category"] != "baseline")].copy()
        sub = sub.sort_values("score", ascending=False, na_position="last")
        if not sub.empty:
            top = sub.iloc[0]
            qtr_winners[wk] = (top["tag"], top["category"])

    print("Per-quarter winners:")
    for wk, (tag, cat) in qtr_winners.items():
        print(f"  {wk:<10} {cat}  {tag}")
    print()

    # --- Step 2: which quarters did each category A/B/C/D win? ---
    cat_qtrs: dict[str, list[str]] = {"A": [], "B": [], "C": [], "D": []}
    for wk, (tag, cat) in qtr_winners.items():
        if cat in cat_qtrs:
            cat_qtrs[cat].append(wk)

    print("Category-won quarters:")
    for c in ("A", "B", "C", "D"):
        print(f"  {c}: {cat_qtrs[c] or '(none)'}")
    print()

    # --- Step 3: for each category, find the variant with highest median score
    #             across the category's winning quarters ---
    median_best_tag: dict[str, str | None] = {"A": None, "B": None, "C": None, "D": None}
    for cat in ("A", "B", "C", "D"):
        qtrs = cat_qtrs[cat]
        if not qtrs:
            continue
        sub = df[(df["category"] == cat) & (df["window"].isin(qtrs))].copy()
        # Variant must appear in ALL category-won quarters to be considered
        counts = sub.groupby("tag")["window"].nunique()
        eligible = counts[counts == len(qtrs)].index.tolist()
        sub = sub[sub["tag"].isin(eligible)]
        if sub.empty:
            print(f"  {cat}: no variant covers all {len(qtrs)} winning quarters")
            continue
        med = (
            sub.groupby("tag")["score"]
            .median()
            .sort_values(ascending=False, na_position="last")
        )
        best_tag = med.index[0]
        median_best_tag[cat] = best_tag
        print(f"  Best median {cat} across {len(qtrs)} quarters {qtrs}:")
        print(f"    {best_tag}  median_score={med.iloc[0]:.1f}")
    print()

    # --- Step 4: per-quarter print with rich detail ---
    cols_needed = [
        "window", "tag", "category", "return_pct", "wr_pct",
        "avg_pnl_pct", "avg_trail_pnl_pct", "trail_exits",
        "wins", "losses", "n_trades", "sharpe", "max_dd_pct", "score",
    ]

    for wk in WINDOWS:
        sub = df[df["window"] == wk].copy()
        if sub.empty:
            continue
        baseline = sub[sub["category"] == "baseline"].sort_values(
            "return_pct", ascending=False, na_position="last"
        )
        non_base = sub[sub["category"] != "baseline"].sort_values(
            "score", ascending=False, na_position="last"
        )
        # Best in this quarter (any type)
        best_row = non_base.iloc[0] if not non_base.empty else None
        best_score = float(best_row["score"]) if best_row is not None and not pd.isna(best_row["score"]) else None
        top5 = non_base.head(5)

        # Lookup median-best rows for this quarter
        median_rows: list[tuple[str, pd.Series | None]] = []
        for cat in ("A", "B", "C", "D"):
            tag = median_best_tag[cat]
            if not tag:
                median_rows.append((cat, None))
                continue
            r = sub[sub["tag"] == tag]
            median_rows.append((cat, r.iloc[0] if not r.empty else None))

        # Print header
        print("=" * 138)
        winner_tag, winner_cat = qtr_winners.get(wk, ("?", "?"))
        print(f"  {wk}    [winner: {winner_cat} {winner_tag}]")
        print("=" * 138)
        hdr = (
            f"  {'rank':<6} {'tag':<42} {'cat':>3} "
            f"{'ret%':>7} {'WR%':>5} {'avgPnL%':>8} {'trlPnL%':>8} "
            f"{'trail':>5} {'W':>3} {'L':>3} {'N':>3} {'Shrp':>5} {'maxDD%':>7} "
            f"{'score':>7} {'Δscore':>8}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))

        def _row(label: str, r: pd.Series) -> None:
            score = r['score'] if not pd.isna(r['score']) else None
            delta = (score - best_score) if (score is not None and best_score is not None) else None
            delta_str = f"{delta:+8.1f}" if delta is not None else "     n/a"
            print(
                f"  {label:<6} {r['tag']:<42} {r['category']:>3} "
                f"{r['return_pct']:>+7.2f} {r['wr_pct']:>5.1f} "
                f"{r['avg_pnl_pct']:>+8.3f} {_fmt(r['avg_trail_pnl_pct'], '+8.3f')} "
                f"{int(r['trail_exits']):>5} {int(r['wins']):>3} {int(r['losses']):>3} "
                f"{int(r['n_trades']):>3} {r['sharpe']:>5.2f} {r['max_dd_pct']:>+7.2f} "
                f"{_fmt(score, '7.1f')} {delta_str}"
            )

        for i, (_, r) in enumerate(baseline.iterrows(), start=1):
            _row(f"B{i}", r)
        print("  " + "-" * (len(hdr) - 2))
        for i, (_, r) in enumerate(top5.iterrows(), start=1):
            _row(f"#{i}", r)

        # Median-best rows (only print if not already in top-5 OR show anyway for clarity)
        printed_tags = set(top5["tag"].tolist())
        any_med = False
        for cat, r in median_rows:
            if r is None:
                continue
            label = f"M-{cat}"
            tag = r["tag"]
            mark = "" if tag not in printed_tags else " (=top5)"
            if not any_med:
                print("  " + "-" * (len(hdr) - 2))
                any_med = True
            score = r['score'] if not pd.isna(r['score']) else None
            delta = (score - best_score) if (score is not None and best_score is not None) else None
            delta_str = f"{delta:+8.1f}" if delta is not None else "     n/a"
            print(
                f"  {label:<6} {(r['tag']+mark):<42} {r['category']:>3} "
                f"{r['return_pct']:>+7.2f} {r['wr_pct']:>5.1f} "
                f"{r['avg_pnl_pct']:>+8.3f} {_fmt(r['avg_trail_pnl_pct'], '+8.3f')} "
                f"{int(r['trail_exits']):>5} {int(r['wins']):>3} {int(r['losses']):>3} "
                f"{int(r['n_trades']):>3} {r['sharpe']:>5.2f} {r['max_dd_pct']:>+7.2f} "
                f"{_fmt(score, '7.1f')} {delta_str}"
            )
        print()


if __name__ == "__main__":
    main()
