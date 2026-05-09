#!/usr/bin/env python3
"""Extend top-10 per-quarter variants with min_gain ∈ {2.0, 2.5}.

For each of the 10 quarterly windows, take the top 10 from summary_all.csv,
dedupe by the underlying mechanism (mode + indicator params, ignoring mg),
then re-run each unique mechanism with new min_gain values 2.0 and 2.5
on that same window.

Output: data/backtests/eth/profit_exit_grid/summary_top10_extend.csv

Usage:
    PYTHONPATH=src python3 scripts/grid_search_top10_extend_mg.py [--workers N]
"""
from __future__ import annotations

import argparse
import math
import multiprocessing as mp
import sys
import time
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
sys.path.insert(0, str(REPO / "scripts"))

from grid_search_lazyswing_profit_exit import (  # noqa: E402
    WINDOWS,
    _prepare_slices,
    _run_one,
)

OUT_DIR = REPO / "data" / "backtests" / "eth" / "profit_exit_grid"
SUMMARY_PATH = OUT_DIR / "summary_all.csv"
EXTEND_PATH = OUT_DIR / "summary_top10_extend.csv"

# Param columns (from summary_all schema) that define a "mechanism"
# (excluding trail_stop_min_gain_pct and excluding NaN-only columns).
_MODE_PARAM_COLS = [
    "regime_trail_mode",
    "regime_exhaustion_stretch_lookback",
    "regime_exhaustion_kc_z_min",
    "regime_exhaustion_bb_z_min",
    "regime_exhaustion_adx_lookback",
    "regime_exhaustion_prev_adx_min",
    "regime_exhaustion_adx_drop_pct",
    "profit_exit_macd_fast",
    "profit_exit_macd_slow",
    "profit_exit_macd_signal_period",
    "profit_exit_macd_condition",
    "profit_exit_macd_histogram_bars",
    "profit_exit_ema_period",
]

NEW_MGS = [2.0, 2.5]


def _row_to_params(row: pd.Series) -> dict:
    """Extract the trail_params dict (excluding min_gain) used by _build_config."""
    out: dict = {}
    for col in _MODE_PARAM_COLS:
        v = row.get(col)
        if v is None:
            continue
        if isinstance(v, float) and math.isnan(v):
            continue
        # Coerce numeric-looking strings back to numbers
        if isinstance(v, (int, float)):
            # Many discrete params are integers in the grid (lookbacks, periods)
            if col in (
                "regime_exhaustion_stretch_lookback",
                "regime_exhaustion_adx_lookback",
                "profit_exit_macd_fast",
                "profit_exit_macd_slow",
                "profit_exit_macd_signal_period",
                "profit_exit_macd_histogram_bars",
                "profit_exit_ema_period",
            ):
                out[col] = int(v)
            else:
                out[col] = float(v)
        else:
            out[col] = v
    return out


def _strip_mg_suffix(tag: str) -> str:
    """Drop the trailing _mgX.X token so we can dedupe by mechanism."""
    if "_mg" not in tag:
        return tag
    return tag.rsplit("_mg", 1)[0]


def _new_tag(stripped: str, mg: float) -> str:
    return f"{stripped}_mg{mg}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--top-n", type=int, default=10,
                        help="Take top-N per quarter (default 10)")
    args = parser.parse_args()

    print(f"Loading {SUMMARY_PATH} ...", flush=True)
    df = pd.read_csv(SUMMARY_PATH)
    print(f"  rows: {len(df)}  windows: {df['window'].nunique()}", flush=True)

    print("Preparing data slices ...", flush=True)
    slice_map = _prepare_slices()
    print()

    # --- Build dedup'd task list: (qtr, mechanism) → for each mg in NEW_MGS ---
    tasks: list[tuple[str, str, dict, str]] = []
    seen_per_window: dict[str, set[str]] = {}

    for wk in WINDOWS:
        sub = df[df["window"] == wk].copy()
        # Drop baseline (we don't need to re-tune baseline mg outside what we have)
        sub = sub[sub["category"] != "baseline"]
        sub = sub.sort_values("score", ascending=False, na_position="last")
        top = sub.head(args.top_n)
        seen: set[str] = set()
        for _, row in top.iterrows():
            stripped = _strip_mg_suffix(str(row["tag"]))
            if stripped in seen:
                continue
            seen.add(stripped)
            params_no_mg = _row_to_params(row)
            for mg in NEW_MGS:
                params = {**params_no_mg, "trail_stop_min_gain_pct": mg}
                new_tag = _new_tag(stripped, mg)
                tasks.append((wk, new_tag, params, slice_map[wk]))
        seen_per_window[wk] = seen

    print(f"Unique mechanisms per window:")
    for wk, seen in seen_per_window.items():
        print(f"  {wk:<10} {len(seen)} mechanisms")
    print(f"Total runs: {len(tasks)} ({len(NEW_MGS)} new mg × ~{len(tasks)//len(NEW_MGS)//10} mechanisms × 10 windows)")
    print(f"Workers: {args.workers}\n")

    t0 = time.time()
    if args.workers == 1:
        rows = [_run_one(t) for t in tasks]
    else:
        with mp.Pool(args.workers) as pool:
            rows = pool.map(_run_one, tasks)

    out_df = pd.DataFrame(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(EXTEND_PATH, index=False)
    elapsed = time.time() - t0
    print(f"\nDone. {len(rows)} runs in {elapsed:.0f}s. Wrote {EXTEND_PATH}")


if __name__ == "__main__":
    main()
