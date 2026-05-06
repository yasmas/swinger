#!/usr/bin/env python3
"""Grid search stricter LazySwing mean-reversion regime classifiers.

This is an offline diagnostic. It does not run the strategy or change trading
behavior. It asks whether candidate "mean_revert" labels actually produce a
strict adverse move over the next 8/12/16 resampled bars.
"""

from __future__ import annotations

import argparse
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strategies.intraday_indicators import (  # noqa: E402
    compute_bollinger,
    compute_hmacd,
    compute_keltner,
    compute_realised_vol,
    compute_supertrend,
)
from strategies.macd_rsi_advanced import compute_adx, compute_macd  # noqa: E402


WINDOWS = {
    "2024": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-01-01",
        "end": "2025-01-01",
    },
    "2024h2": {
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-07-01",
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
        "end": "2026-05-06",
    },
    "hard": {
        "parts": ["2024h2", "2026"],
    },
}

HORIZONS = [8, 12, 16]


def load_ohlcv(path: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "open_time" in df.columns:
        df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], utc=True)
    else:
        raise ValueError("CSV must include open_time or date")

    df = df.set_index("date").sort_index()
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    return df.loc[(df.index >= start_ts) & (df.index < end_ts)]


def efficiency_ratio(closes: pd.Series, period: int) -> pd.Series:
    direction = (closes - closes.shift(period)).abs()
    volatility = closes.diff().abs().rolling(period).sum()
    return direction / volatility.replace(0.0, np.nan)


def build_frame(bars: pd.DataFrame) -> pd.DataFrame:
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]

    _, st_bullish = compute_supertrend(high, low, close, 25, 1.75)
    direction = pd.Series(np.where(st_bullish, 1.0, -1.0), index=bars.index)

    adx = compute_adx(high, low, close, 14)
    er = efficiency_ratio(close, 24)
    vol = compute_realised_vol(close, period=24, annualize=False)
    vol_ratio = vol / vol.shift(1).rolling(336, min_periods=336).mean().replace(0.0, np.nan)

    bb_upper, bb_mid, _ = compute_bollinger(close, 20, 2.0)
    bb_std = (bb_upper - bb_mid) / 2.0
    bb_abs_z = ((close - bb_mid) / bb_std.replace(0.0, np.nan)).abs()
    kc_upper, kc_mid, kc_lower = compute_keltner(high, low, close, 20, 20, 1.5)
    kc_half_width = (kc_upper - kc_lower) / 2.0
    kc_abs_z = ((close - kc_mid) / kc_half_width.replace(0.0, np.nan)).abs()

    ema_macd, ema_signal, _ = compute_macd(close, 12, 26, 9)
    hmacd, hsignal, _ = compute_hmacd(close, 24, 51, 12)

    frame = pd.DataFrame(index=bars.index)
    frame["open"] = bars["open"]
    frame["high"] = high
    frame["low"] = low
    frame["close"] = close
    frame["direction"] = direction
    frame["adx"] = adx
    frame["er"] = er
    frame["slow_vol_ratio"] = vol_ratio
    frame["bb_abs_z"] = bb_abs_z
    frame["kc_abs_z"] = kc_abs_z
    frame["ema_macd"] = ema_macd
    frame["ema_signal"] = ema_signal
    frame["hmacd"] = hmacd
    frame["hsignal"] = hsignal
    return frame


def add_outcomes(frame: pd.DataFrame, horizon: int, adverse_threshold_pct: float) -> pd.DataFrame:
    out = frame.copy()
    fwd = pd.Series(np.nan, index=out.index)
    mfe = pd.Series(np.nan, index=out.index)
    mae = pd.Series(np.nan, index=out.index)

    for i in range(len(out) - horizon):
        close_now = float(out["close"].iloc[i])
        if close_now <= 0 or not np.isfinite(close_now):
            continue
        future = out.iloc[i + 1 : i + horizon + 1]
        future_close = float(out["close"].iloc[i + horizon])
        direction = float(out["direction"].iloc[i])
        if direction > 0:
            fwd.iloc[i] = (future_close / close_now - 1.0) * 100.0
            mfe.iloc[i] = (future["high"].max() / close_now - 1.0) * 100.0
            mae.iloc[i] = (future["low"].min() / close_now - 1.0) * 100.0
        else:
            fwd.iloc[i] = (close_now / future_close - 1.0) * 100.0
            mfe.iloc[i] = (close_now / future["low"].min() - 1.0) * 100.0
            mae.iloc[i] = (close_now / future["high"].max() - 1.0) * 100.0

    out[f"fwd_{horizon}"] = fwd
    out[f"mfe_{horizon}"] = mfe.clip(lower=0.0)
    out[f"mae_{horizon}"] = mae.clip(upper=0.0)
    out[f"strict_revert_{horizon}"] = (
        (out[f"mae_{horizon}"] <= -adverse_threshold_pct)
        & (out[f"mae_{horizon}"].abs() > out[f"mfe_{horizon}"])
    )
    out[f"continuation_{horizon}"] = (
        (out[f"mfe_{horizon}"] >= adverse_threshold_pct)
        & (out[f"mfe_{horizon}"] > out[f"mae_{horizon}"].abs())
    )
    return out


def macd_masks(frame: pd.DataFrame, variant: str, lookback: int, compression_pct: float, min_gap_bps: float) -> pd.Series:
    if variant == "ema":
        line = frame["ema_macd"]
        signal = frame["ema_signal"]
    elif variant == "hmacd":
        line = frame["hmacd"]
        signal = frame["hsignal"]
    else:
        return pd.Series(True, index=frame.index)

    prev_line = line.shift(lookback)
    prev_signal = signal.shift(lookback)
    prev_gap = (prev_line - prev_signal).abs()
    now_gap = (line - signal).abs()
    prev_gap_bps = prev_gap / frame["close"].replace(0.0, np.nan) * 10000.0
    compression = (prev_gap - now_gap) / prev_gap.replace(0.0, np.nan) * 100.0
    was_with_trend = (
        ((frame["direction"] > 0) & (prev_line > prev_signal))
        | ((frame["direction"] < 0) & (prev_line < prev_signal))
    )
    return (
        was_with_trend
        & (prev_gap_bps >= min_gap_bps)
        & (compression >= compression_pct)
    )


def macd_cross_masks(frame: pd.DataFrame, variant: str) -> pd.Series:
    if variant == "ema":
        line = frame["ema_macd"]
        signal = frame["ema_signal"]
    elif variant == "hmacd":
        line = frame["hmacd"]
        signal = frame["hsignal"]
    else:
        return pd.Series(False, index=frame.index)

    prev_line = line.shift(1)
    prev_signal = signal.shift(1)
    long_cross_down = (frame["direction"] > 0) & (prev_line >= prev_signal) & (line < signal)
    short_cross_up = (frame["direction"] < 0) & (prev_line <= prev_signal) & (line > signal)
    return long_cross_down | short_cross_up


def current_mean_revert_mask(frame: pd.DataFrame) -> pd.Series:
    adx_delta = frame["adx"].diff(2)
    momentum = (
        (frame["adx"] >= 40.0)
        & (frame["er"] >= 0.40)
        & (adx_delta >= 1.0)
        & (frame["slow_vol_ratio"] <= 1.0)
    )
    stretched = (frame["kc_abs_z"] >= 1.0) | (frame["bb_abs_z"] >= 1.5)
    trend_not_confirmed = (
        (frame["adx"] < 40.0)
        | (frame["er"] < 0.40)
        | (adx_delta < 0.0)
    )
    return stretched & trend_not_confirmed & ~momentum


def score_mask(frame: pd.DataFrame, mask: pd.Series, tag: str, params: dict, min_signals: int) -> dict | None:
    row = {"tag": tag, **params}
    signal_count = int(mask.sum())
    row["signals"] = signal_count
    row["pct_of_valid_bars"] = signal_count / len(frame) * 100.0 if len(frame) else np.nan
    if signal_count < min_signals:
        return None

    rates = []
    for horizon in HORIZONS:
        valid = mask & frame[f"strict_revert_{horizon}"].notna()
        n = int(valid.sum())
        right = int(frame.loc[valid, f"strict_revert_{horizon}"].sum())
        continuation = int(frame.loc[valid, f"continuation_{horizon}"].sum())
        rate = right / n * 100.0 if n else np.nan
        rates.append(rate)
        row[f"h{horizon}_signals"] = n
        row[f"h{horizon}_right"] = right
        row[f"h{horizon}_wrong"] = n - right
        row[f"h{horizon}_right_rate_pct"] = rate
        row[f"h{horizon}_continuation_rate_pct"] = continuation / n * 100.0 if n else np.nan
        row[f"h{horizon}_avg_fwd_pct"] = frame.loc[valid, f"fwd_{horizon}"].mean()
        row[f"h{horizon}_avg_mfe_pct"] = frame.loc[valid, f"mfe_{horizon}"].mean()
        row[f"h{horizon}_avg_mae_pct"] = frame.loc[valid, f"mae_{horizon}"].mean()

    row["avg_right_rate_pct"] = float(np.nanmean(rates))
    row["min_right_rate_pct"] = float(np.nanmin(rates))
    row["all_horizons_over_60"] = bool(np.nanmin(rates) >= 60.0)
    return row


def run_grid(frame: pd.DataFrame, min_signals: int) -> pd.DataFrame:
    rows = []
    baseline = current_mean_revert_mask(frame)
    rows.append(score_mask(frame, baseline, "current_mean_revert", {
        "stretch_lookback": 1,
        "kc_abs_z_min": 1.0,
        "bb_abs_z_min": 1.5,
        "adx_lookback": 2,
        "prev_adx_min": np.nan,
        "adx_drop_pct": np.nan,
        "macd_rule": "none",
        "macd_variant": "none",
        "macd_lookback": np.nan,
        "macd_compression_pct": np.nan,
        "macd_min_gap_bps": np.nan,
    }, min_signals=1))

    stretch_lookbacks = [1, 3, 6, 12]
    kc_thresholds = [1.0, 1.25, 1.5, 1.75, 2.0]
    bb_thresholds = [1.5, 2.0, 2.5, 3.0]
    adx_lookbacks = [2, 4]
    prev_adx_mins = [20.0, 25.0, 30.0]
    adx_drop_pcts = [2.5, 5.0, 7.5, 10.0]
    macd_options: list[dict] = [{"rule": "none", "variant": "none"}]
    for variant, lookback, compression, min_gap in product(
        ["ema", "hmacd"],
        [2, 4],
        [10.0, 20.0, 30.0, 40.0],
        [0.0, 5.0],
    ):
        macd_options.append({
            "rule": "compression",
            "variant": variant,
            "lookback": lookback,
            "compression": compression,
            "min_gap": min_gap,
        })
    for variant in ["ema", "hmacd"]:
        macd_options.append({"rule": "cross", "variant": variant})

    macd_cache = {}
    for opt in macd_options:
        key = tuple(sorted(opt.items()))
        if opt["rule"] == "none":
            macd_cache[key] = pd.Series(True, index=frame.index)
        elif opt["rule"] == "cross":
            macd_cache[key] = macd_cross_masks(frame, opt["variant"])
        else:
            macd_cache[key] = macd_masks(
                frame,
                opt["variant"],
                int(opt["lookback"]),
                float(opt["compression"]),
                float(opt["min_gap"]),
            )

    not_strong_momentum = ~(
        (frame["adx"] >= 40.0)
        & (frame["er"] >= 0.40)
        & (frame["adx"].diff(2) >= 1.0)
        & (frame["slow_vol_ratio"] <= 1.0)
    )

    for stretch_lb, kc_min, bb_min, adx_lb, prev_adx_min, adx_drop in product(
        stretch_lookbacks,
        kc_thresholds,
        bb_thresholds,
        adx_lookbacks,
        prev_adx_mins,
        adx_drop_pcts,
    ):
        recent_stretch = (
            frame["kc_abs_z"].rolling(stretch_lb, min_periods=1).max().ge(kc_min)
            | frame["bb_abs_z"].rolling(stretch_lb, min_periods=1).max().ge(bb_min)
        )
        prev_adx = frame["adx"].shift(adx_lb)
        adx_pct_change = (frame["adx"] / prev_adx.replace(0.0, np.nan) - 1.0) * 100.0
        adx_fade = (prev_adx >= prev_adx_min) & (adx_pct_change <= -adx_drop)
        base_mask = recent_stretch & adx_fade & not_strong_momentum

        if not bool(base_mask.any()):
            continue

        for opt in macd_options:
            key = tuple(sorted(opt.items()))
            mask = base_mask & macd_cache[key]
            params = {
                "stretch_lookback": stretch_lb,
                "kc_abs_z_min": kc_min,
                "bb_abs_z_min": bb_min,
                "adx_lookback": adx_lb,
                "prev_adx_min": prev_adx_min,
                "adx_drop_pct": adx_drop,
                "macd_rule": opt["rule"],
                "macd_variant": opt["variant"],
                "macd_lookback": opt.get("lookback", np.nan),
                "macd_compression_pct": opt.get("compression", np.nan),
                "macd_min_gap_bps": opt.get("min_gap", np.nan),
            }
            row = score_mask(frame, mask, "candidate", params, min_signals)
            if row is not None:
                rows.append(row)

    return pd.DataFrame(rows)


def build_scored_frame(year: str, adverse_threshold_pct: float) -> pd.DataFrame:
    window = WINDOWS[year]
    if "parts" in window:
        frames = [
            build_scored_frame(part, adverse_threshold_pct)
            for part in window["parts"]
        ]
        return pd.concat(frames).sort_index()

    raw = load_ohlcv(REPO / window["data_file"], window["start"], window["end"])
    bars = raw.resample("30min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    frame = build_frame(bars)
    for horizon in HORIZONS:
        frame = add_outcomes(frame, horizon, adverse_threshold_pct)
    required = [
        "adx",
        "er",
        "slow_vol_ratio",
        "bb_abs_z",
        "kc_abs_z",
        "ema_macd",
        "ema_signal",
        "hmacd",
        "hsignal",
    ]
    return frame.dropna(subset=required)


def write_report(results: pd.DataFrame, year: str, min_signals: int, output_dir: Path) -> None:
    report_path = output_dir / f"report_{year}_min{min_signals}.md"
    baseline = results.loc[results["tag"] == "current_mean_revert"].head(1)
    candidates = results.loc[results["tag"] == "candidate"].copy()
    over_60 = candidates.loc[candidates["all_horizons_over_60"]].copy()
    top_avg = candidates.sort_values(
        ["avg_right_rate_pct", "min_right_rate_pct", "signals"],
        ascending=[False, False, False],
    ).head(10)
    top_min = candidates.sort_values(
        ["min_right_rate_pct", "avg_right_rate_pct", "signals"],
        ascending=[False, False, False],
    ).head(10)

    def markdown_table(df: pd.DataFrame) -> str:
        if df.empty:
            return "_No rows._"
        text = df.copy()
        for col in text.columns:
            if pd.api.types.is_float_dtype(text[col]):
                text[col] = text[col].map(lambda v: "" if pd.isna(v) else f"{v:.2f}")
            else:
                text[col] = text[col].map(lambda v: "" if pd.isna(v) else str(v))
        header = "| " + " | ".join(text.columns) + " |"
        sep = "| " + " | ".join(["---"] * len(text.columns)) + " |"
        rows = [
            "| " + " | ".join(row) + " |"
            for row in text.astype(str).to_numpy().tolist()
        ]
        return "\n".join([header, sep, *rows])

    lines = [
        "# LazySwing Mean-Revert Classifier Grid",
        "",
        f"Year: `{year}`",
        f"Minimum signals: `{min_signals}`",
        "",
        "Strict correctness rule: `MAE <= -1%` and `abs(MAE) > MFE` over the horizon.",
        "",
        "## Current Classifier",
        "",
    ]
    if not baseline.empty:
        b = baseline.iloc[0]
        lines.extend([
            f"- Signals: `{int(b['signals'])}`",
            f"- H8 right-rate: `{b['h8_right_rate_pct']:.2f}%`",
            f"- H12 right-rate: `{b['h12_right_rate_pct']:.2f}%`",
            f"- H16 right-rate: `{b['h16_right_rate_pct']:.2f}%`",
        ])

    lines.extend([
        "",
        "## >60% Across All Horizons",
        "",
    ])
    if over_60.empty:
        lines.append("No candidate with the minimum signal count cleared 60% on all 8/12/16-bar horizons.")
    else:
        cols = [
            "signals",
            "avg_right_rate_pct",
            "min_right_rate_pct",
            "h8_right_rate_pct",
            "h12_right_rate_pct",
            "h16_right_rate_pct",
            "stretch_lookback",
            "kc_abs_z_min",
            "bb_abs_z_min",
            "adx_lookback",
            "prev_adx_min",
            "adx_drop_pct",
            "macd_rule",
            "macd_variant",
            "macd_lookback",
            "macd_compression_pct",
            "macd_min_gap_bps",
        ]
        lines.append(markdown_table(
            over_60.sort_values(["avg_right_rate_pct", "signals"], ascending=[False, False]).head(20)[cols]
        ))

    cols = [
        "signals",
        "avg_right_rate_pct",
        "min_right_rate_pct",
        "h8_right_rate_pct",
        "h12_right_rate_pct",
        "h16_right_rate_pct",
        "h8_continuation_rate_pct",
        "h12_continuation_rate_pct",
        "h16_continuation_rate_pct",
        "stretch_lookback",
        "kc_abs_z_min",
        "bb_abs_z_min",
        "adx_lookback",
        "prev_adx_min",
        "adx_drop_pct",
        "macd_rule",
        "macd_variant",
        "macd_lookback",
        "macd_compression_pct",
        "macd_min_gap_bps",
    ]
    lines.extend([
        "",
        "## Top By Average Right-Rate",
        "",
        markdown_table(top_avg[cols]),
        "",
        "## Top By Worst-Horizon Right-Rate",
        "",
        markdown_table(top_min[cols]),
        "",
    ])
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", choices=WINDOWS.keys(), default="2026")
    parser.add_argument("--min-signals", type=int, default=100)
    parser.add_argument("--adverse-threshold-pct", type=float, default=1.0)
    args = parser.parse_args()

    frame = build_scored_frame(args.year, args.adverse_threshold_pct)

    print(f"Year {args.year}: {len(frame)} valid 30m bars")
    results = run_grid(frame, args.min_signals)
    output_dir = REPO / "reports" / "lazyswing-mean-revert-classifier-grid"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"summary_{args.year}_min{args.min_signals}.csv"
    results.to_csv(csv_path, index=False)
    write_report(results, args.year, args.min_signals, output_dir)

    candidates = results.loc[results["tag"] == "candidate"].copy()
    winners = candidates.loc[candidates["all_horizons_over_60"]]
    print(f"Evaluated rows: {len(results)}")
    print(f"Candidates >60% on all horizons: {len(winners)}")
    if not winners.empty:
        print(
            winners.sort_values(["avg_right_rate_pct", "signals"], ascending=[False, False])
            .head(10)
            [[
                "signals",
                "avg_right_rate_pct",
                "min_right_rate_pct",
                "h8_right_rate_pct",
                "h12_right_rate_pct",
                "h16_right_rate_pct",
                "stretch_lookback",
                "kc_abs_z_min",
                "bb_abs_z_min",
                "adx_lookback",
                "prev_adx_min",
                "adx_drop_pct",
                "macd_rule",
                "macd_variant",
                "macd_lookback",
                "macd_compression_pct",
                "macd_min_gap_bps",
            ]]
            .to_string(index=False)
        )
    else:
        print(
            candidates.sort_values(
                ["min_right_rate_pct", "avg_right_rate_pct", "signals"],
                ascending=[False, False, False],
            )
            .head(10)
            [[
                "signals",
                "avg_right_rate_pct",
                "min_right_rate_pct",
                "h8_right_rate_pct",
                "h12_right_rate_pct",
                "h16_right_rate_pct",
                "stretch_lookback",
                "kc_abs_z_min",
                "bb_abs_z_min",
                "adx_lookback",
                "prev_adx_min",
                "adx_drop_pct",
                "macd_rule",
                "macd_variant",
                "macd_lookback",
                "macd_compression_pct",
                "macd_min_gap_bps",
            ]]
            .to_string(index=False)
        )
    print(f"Saved: {csv_path}")


if __name__ == "__main__":
    main()
