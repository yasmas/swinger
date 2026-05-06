#!/usr/bin/env python3
"""Offline diagnostics for LazySwing momentum vs mean-reversion regimes.

This script does not run or modify the strategy. It reads historical OHLCV,
computes existing indicators on the LazySwing resample interval, and asks a
simple qualification question:

Do candidate regime signals separate bars that keep trending from bars that
show profit-then-giveback behavior where a trailing stop could help?
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strategies.intraday_indicators import (  # noqa: E402
    compute_bollinger,
    compute_keltner,
    compute_realised_vol,
    compute_supertrend,
)
from strategies.macd_rsi_advanced import compute_adx, compute_atr  # noqa: E402


DEFAULT_DATA_FILE = (
    "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv"
)


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    higher_means_momentum: bool = True


CANDIDATES = [
    Candidate("slow_vol_ratio", "24-bar realised vol / prior 336-bar mean"),
    Candidate("fast_vol_ratio", "4-bar realised vol / prior 336-bar mean"),
    Candidate("adx14", "ADX trend strength"),
    Candidate("adx14_delta_4", "4-bar ADX change"),
    Candidate("efficiency_24", "24-bar efficiency ratio"),
    Candidate("atr_pct", "ATR as percent of close"),
    Candidate("bb_width_pct", "Bollinger width as percent of midline"),
    Candidate("bb_abs_z", "absolute Bollinger z-score", higher_means_momentum=False),
    Candidate("kc_abs_z", "absolute Keltner mid distance in ATRs", higher_means_momentum=False),
    Candidate("squeeze_on", "Bollinger inside Keltner squeeze flag", higher_means_momentum=False),
]


def load_ohlcv(path: Path, start: str | None, end: str | None) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "open_time" in df.columns:
        df["date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    elif "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], utc=True)
    else:
        raise ValueError("CSV must include open_time or date")

    df = df.set_index("date").sort_index()
    keep_cols = ["open", "high", "low", "close", "volume"]
    missing = [col for col in keep_cols if col not in df.columns]
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")
    df = df[keep_cols].astype(float)

    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index < pd.Timestamp(end, tz="UTC")]
    if df.empty:
        raise ValueError("No rows remain after date filtering")
    return df


def resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    return (
        df.resample(interval)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )


def efficiency_ratio(closes: pd.Series, period: int) -> pd.Series:
    direction = (closes - closes.shift(period)).abs()
    volatility = closes.diff().abs().rolling(period).sum()
    return direction / volatility.replace(0.0, np.nan)


def prior_mean(series: pd.Series, period: int) -> pd.Series:
    return series.shift(1).rolling(period, min_periods=period).mean()


def build_indicator_frame(
    bars: pd.DataFrame,
    st_atr_period: int,
    st_multiplier: float,
    slow_vol_period: int = 24,
    slow_vol_long_period: int = 336,
    fast_vol_period: int = 4,
    adx_period: int = 14,
    adx_delta_bars: int = 4,
    efficiency_period: int = 24,
) -> pd.DataFrame:
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]
    volume = bars["volume"]

    _, st_bullish = compute_supertrend(high, low, close, st_atr_period, st_multiplier)
    direction = pd.Series(np.where(st_bullish, 1.0, -1.0), index=bars.index)

    slow_vol = compute_realised_vol(close, period=slow_vol_period)
    slow_vol_ratio = slow_vol / prior_mean(slow_vol, slow_vol_long_period).replace(0.0, np.nan)
    fast_vol = compute_realised_vol(close, period=fast_vol_period)
    fast_vol_ratio = fast_vol / prior_mean(fast_vol, slow_vol_long_period).replace(0.0, np.nan)

    adx14 = compute_adx(high, low, close, adx_period)
    atr = compute_atr(high, low, close, 14)
    bb_upper, bb_mid, bb_lower = compute_bollinger(close, 20, 2.0)
    kc_upper, kc_mid, kc_lower = compute_keltner(high, low, close, 20, 20, 1.5)

    bb_std = (bb_upper - bb_mid) / 2.0
    bb_z = (close - bb_mid) / bb_std.replace(0.0, np.nan)
    kc_half_width = (kc_upper - kc_lower) / 2.0
    kc_z = (close - kc_mid) / kc_half_width.replace(0.0, np.nan)
    squeeze_on = ((bb_lower > kc_lower) & (bb_upper < kc_upper)).astype(float)

    frame = pd.DataFrame(index=bars.index)
    frame["close"] = close
    frame["st_bullish"] = st_bullish.astype(bool)
    frame["direction"] = direction
    frame["slow_vol_ratio"] = slow_vol_ratio
    frame["fast_vol_ratio"] = fast_vol_ratio
    frame["adx14"] = adx14
    frame["adx14_delta_4"] = adx14.diff(adx_delta_bars)
    frame["efficiency_24"] = efficiency_ratio(close, efficiency_period)
    frame["atr_pct"] = atr / close * 100.0
    frame["bb_width_pct"] = (bb_upper - bb_lower) / bb_mid.replace(0.0, np.nan) * 100.0
    frame["bb_abs_z"] = bb_z.abs()
    frame["kc_abs_z"] = kc_z.abs()
    frame["squeeze_on"] = squeeze_on
    return frame


def assign_modes(
    frame: pd.DataFrame,
    momentum_adx_min: float,
    momentum_er_min: float,
    momentum_adx_delta_min: float,
    momentum_vol_ratio_max: float,
    breakout_adx_min: float,
    breakout_er_min: float,
    breakout_adx_delta_min: float,
    stretch_kc_z_min: float,
    stretch_bb_z_min: float,
    decay_adx_max: float,
    decay_er_max: float,
    decay_adx_delta_max: float,
) -> pd.DataFrame:
    """Assign plain-English regime modes from real-time indicator scores.

    The labels are mutually exclusive and intentionally conservative:
    - momentum protects strong trends from premature trailing stops.
    - mean_revert catches stretched moves with weak/fading confirmation.
    - momentum_decay catches non-stretched moves whose trend quality is fading.
    - neutral means the first-pass filter does not have enough confidence.
    """
    out = frame.copy()
    adx = out["adx14"]
    er = out["efficiency_24"]
    adx_delta = out["adx14_delta_4"]
    vol_ratio = out["slow_vol_ratio"]
    stretched = (out["kc_abs_z"] >= stretch_kc_z_min) | (
        out["bb_abs_z"] >= stretch_bb_z_min
    )

    smooth_momentum = (
        (adx >= momentum_adx_min)
        & (er >= momentum_er_min)
        & (adx_delta >= momentum_adx_delta_min)
        & (vol_ratio <= momentum_vol_ratio_max)
    )
    breakout_momentum = (
        (adx >= breakout_adx_min)
        & (er >= breakout_er_min)
        & (adx_delta >= breakout_adx_delta_min)
        & (vol_ratio > 1.0)
    )
    momentum = smooth_momentum | breakout_momentum

    trend_not_confirmed = (
        (adx < momentum_adx_min)
        | (er < momentum_er_min)
        | (adx_delta < 0.0)
    )
    weak_or_fading = (
        (adx <= decay_adx_max)
        | (er <= decay_er_max)
        | (adx_delta <= decay_adx_delta_max)
    )

    mode = pd.Series("neutral", index=out.index, dtype="object")
    mean_revert = stretched & trend_not_confirmed & ~momentum
    momentum_decay = ~stretched & weak_or_fading & ~momentum

    mode.loc[momentum] = "momentum"
    mode.loc[mean_revert] = "mean_revert"
    mode.loc[momentum_decay] = "momentum_decay"

    out["is_stretched"] = stretched
    out["smooth_momentum"] = smooth_momentum
    out["breakout_momentum"] = breakout_momentum
    out["trend_not_confirmed"] = trend_not_confirmed
    out["weak_or_fading"] = weak_or_fading
    out["regime_mode"] = mode
    out["trail_stop_allowed_mode"] = out["regime_mode"].isin(
        ["mean_revert", "momentum_decay"]
    )
    return out


def add_forward_labels(
    frame: pd.DataFrame,
    bars: pd.DataFrame,
    horizon_bars: int,
    min_gain_pct: float,
    trail_stop_pct: float,
    continuation_pct: float,
) -> pd.DataFrame:
    out = frame.copy()
    closes = bars["close"].reindex(out.index)
    highs = bars["high"].reindex(out.index)
    lows = bars["low"].reindex(out.index)
    dirs = out["direction"]

    fwd_return = pd.Series(np.nan, index=out.index)
    mfe = pd.Series(np.nan, index=out.index)
    mae = pd.Series(np.nan, index=out.index)

    for i in range(len(out) - horizon_bars):
        close_now = float(closes.iloc[i])
        if close_now <= 0 or not np.isfinite(close_now):
            continue
        direction = float(dirs.iloc[i])
        future_close = float(closes.iloc[i + horizon_bars])
        future_high = float(highs.iloc[i + 1 : i + horizon_bars + 1].max())
        future_low = float(lows.iloc[i + 1 : i + horizon_bars + 1].min())

        if direction > 0:
            fwd_return.iloc[i] = (future_close / close_now - 1.0) * 100.0
            mfe.iloc[i] = (future_high / close_now - 1.0) * 100.0
            mae.iloc[i] = (future_low / close_now - 1.0) * 100.0
        else:
            fwd_return.iloc[i] = (close_now / future_close - 1.0) * 100.0
            mfe.iloc[i] = (close_now / future_low - 1.0) * 100.0
            mae.iloc[i] = (close_now / future_high - 1.0) * 100.0

    out["fwd_return_pct"] = fwd_return
    out["mfe_pct"] = mfe.clip(lower=0.0)
    out["mae_pct"] = mae.clip(upper=0.0)
    out["giveback_pct"] = (out["mfe_pct"] - out["fwd_return_pct"]).clip(lower=0.0)
    out["continuation"] = out["fwd_return_pct"] >= continuation_pct
    out["trail_risk"] = (
        (out["mfe_pct"] >= min_gain_pct)
        & (out["giveback_pct"] >= trail_stop_pct)
    )
    return out


def summarize_bucket(df: pd.DataFrame, label: str) -> dict:
    return {
        "bucket": label,
        "n": int(len(df)),
        "fwd_return_mean_pct": df["fwd_return_pct"].mean(),
        "fwd_return_median_pct": df["fwd_return_pct"].median(),
        "mfe_mean_pct": df["mfe_pct"].mean(),
        "mae_mean_pct": df["mae_pct"].mean(),
        "giveback_mean_pct": df["giveback_pct"].mean(),
        "continuation_rate_pct": df["continuation"].mean() * 100.0,
        "trail_risk_rate_pct": df["trail_risk"].mean() * 100.0,
    }


def summarize_mode(df: pd.DataFrame, mode: str, total_rows: int) -> dict:
    row = summarize_bucket(df, mode)
    row["pct_of_bars"] = len(df) / total_rows * 100.0 if total_rows else np.nan
    row["avg_adx14"] = df["adx14"].mean()
    row["avg_adx14_delta_4"] = df["adx14_delta_4"].mean()
    row["avg_efficiency_24"] = df["efficiency_24"].mean()
    row["avg_slow_vol_ratio"] = df["slow_vol_ratio"].mean()
    row["avg_kc_abs_z"] = df["kc_abs_z"].mean()
    row["avg_bb_abs_z"] = df["bb_abs_z"].mean()
    row["stretched_rate_pct"] = df["is_stretched"].mean() * 100.0
    row["smooth_momentum_rate_pct"] = df["smooth_momentum"].mean() * 100.0
    row["breakout_momentum_rate_pct"] = df["breakout_momentum"].mean() * 100.0
    row["trail_stop_allowed"] = mode in {"mean_revert", "momentum_decay"}
    return row


def build_mode_summary(df: pd.DataFrame) -> pd.DataFrame:
    valid = df.loc[df["fwd_return_pct"].notna() & df["regime_mode"].notna()].copy()
    total = len(valid)
    rows = []
    for mode in ["momentum", "mean_revert", "momentum_decay", "neutral"]:
        subset = valid.loc[valid["regime_mode"] == mode]
        if not subset.empty:
            rows.append(summarize_mode(subset, mode, total))
    return pd.DataFrame(rows)


def mode_right_mask(df: pd.DataFrame, continuation_pct: float) -> pd.Series:
    """Evaluate whether each regime signal matched its intended outcome.

    These are diagnostic labels, not trading PnL:
    - momentum should offer enough favorable movement, and that favorable path
      should be better than the adverse path.
    - mean_revert should move materially against the current direction or
      produce trail-risk giveback.
    - momentum_decay should fail to produce a clean favorable path.
    - neutral is intentionally unscored.
    """
    mode = df["regime_mode"]
    mfe = df["mfe_pct"]
    abs_mae = df["mae_pct"].abs()
    clean_favorable_path = (mfe >= continuation_pct) & (mfe > abs_mae)
    materially_adverse = df["mae_pct"] <= -continuation_pct
    trail_risk = df["trail_risk"].astype(bool)

    right = pd.Series(pd.NA, index=df.index, dtype="boolean")
    right.loc[mode == "momentum"] = clean_favorable_path.loc[mode == "momentum"]
    right.loc[mode == "mean_revert"] = (
        materially_adverse.loc[mode == "mean_revert"]
        | trail_risk.loc[mode == "mean_revert"]
    )
    right.loc[mode == "momentum_decay"] = ~clean_favorable_path.loc[
        mode == "momentum_decay"
    ]
    return right


def right_rule_for_mode(mode: str, continuation_pct: float) -> str:
    if mode == "momentum":
        return f"MFE >= +{continuation_pct:.2f}% AND MFE > abs(MAE)"
    if mode == "mean_revert":
        return f"MAE <= -{continuation_pct:.2f}% OR trail-risk happened"
    if mode == "momentum_decay":
        return f"NOT (MFE >= +{continuation_pct:.2f}% AND MFE > abs(MAE))"
    return "not scored"


def summarize_mode_outcomes(
    df: pd.DataFrame,
    continuation_pct: float,
) -> pd.DataFrame:
    valid = df.loc[df["fwd_return_pct"].notna() & df["regime_mode"].notna()].copy()
    valid["year"] = valid.index.year
    valid["mode_right"] = mode_right_mask(valid, continuation_pct)

    rows = []
    for (year, mode), subset in valid.groupby(["year", "regime_mode"], sort=True):
        scored = subset["mode_right"].notna()
        right_count = int(subset.loc[scored, "mode_right"].sum())
        scored_count = int(scored.sum())
        wrong_count = scored_count - right_count
        rows.append(
            {
                "year": int(year),
                "mode": mode,
                "signals": int(len(subset)),
                "pct_of_year_bars": len(subset)
                / max(1, int((valid["year"] == year).sum()))
                * 100.0,
                "right_rule": right_rule_for_mode(mode, continuation_pct),
                "right": right_count if scored_count else "",
                "wrong": wrong_count if scored_count else "",
                "right_rate_pct": right_count / scored_count * 100.0
                if scored_count
                else np.nan,
                "avg_fwd_return_pct": subset["fwd_return_pct"].mean(),
                "median_fwd_return_pct": subset["fwd_return_pct"].median(),
                "avg_mfe_pct": subset["mfe_pct"].mean(),
                "avg_mae_pct": subset["mae_pct"].mean(),
                "avg_giveback_pct": subset["giveback_pct"].mean(),
                "continuation_rate_pct": subset["continuation"].mean() * 100.0,
                "trail_risk_rate_pct": subset["trail_risk"].mean() * 100.0,
                "reversal_rate_pct": (subset["fwd_return_pct"] <= 0.0).mean()
                * 100.0,
            }
        )
    order = {"momentum": 0, "mean_revert": 1, "momentum_decay": 2, "neutral": 3}
    out = pd.DataFrame(rows)
    out["_mode_order"] = out["mode"].map(order).fillna(99)
    out = out.sort_values(["year", "_mode_order"]).drop(columns=["_mode_order"])
    return out


def score_candidate(df: pd.DataFrame, candidate: Candidate) -> tuple[dict, list[dict]]:
    value = df[candidate.name]
    adjusted = value if candidate.higher_means_momentum else -value
    valid = df.loc[adjusted.notna() & df["fwd_return_pct"].notna()].copy()
    adjusted = adjusted.loc[valid.index]
    if len(valid) < 100:
        return {}, []

    low_cut = adjusted.quantile(0.25)
    high_cut = adjusted.quantile(0.75)
    mean_revert = valid.loc[adjusted <= low_cut]
    momentum = valid.loc[adjusted >= high_cut]
    if mean_revert.empty or momentum.empty:
        return {}, []

    mr = summarize_bucket(mean_revert, "mean_revert_q1")
    mom = summarize_bucket(momentum, "momentum_q4")

    return_spread = mom["fwd_return_mean_pct"] - mr["fwd_return_mean_pct"]
    continuation_spread = mom["continuation_rate_pct"] - mr["continuation_rate_pct"]
    trail_risk_spread = mr["trail_risk_rate_pct"] - mom["trail_risk_rate_pct"]
    giveback_spread = mr["giveback_mean_pct"] - mom["giveback_mean_pct"]
    qualification_score = (
        return_spread
        + 0.05 * continuation_spread
        + 0.05 * trail_risk_spread
        + 0.25 * giveback_spread
    )

    row = {
        "candidate": candidate.name,
        "description": candidate.description,
        "higher_means_momentum": candidate.higher_means_momentum,
        "valid_bars": int(len(valid)),
        "q1_cut_adjusted": low_cut,
        "q4_cut_adjusted": high_cut,
        "momentum_return_mean_pct": mom["fwd_return_mean_pct"],
        "mean_revert_return_mean_pct": mr["fwd_return_mean_pct"],
        "return_spread_pct": return_spread,
        "momentum_continuation_rate_pct": mom["continuation_rate_pct"],
        "mean_revert_continuation_rate_pct": mr["continuation_rate_pct"],
        "continuation_spread_pct": continuation_spread,
        "momentum_trail_risk_rate_pct": mom["trail_risk_rate_pct"],
        "mean_revert_trail_risk_rate_pct": mr["trail_risk_rate_pct"],
        "trail_risk_spread_pct": trail_risk_spread,
        "momentum_giveback_mean_pct": mom["giveback_mean_pct"],
        "mean_revert_giveback_mean_pct": mr["giveback_mean_pct"],
        "giveback_spread_pct": giveback_spread,
        "qualification_score": qualification_score,
    }

    buckets = []
    for summary in (mr, mom):
        buckets.append(
            {
                "candidate": candidate.name,
                "description": candidate.description,
                **summary,
            }
        )
    return row, buckets


def build_summaries(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    buckets = []
    for candidate in CANDIDATES:
        row, bucket_rows = score_candidate(df, candidate)
        if row:
            rows.append(row)
            buckets.extend(bucket_rows)
    summary = pd.DataFrame(rows).sort_values("qualification_score", ascending=False)
    bucket_summary = pd.DataFrame(buckets)
    return summary, bucket_summary


def print_summary(summary: pd.DataFrame, horizon_bars: int) -> None:
    cols = [
        "candidate",
        "qualification_score",
        "return_spread_pct",
        "trail_risk_spread_pct",
        "continuation_spread_pct",
        "mean_revert_trail_risk_rate_pct",
        "momentum_trail_risk_rate_pct",
    ]
    display = summary[cols].head(12).copy()
    for col in cols[1:]:
        display[col] = display[col].map(lambda x: f"{x:+.3f}")
    print(f"\nTop regime separators over {horizon_bars} resampled bars:\n")
    print(display.to_string(index=False))


def print_mode_summary(mode_summary: pd.DataFrame) -> None:
    cols = [
        "bucket",
        "n",
        "pct_of_bars",
        "fwd_return_mean_pct",
        "continuation_rate_pct",
        "trail_risk_rate_pct",
        "giveback_mean_pct",
        "avg_adx14",
        "avg_adx14_delta_4",
        "avg_efficiency_24",
        "avg_slow_vol_ratio",
        "stretched_rate_pct",
    ]
    display = mode_summary[cols].copy()
    for col in cols[2:]:
        display[col] = display[col].map(lambda x: f"{x:.3f}")
    print("\nThree-mode classifier summary:\n")
    print(display.to_string(index=False))


def print_outcome_summary(outcome_summary: pd.DataFrame) -> None:
    cols = [
        "year",
        "mode",
        "signals",
        "right",
        "wrong",
        "right_rate_pct",
        "avg_fwd_return_pct",
        "avg_mfe_pct",
        "avg_mae_pct",
        "avg_giveback_pct",
        "trail_risk_rate_pct",
    ]
    display = outcome_summary[cols].copy()
    for col in [
        "right_rate_pct",
        "avg_fwd_return_pct",
        "avg_mfe_pct",
        "avg_mae_pct",
        "avg_giveback_pct",
        "trail_risk_rate_pct",
    ]:
        display[col] = display[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
    print("\nYear-by-year right/wrong outcome summary:\n")
    print(display.to_string(index=False))


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._"

    formatted = df.copy()
    for col in formatted.columns:
        if pd.api.types.is_float_dtype(formatted[col]):
            formatted[col] = formatted[col].map(lambda x: "" if pd.isna(x) else f"{x:.3f}")
        else:
            formatted[col] = formatted[col].map(lambda x: "" if pd.isna(x) else str(x))

    headers = list(formatted.columns)
    rows = formatted.values.tolist()
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def write_markdown_report(
    path: Path,
    args: argparse.Namespace,
    raw: pd.DataFrame,
    bars: pd.DataFrame,
    mode_summary: pd.DataFrame,
    outcome_summary: pd.DataFrame,
    candidate_summary: pd.DataFrame,
) -> None:
    mode_display = mode_summary.copy()
    candidate_display = candidate_summary.head(8).copy()

    lines = [
        "# LazySwing Regime Filter Diagnostic",
        "",
        f"- Data: `{args.data_file}`",
        f"- Window: `{raw.index.min()}` to `{raw.index.max()}`",
        f"- Bars: `{len(bars):,}` at `{args.interval}`",
        f"- Forward horizon: `{args.horizon_bars}` bars",
        f"- Trail-risk label: MFE >= `{args.min_gain_pct}%` and giveback >= `{args.trail_stop_pct}%`",
        "",
        "## Mode Definitions",
        "",
        f"- `momentum`: either smooth momentum (`ADX >= {args.momentum_adx_min}`, `ER >= {args.momentum_er_min}`, `ADX delta >= {args.momentum_adx_delta_min}`, `slow vol ratio <= {args.momentum_vol_ratio_max}`) or breakout momentum (`ADX >= {args.breakout_adx_min}`, `ER >= {args.breakout_er_min}`, `ADX delta >= {args.breakout_adx_delta_min}`, `slow vol ratio > 1.0`).",
        f"- `mean_revert`: price is stretched (`KC abs z >= {args.stretch_kc_z_min}` or `BB abs z >= {args.stretch_bb_z_min}`) and trend is not confirmed.",
        f"- `momentum_decay`: price is not stretched, but trend is weak/fading (`ADX <= {args.decay_adx_max}` or `ER <= {args.decay_er_max}` or `ADX delta <= {args.decay_adx_delta_max}`).",
        "- `neutral`: the first-pass filter does not have enough conviction.",
        "",
        "## Mode Summary",
        "",
        markdown_table(mode_display),
        "",
        "## Year-by-Year Right / Wrong Outcomes",
        "",
        "The `right` column is mode-specific: momentum is right when MFE is large enough and better than MAE, mean_revert is right when MAE is materially adverse or trail-risk happens, and momentum_decay is right when price fails to create that clean favorable path.",
        "",
        markdown_table(outcome_summary),
        "",
        "## Top Raw Indicator Separators",
        "",
        markdown_table(candidate_display),
        "",
        "## Plain-English Reading",
        "",
        "Use `momentum` to avoid choking trend winners. Use `mean_revert` and `momentum_decay` as the first candidate set where a trailing stop may be allowed, but only after an actual open trade has enough unrealized profit.",
        "",
    ]
    path.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-file", default=DEFAULT_DATA_FILE)
    parser.add_argument("--label", default="eth_2024")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--interval", default="30min")
    parser.add_argument("--horizon-bars", type=int, default=8)
    parser.add_argument("--st-atr-period", type=int, default=25)
    parser.add_argument("--st-multiplier", type=float, default=1.75)
    parser.add_argument("--trail-stop-pct", type=float, default=1.0)
    parser.add_argument("--min-gain-pct", type=float, default=2.0)
    parser.add_argument("--continuation-pct", type=float, default=1.0)
    parser.add_argument("--momentum-adx-min", type=float, default=25.0)
    parser.add_argument("--momentum-er-min", type=float, default=0.35)
    parser.add_argument("--momentum-adx-delta-min", type=float, default=-1.0)
    parser.add_argument("--momentum-vol-ratio-max", type=float, default=1.20)
    parser.add_argument("--breakout-adx-min", type=float, default=20.0)
    parser.add_argument("--breakout-er-min", type=float, default=0.30)
    parser.add_argument("--breakout-adx-delta-min", type=float, default=2.0)
    parser.add_argument("--stretch-kc-z-min", type=float, default=1.0)
    parser.add_argument("--stretch-bb-z-min", type=float, default=1.5)
    parser.add_argument("--decay-adx-max", type=float, default=20.0)
    parser.add_argument("--decay-er-max", type=float, default=0.25)
    parser.add_argument("--decay-adx-delta-max", type=float, default=-2.0)
    args = parser.parse_args()

    raw = load_ohlcv(REPO / args.data_file, args.start, args.end)
    bars = resample_ohlcv(raw, args.interval)
    frame = build_indicator_frame(
        bars,
        st_atr_period=args.st_atr_period,
        st_multiplier=args.st_multiplier,
    )
    frame = add_forward_labels(
        frame,
        bars,
        horizon_bars=args.horizon_bars,
        min_gain_pct=args.min_gain_pct,
        trail_stop_pct=args.trail_stop_pct,
        continuation_pct=args.continuation_pct,
    )
    frame = assign_modes(
        frame,
        momentum_adx_min=args.momentum_adx_min,
        momentum_er_min=args.momentum_er_min,
        momentum_adx_delta_min=args.momentum_adx_delta_min,
        momentum_vol_ratio_max=args.momentum_vol_ratio_max,
        breakout_adx_min=args.breakout_adx_min,
        breakout_er_min=args.breakout_er_min,
        breakout_adx_delta_min=args.breakout_adx_delta_min,
        stretch_kc_z_min=args.stretch_kc_z_min,
        stretch_bb_z_min=args.stretch_bb_z_min,
        decay_adx_max=args.decay_adx_max,
        decay_er_max=args.decay_er_max,
        decay_adx_delta_max=args.decay_adx_delta_max,
    )

    usable = frame.dropna(subset=["fwd_return_pct"]).copy()
    summary, bucket_summary = build_summaries(usable)
    mode_summary = build_mode_summary(usable)
    outcome_summary = summarize_mode_outcomes(
        usable,
        continuation_pct=args.continuation_pct,
    )

    out_dir = REPO / "reports" / "lazyswing-regime-filter" / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    frame_path = out_dir / "regime_frame.csv"
    summary_path = out_dir / "candidate_summary.csv"
    bucket_path = out_dir / "bucket_summary.csv"
    mode_path = out_dir / "mode_summary.csv"
    outcome_path = out_dir / "mode_outcome_summary.csv"
    report_path = out_dir / "regime_report.md"
    frame.to_csv(frame_path, index_label="date")
    summary.to_csv(summary_path, index=False)
    bucket_summary.to_csv(bucket_path, index=False)
    mode_summary.to_csv(mode_path, index=False)
    outcome_summary.to_csv(outcome_path, index=False)
    write_markdown_report(
        report_path,
        args,
        raw,
        bars,
        mode_summary,
        outcome_summary,
        summary,
    )

    print(
        "LazySwing regime diagnostics\n"
        f"data={args.data_file}\n"
        f"window={raw.index.min()} -> {raw.index.max()}\n"
        f"bars={len(bars):,} interval={args.interval} horizon={args.horizon_bars}\n"
        f"trail_risk: mfe>={args.min_gain_pct}% and giveback>={args.trail_stop_pct}%\n"
        "mode thresholds: "
        f"momentum_adx>={args.momentum_adx_min}, "
        f"momentum_er>={args.momentum_er_min}, "
        f"momentum_adx_delta>={args.momentum_adx_delta_min}, "
        f"momentum_vol_ratio<={args.momentum_vol_ratio_max}, "
        f"stretch kc/bb>={args.stretch_kc_z_min}/{args.stretch_bb_z_min}"
    )
    print_mode_summary(mode_summary)
    print_outcome_summary(outcome_summary)
    print_summary(summary, args.horizon_bars)
    print(f"\nSaved frame:   {frame_path}")
    print(f"Saved summary: {summary_path}")
    print(f"Saved buckets: {bucket_path}")
    print(f"Saved modes:   {mode_path}")
    print(f"Saved outcome: {outcome_path}")
    print(f"Saved report:  {report_path}")


if __name__ == "__main__":
    main()
