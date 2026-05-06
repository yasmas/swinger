#!/usr/bin/env python3
"""Compact grid search for LazySwing regime indicator periods.

Keeps the absolute momentum thresholds fixed and changes only lookback periods.
This intentionally avoids a combinatorial search; it tests a curated set of 20
period configurations across 2024, 2025, and 2026 at 12/16-bar horizons.
"""

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

HORIZONS = [12, 16]

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

PERIOD_RUNS = [
    {"tag": "baseline", "slow_vol_period": 24, "slow_vol_long_period": 336, "adx_period": 14, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv12_l168_adx10_d2_er12", "slow_vol_period": 12, "slow_vol_long_period": 168, "adx_period": 10, "adx_delta_bars": 2, "efficiency_period": 12},
    {"tag": "sv12_l336_adx10_d4_er12", "slow_vol_period": 12, "slow_vol_long_period": 336, "adx_period": 10, "adx_delta_bars": 4, "efficiency_period": 12},
    {"tag": "sv12_l336_adx14_d4_er12", "slow_vol_period": 12, "slow_vol_long_period": 336, "adx_period": 14, "adx_delta_bars": 4, "efficiency_period": 12},
    {"tag": "sv12_l672_adx14_d8_er24", "slow_vol_period": 12, "slow_vol_long_period": 672, "adx_period": 14, "adx_delta_bars": 8, "efficiency_period": 24},
    {"tag": "sv24_l168_adx10_d4_er24", "slow_vol_period": 24, "slow_vol_long_period": 168, "adx_period": 10, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv24_l336_adx10_d4_er24", "slow_vol_period": 24, "slow_vol_long_period": 336, "adx_period": 10, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv24_l336_adx14_d2_er24", "slow_vol_period": 24, "slow_vol_long_period": 336, "adx_period": 14, "adx_delta_bars": 2, "efficiency_period": 24},
    {"tag": "sv24_l336_adx14_d8_er24", "slow_vol_period": 24, "slow_vol_long_period": 336, "adx_period": 14, "adx_delta_bars": 8, "efficiency_period": 24},
    {"tag": "sv24_l336_adx20_d4_er24", "slow_vol_period": 24, "slow_vol_long_period": 336, "adx_period": 20, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv24_l672_adx14_d4_er24", "slow_vol_period": 24, "slow_vol_long_period": 672, "adx_period": 14, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv24_l672_adx20_d8_er24", "slow_vol_period": 24, "slow_vol_long_period": 672, "adx_period": 20, "adx_delta_bars": 8, "efficiency_period": 24},
    {"tag": "sv48_l336_adx14_d4_er24", "slow_vol_period": 48, "slow_vol_long_period": 336, "adx_period": 14, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv48_l672_adx14_d8_er48", "slow_vol_period": 48, "slow_vol_long_period": 672, "adx_period": 14, "adx_delta_bars": 8, "efficiency_period": 48},
    {"tag": "sv48_l672_adx20_d8_er48", "slow_vol_period": 48, "slow_vol_long_period": 672, "adx_period": 20, "adx_delta_bars": 8, "efficiency_period": 48},
    {"tag": "sv24_l336_adx10_d2_er12", "slow_vol_period": 24, "slow_vol_long_period": 336, "adx_period": 10, "adx_delta_bars": 2, "efficiency_period": 12},
    {"tag": "sv24_l336_adx20_d8_er48", "slow_vol_period": 24, "slow_vol_long_period": 336, "adx_period": 20, "adx_delta_bars": 8, "efficiency_period": 48},
    {"tag": "sv12_l168_adx14_d4_er24", "slow_vol_period": 12, "slow_vol_long_period": 168, "adx_period": 14, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv48_l336_adx10_d4_er24", "slow_vol_period": 48, "slow_vol_long_period": 336, "adx_period": 10, "adx_delta_bars": 4, "efficiency_period": 24},
    {"tag": "sv48_l336_adx20_d4_er48", "slow_vol_period": 48, "slow_vol_long_period": 336, "adx_period": 20, "adx_delta_bars": 4, "efficiency_period": 48},
]


def evaluate_periods(year: str, spec: dict, periods: dict) -> list[dict]:
    raw = load_ohlcv(REPO / spec["data_file"], spec["start"], spec["end"])
    bars = resample_ohlcv(raw, "30min")
    indicators = build_indicator_frame(
        bars,
        st_atr_period=25,
        st_multiplier=1.75,
        slow_vol_period=periods["slow_vol_period"],
        slow_vol_long_period=periods["slow_vol_long_period"],
        adx_period=periods["adx_period"],
        adx_delta_bars=periods["adx_delta_bars"],
        efficiency_period=periods["efficiency_period"],
    )
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
        outcome = summarize_mode_outcomes(assigned, continuation_pct=1.0)
        momentum = outcome.loc[outcome["mode"] == "momentum"].iloc[0]
        rows.append(
            {
                "tag": periods["tag"],
                "year": year,
                "horizon_bars": horizon,
                "signals": int(momentum["signals"]),
                "right_rate_pct": float(momentum["right_rate_pct"]),
                "avg_fwd_return_pct": float(momentum["avg_fwd_return_pct"]),
                "avg_mfe_pct": float(momentum["avg_mfe_pct"]),
                "avg_mae_pct": float(momentum["avg_mae_pct"]),
                "mfe_to_abs_mae": float(
                    momentum["avg_mfe_pct"] / abs(momentum["avg_mae_pct"])
                ),
                "trail_risk_rate_pct": float(momentum["trail_risk_rate_pct"]),
                **periods,
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
    for periods in PERIOD_RUNS:
        for year, spec in WINDOWS.items():
            rows.extend(evaluate_periods(year, spec, periods))

    detail = pd.DataFrame(rows)
    grouped = (
        detail.groupby("tag")
        .agg(
            avg_right_rate_pct=("right_rate_pct", "mean"),
            avg_fwd_return_pct=("avg_fwd_return_pct", "mean"),
            avg_mfe_pct=("avg_mfe_pct", "mean"),
            avg_mfe_to_abs_mae=("mfe_to_abs_mae", "mean"),
            min_signals=("signals", "min"),
            total_signals=("signals", "sum"),
            avg_trail_risk_rate_pct=("trail_risk_rate_pct", "mean"),
            slow_vol_period=("slow_vol_period", "first"),
            slow_vol_long_period=("slow_vol_long_period", "first"),
            adx_period=("adx_period", "first"),
            adx_delta_bars=("adx_delta_bars", "first"),
            efficiency_period=("efficiency_period", "first"),
        )
        .reset_index()
    )
    eligible = grouped.loc[grouped["min_signals"] >= 300].copy()
    ranked = eligible.sort_values(
        ["avg_right_rate_pct", "avg_fwd_return_pct"],
        ascending=[False, False],
    )
    baseline = grouped.loc[grouped["tag"] == "baseline"].copy()
    top5 = ranked.head(5).copy()

    out_dir = REPO / "reports" / "lazyswing-regime-filter" / "indicator_period_grid"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / "indicator_period_detail.csv"
    summary_path = out_dir / "indicator_period_summary.csv"
    top_path = out_dir / "indicator_period_top5.csv"
    report_path = out_dir / "indicator_period_report.md"
    detail.to_csv(detail_path, index=False)
    grouped.to_csv(summary_path, index=False)
    top5.to_csv(top_path, index=False)

    report_cols = [
        "tag",
        "avg_right_rate_pct",
        "avg_fwd_return_pct",
        "avg_mfe_pct",
        "avg_mfe_to_abs_mae",
        "min_signals",
        "slow_vol_period",
        "slow_vol_long_period",
        "adx_period",
        "adx_delta_bars",
        "efficiency_period",
    ]
    lines = [
        "# Momentum Indicator Period Grid",
        "",
        "Scope: ETH 2024, 2025, 2026; horizons 12 and 16 bars.",
        "",
        "Momentum thresholds held fixed:",
        "",
        "```text",
        "ADX14-equivalent >= 40",
        "efficiency >= 0.40",
        "ADX delta >= 1.0",
        "slow_vol_ratio <= 1.0",
        "```",
        "",
        "Ranking rule: average right-rate across all year/horizon slices, then average forward return. Configs need at least 300 momentum signals in every slice.",
        "",
        "## Baseline",
        "",
        markdown_table(baseline[report_cols]),
        "",
        "## Best 5 Period Configs",
        "",
        markdown_table(top5[report_cols]),
        "",
    ]
    report_path.write_text("\n".join(lines))

    print("Indicator period grid complete")
    print(f"Runs: {len(PERIOD_RUNS)} period configs across 3 years x 2 horizons")
    print(f"Saved detail:  {detail_path}")
    print(f"Saved summary: {summary_path}")
    print(f"Saved report:  {report_path}\n")
    print("Baseline:")
    print(baseline[report_cols].to_string(index=False))
    print("\nTop 5:")
    print(top5[report_cols].to_string(index=False))


if __name__ == "__main__":
    main()
