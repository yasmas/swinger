#!/usr/bin/env python3
"""Diagnose why each median-best-of-category variant deteriorates off home.

Hypothesis: trail_stop_reentry_enabled is False in BASE_PARAMS, so after a trail
exit the strategy goes FLAT until the next ST flip. In regimes that don't match
the trail's mechanism, the trail fires "wrong" — exits a trend that continues —
incurring opportunity cost while flat, plus trade-cost drag from extra round-trips.

For each (median variant, quarter):
  - tag, baseline_ret, variant_ret, n_trades, n_trades_base
  - trail_exits, st_exits_implied
  - avg_trail_pnl, avg_st_pnl_implied (= sum-pnl-non-trail / n-st-exits)
  - extra_trade_count vs baseline → cost drag
  - "trail_pnl_contrib" = trail_exits * avg_trail_pnl  (rough sum of trail PnL%)
"""
from __future__ import annotations

import math
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

MEDIAN_BEST = {
    "M-A": "A_kc1.75_bb2.75_d1.0_mg1.75",
    "M-B": "B_lb6_d3.5_min20_mg1.75",
    "M-C": "C_f8s21g9_cross_mg1.0",
    "M-D": "D_ema8_mg1.75",
}
HOME = {"M-A": "2025_Q3", "M-B": "2024_Q3,2025_Q1,2026_Q1",
        "M-C": "2024_Q1,2024_Q2,2024_Q4,2025_Q2,2026_Q2", "M-D": "2025_Q4"}

COST_PER_TRADE_PCT = 0.05  # round trip = entry+exit; per-trade column already in this unit


def main() -> None:
    df = pd.concat([pd.read_csv(ORIG), pd.read_csv(EXT)], ignore_index=True)

    # Best baseline per quarter (highest score among the 4 mg variants)
    base = df[df["category"] == "baseline"].copy()
    base = base.sort_values(["window", "score"], ascending=[True, False], na_position="last")
    best_base = base.drop_duplicates("window", keep="first").set_index("window")

    print(f"{'Median':<5} {'Quarter':<10} {'home?':<6} {'ret%':>7} {'base%':>7} "
          f"{'Δret':>7} {'N':>4} {'Nbase':>5} {'extraN':>7} {'extraCost%':>10} "
          f"{'trail':>5} {'avgTrail%':>9} {'trailContrib%':>13} "
          f"{'avgST%(impl)':>12} {'baseAvgST%':>10}")
    print("-" * 165)

    for label, tag in MEDIAN_BEST.items():
        for wk in WINDOWS:
            row = df[(df["window"] == wk) & (df["tag"] == tag)]
            if row.empty:
                continue
            r = row.iloc[0]
            b = best_base.loc[wk]
            is_home = wk in HOME[label].split(",")
            n_v = int(r["n_trades"])
            n_b = int(b["n_trades"])
            extra_n = n_v - n_b
            extra_cost = extra_n * COST_PER_TRADE_PCT * 2  # entry+exit
            trails = int(r["trail_exits"])
            avg_trail = r["avg_trail_pnl_pct"] if not pd.isna(r["avg_trail_pnl_pct"]) else 0.0
            avg_overall = r["avg_pnl_pct"] if not pd.isna(r["avg_pnl_pct"]) else 0.0
            n_st = max(n_v - trails, 0)
            # implied avg ST pnl: total pnl sum = avg_overall * (n_v) ; trail sum = avg_trail * trails
            # st sum = total - trail; avg_st = st_sum / n_st
            if n_st > 0 and not pd.isna(avg_overall):
                total_sum = avg_overall * n_v
                trail_sum = avg_trail * trails
                avg_st = (total_sum - trail_sum) / n_st
            else:
                avg_st = float("nan")
            # baseline avg-ST (baseline has trails too, mostly few)
            n_b_trails = int(b["trail_exits"])
            n_b_st = max(n_b - n_b_trails, 0)
            b_avg_overall = b["avg_pnl_pct"]
            b_avg_trail = b["avg_trail_pnl_pct"] if not pd.isna(b["avg_trail_pnl_pct"]) else 0.0
            if n_b_st > 0 and not pd.isna(b_avg_overall):
                base_avg_st = (b_avg_overall * n_b - b_avg_trail * n_b_trails) / n_b_st
            else:
                base_avg_st = float("nan")
            trail_contrib = trails * avg_trail  # rough sum of trail-exit P&L

            print(
                f"{label:<5} {wk:<10} {'YES' if is_home else 'no':<6} "
                f"{r['return_pct']:>+7.2f} {b['return_pct']:>+7.2f} "
                f"{(r['return_pct']-b['return_pct']):>+7.2f} "
                f"{n_v:>4} {n_b:>5} {extra_n:>+7d} {extra_cost:>+10.2f} "
                f"{trails:>5} {avg_trail:>+9.3f} {trail_contrib:>+13.1f} "
                f"{avg_st:>+12.3f} {base_avg_st:>+10.3f}"
            )
        print("-" * 165)


if __name__ == "__main__":
    main()
