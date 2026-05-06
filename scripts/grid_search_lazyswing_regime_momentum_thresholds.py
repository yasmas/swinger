#!/usr/bin/env python3
"""Compact grid search for LazySwing regime momentum thresholds.

This reuses the offline regime diagnostic frame and only changes the absolute
thresholds that decide whether a bar is classified as momentum.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from analyze_lazyswing_regime_filter import (  # noqa: E402
    add_forward_labels,
    assign_modes,
    build_indicator_frame,
    build_mode_summary,
    load_ohlcv,
    resample_ohlcv,
    summarize_mode_outcomes,
)


DATA_FILE = "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv"

WINDOWS = {
    "eth_2024_momentum_grid": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-01-01",
        "end": "2025-01-01",
    },
    "eth_2025_momentum_grid": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-01-01",
        "end": "2026-01-01",
    },
    "eth_2026_momentum_grid": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
        "start": "2026-01-01",
        "end": "2026-05-01",
    },
}

COMMON = {
    "breakout_adx_min": 20.0,
    "breakout_er_min": 0.30,
    "breakout_adx_delta_min": 2.0,
    "stretch_kc_z_min": 1.0,
    "stretch_bb_z_min": 1.5,
    "decay_adx_max": 20.0,
    "decay_er_max": 0.25,
    "decay_adx_delta_max": -2.0,
}

BASELINE = {
    "tag": "baseline",
    "momentum_adx_min": 25.0,
    "momentum_er_min": 0.35,
    "momentum_adx_delta_min": -1.0,
    "momentum_vol_ratio_max": 1.20,
}

GRID = [
    {"tag": "adx20_er030_d-1_v120", "momentum_adx_min": 20.0, "momentum_er_min": 0.30, "momentum_adx_delta_min": -1.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx20_er035_d-1_v120", "momentum_adx_min": 20.0, "momentum_er_min": 0.35, "momentum_adx_delta_min": -1.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx20_er040_d0_v120", "momentum_adx_min": 20.0, "momentum_er_min": 0.40, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx20_er045_d0_v110", "momentum_adx_min": 20.0, "momentum_er_min": 0.45, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx25_er030_d-1_v120", "momentum_adx_min": 25.0, "momentum_er_min": 0.30, "momentum_adx_delta_min": -1.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx25_er035_d0_v120", "momentum_adx_min": 25.0, "momentum_er_min": 0.35, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx25_er040_d0_v120", "momentum_adx_min": 25.0, "momentum_er_min": 0.40, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx25_er045_d1_v110", "momentum_adx_min": 25.0, "momentum_er_min": 0.45, "momentum_adx_delta_min": 1.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx30_er030_d-1_v120", "momentum_adx_min": 30.0, "momentum_er_min": 0.30, "momentum_adx_delta_min": -1.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx30_er035_d0_v120", "momentum_adx_min": 30.0, "momentum_er_min": 0.35, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx30_er040_d0_v110", "momentum_adx_min": 30.0, "momentum_er_min": 0.40, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx30_er045_d1_v110", "momentum_adx_min": 30.0, "momentum_er_min": 0.45, "momentum_adx_delta_min": 1.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx35_er030_d0_v120", "momentum_adx_min": 35.0, "momentum_er_min": 0.30, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.20},
    {"tag": "adx35_er035_d0_v110", "momentum_adx_min": 35.0, "momentum_er_min": 0.35, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx35_er040_d1_v110", "momentum_adx_min": 35.0, "momentum_er_min": 0.40, "momentum_adx_delta_min": 1.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx35_er045_d1_v100", "momentum_adx_min": 35.0, "momentum_er_min": 0.45, "momentum_adx_delta_min": 1.0, "momentum_vol_ratio_max": 1.00},
    {"tag": "adx40_er030_d0_v110", "momentum_adx_min": 40.0, "momentum_er_min": 0.30, "momentum_adx_delta_min": 0.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx40_er035_d1_v110", "momentum_adx_min": 40.0, "momentum_er_min": 0.35, "momentum_adx_delta_min": 1.0, "momentum_vol_ratio_max": 1.10},
    {"tag": "adx40_er040_d1_v100", "momentum_adx_min": 40.0, "momentum_er_min": 0.40, "momentum_adx_delta_min": 1.0, "momentum_vol_ratio_max": 1.00},
    {"tag": "adx40_er045_d2_v100", "momentum_adx_min": 40.0, "momentum_er_min": 0.45, "momentum_adx_delta_min": 2.0, "momentum_vol_ratio_max": 1.00},
]


def evaluate(frame: pd.DataFrame, params: dict) -> dict:
    threshold_params = {key: value for key, value in params.items() if key != "tag"}
    assigned = assign_modes(frame, **threshold_params, **COMMON)
    mode_summary = build_mode_summary(assigned)
    outcome_summary = summarize_mode_outcomes(assigned, continuation_pct=1.0)

    momentum = outcome_summary.loc[outcome_summary["mode"] == "momentum"].iloc[0]
    mode_row = mode_summary.loc[mode_summary["bucket"] == "momentum"].iloc[0]

    return {
        "tag": params["tag"],
        "signals": int(momentum["signals"]),
        "right": int(momentum["right"]),
        "wrong": int(momentum["wrong"]),
        "right_rate_pct": float(momentum["right_rate_pct"]),
        "avg_fwd_return_pct": float(momentum["avg_fwd_return_pct"]),
        "avg_mfe_pct": float(momentum["avg_mfe_pct"]),
        "avg_mae_pct": float(momentum["avg_mae_pct"]),
        "avg_giveback_pct": float(momentum["avg_giveback_pct"]),
        "trail_risk_rate_pct": float(momentum["trail_risk_rate_pct"]),
        "pct_of_year_bars": float(momentum["pct_of_year_bars"]),
        "smooth_momentum_rate_pct": float(mode_row["smooth_momentum_rate_pct"]),
        "breakout_momentum_rate_pct": float(mode_row["breakout_momentum_rate_pct"]),
        "momentum_adx_min": params["momentum_adx_min"],
        "momentum_er_min": params["momentum_er_min"],
        "momentum_adx_delta_min": params["momentum_adx_delta_min"],
        "momentum_vol_ratio_max": params["momentum_vol_ratio_max"],
    }


def markdown_table(df: pd.DataFrame) -> str:
    formatted = df.copy()
    for col in formatted.columns:
        if pd.api.types.is_float_dtype(formatted[col]):
            formatted[col] = formatted[col].map(lambda x: f"{x:.3f}")
        else:
            formatted[col] = formatted[col].map(str)
    lines = [
        "| " + " | ".join(formatted.columns) + " |",
        "| " + " | ".join(["---"] * len(formatted.columns)) + " |",
    ]
    for row in formatted.values.tolist():
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def run_window(label: str, spec: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw = load_ohlcv(REPO / spec["data_file"], spec["start"], spec["end"])
    bars = resample_ohlcv(raw, "30min")
    frame = build_indicator_frame(bars, st_atr_period=25, st_multiplier=1.75)
    frame = add_forward_labels(
        frame,
        bars,
        horizon_bars=8,
        min_gain_pct=2.0,
        trail_stop_pct=1.0,
        continuation_pct=1.0,
    ).dropna(subset=["fwd_return_pct"])

    runs = [BASELINE, *GRID]
    rows = [evaluate(frame, run) for run in runs]
    df = pd.DataFrame(rows)

    baseline = df.loc[df["tag"] == "baseline"].copy()
    candidates = df.loc[df["tag"] != "baseline"].copy()
    eligible = candidates.loc[candidates["signals"] >= 500].copy()
    ranked = eligible.sort_values(
        ["right_rate_pct", "avg_fwd_return_pct"],
        ascending=[False, False],
    )
    top5 = ranked.head(5).copy()

    out_dir = REPO / "reports" / "lazyswing-regime-filter" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "momentum_grid_summary.csv"
    top_path = out_dir / "momentum_grid_top5.csv"
    report_path = out_dir / "momentum_grid_report.md"
    df.to_csv(summary_path, index=False)
    top5.to_csv(top_path, index=False)

    report_cols = [
        "tag",
        "signals",
        "right_rate_pct",
        "avg_fwd_return_pct",
        "avg_mfe_pct",
        "avg_mae_pct",
        "trail_risk_rate_pct",
        "momentum_adx_min",
        "momentum_er_min",
        "momentum_adx_delta_min",
        "momentum_vol_ratio_max",
    ]
    lines = [
        f"# {spec['start'][:4]} Momentum Threshold Grid",
        "",
        f"Scope: ETH {spec['start']} to {spec['end']}, 30m bars, 20 candidate threshold sets plus baseline.",
        "",
        "Ranking rule: among configs with at least 500 momentum signals, sort by path-based momentum right-rate, then average forward return.",
        "",
        "Momentum is right when `MFE >= +1%` and `MFE > abs(MAE)` over the next 8 bars.",
        "",
        "## Baseline",
        "",
        markdown_table(baseline[report_cols]),
        "",
        "## Best 5 Candidate Runs",
        "",
        markdown_table(top5[report_cols]),
        "",
    ]
    report_path.write_text("\n".join(lines))

    print("2025 momentum threshold grid complete")
    print(f"Window: {label}")
    print(f"Runs: {len(GRID)} candidates + baseline")
    print(f"Saved summary: {summary_path}")
    print(f"Saved top5:    {top_path}")
    print(f"Saved report:  {report_path}\n")
    print("Baseline:")
    print(baseline[report_cols].to_string(index=False))
    print("\nTop 5:")
    print(top5[report_cols].to_string(index=False))
    return df, baseline, top5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--window",
        choices=[*WINDOWS.keys(), "all"],
        default="eth_2025_momentum_grid",
    )
    args = parser.parse_args()

    labels = list(WINDOWS) if args.window == "all" else [args.window]
    for label in labels:
        run_window(label, WINDOWS[label])


if __name__ == "__main__":
    main()
