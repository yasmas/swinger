#!/usr/bin/env python3
"""Sweep prediction horizons for the fixed LazySwing momentum classifier."""

from __future__ import annotations

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


WINDOWS = {
    "2024": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-01-01",
        "end": "2025-01-01",
    },
    "2025": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-01-01",
        "end": "2026-01-01",
    },
    "2026": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
        "start": "2026-01-01",
        "end": "2026-05-01",
    },
}

HORIZONS = [4, 8, 12, 16]

ROBUST_MOMENTUM = {
    "momentum_adx_min": 40.0,
    "momentum_er_min": 0.40,
    "momentum_adx_delta_min": 1.0,
    "momentum_vol_ratio_max": 1.0,
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


def evaluate_window(year: str, spec: dict) -> list[dict]:
    raw = load_ohlcv(REPO / spec["data_file"], spec["start"], spec["end"])
    bars = resample_ohlcv(raw, "30min")
    indicators = build_indicator_frame(bars, st_atr_period=25, st_multiplier=1.75)
    rows = []

    for horizon in HORIZONS:
        frame = add_forward_labels(
            indicators,
            bars,
            horizon_bars=horizon,
            min_gain_pct=2.0,
            trail_stop_pct=1.0,
            continuation_pct=1.0,
        ).dropna(subset=["fwd_return_pct"])
        assigned = assign_modes(frame, **ROBUST_MOMENTUM, **COMMON)
        mode_summary = build_mode_summary(assigned)
        outcome_summary = summarize_mode_outcomes(assigned, continuation_pct=1.0)
        momentum = outcome_summary.loc[outcome_summary["mode"] == "momentum"].iloc[0]
        mode_row = mode_summary.loc[mode_summary["bucket"] == "momentum"].iloc[0]

        rows.append(
            {
                "year": year,
                "horizon_bars": horizon,
                "horizon_hours": horizon * 0.5,
                "signals": int(momentum["signals"]),
                "right": int(momentum["right"]),
                "wrong": int(momentum["wrong"]),
                "right_rate_pct": float(momentum["right_rate_pct"]),
                "avg_fwd_return_pct": float(momentum["avg_fwd_return_pct"]),
                "avg_mfe_pct": float(momentum["avg_mfe_pct"]),
                "avg_mae_pct": float(momentum["avg_mae_pct"]),
                "mfe_to_abs_mae": float(
                    momentum["avg_mfe_pct"] / abs(momentum["avg_mae_pct"])
                ),
                "avg_giveback_pct": float(momentum["avg_giveback_pct"]),
                "trail_risk_rate_pct": float(momentum["trail_risk_rate_pct"]),
                "pct_of_year_bars": float(momentum["pct_of_year_bars"]),
                "smooth_momentum_rate_pct": float(mode_row["smooth_momentum_rate_pct"]),
                "breakout_momentum_rate_pct": float(mode_row["breakout_momentum_rate_pct"]),
            }
        )
    return rows


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


def main() -> None:
    rows = []
    for year, spec in WINDOWS.items():
        rows.extend(evaluate_window(year, spec))

    df = pd.DataFrame(rows)
    out_dir = REPO / "reports" / "lazyswing-regime-filter" / "prediction_horizon_sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "momentum_horizon_summary.csv"
    report_path = out_dir / "momentum_horizon_report.md"
    df.to_csv(summary_path, index=False)

    cols = [
        "year",
        "horizon_bars",
        "horizon_hours",
        "signals",
        "right_rate_pct",
        "avg_fwd_return_pct",
        "avg_mfe_pct",
        "avg_mae_pct",
        "mfe_to_abs_mae",
        "trail_risk_rate_pct",
    ]

    lines = [
        "# Momentum Prediction Horizon Sweep",
        "",
        "Classifier held fixed at the robust momentum candidate:",
        "",
        "```text",
        "ADX14 >= 40",
        "efficiency_24 >= 0.40",
        "ADX14_delta_4 >= 1.0",
        "slow_vol_ratio <= 1.0",
        "```",
        "",
        "Only the outcome horizon changes.",
        "",
        "Momentum is right when `MFE >= +1%` and `MFE > abs(MAE)` inside the horizon.",
        "",
        markdown_table(df[cols]),
        "",
    ]
    report_path.write_text("\n".join(lines))

    print("Momentum prediction horizon sweep complete")
    print(f"Saved summary: {summary_path}")
    print(f"Saved report:  {report_path}\n")
    print(df[cols].to_string(index=False))


if __name__ == "__main__":
    main()
