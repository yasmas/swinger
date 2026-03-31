"""Analyze flat trades from the LazySwing dev backtest.

Computes volatility and directional-pressure indicators at each trade's entry,
then tests whether trades that end up "flat" (< 0.25% per holding day) correlate
with any of those indicators.

Volatility indicators (computed on 1h bars):
  1. ATR% — ATR(14) / close  (normalised volatility)
  2. Bollinger Band Width — (upper - lower) / middle, period 20, 2 std
  3. Realised Volatility — rolling 20-period std-dev of hourly log returns

Directional-pressure indicators (volume-based, 1h bars):
  1. OBV Slope — slope of OBV over the last 20 bars (linear regression)
  2. Volume Imbalance Ratio — sum(volume on up bars) / sum(volume on down bars)
     over the last 20 bars

Usage:
    PYTHONPATH=src python analyze_flat_trades.py
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

TRADE_LOG_PATH = "reports/LazySwing_Dev_lazy_swing_v3.csv"
PRICE_DATA_PATH = "data/BTCUSDT-5m-2022-2024-combined.csv"
FLAT_THRESHOLD_PCT_PER_DAY = 0.25
LOOKBACK = 20  # periods for rolling indicators

# ── Load data ────────────────────────────────────────────────────────────────

def load_price_data(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    timestamps = df["open_time"].astype(float)
    threshold = 1e15
    ms_ts = timestamps.where(timestamps < threshold, timestamps / 1000)
    df["date"] = pd.to_datetime(ms_ts, unit="ms")
    df = df.set_index("date")
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def resample_hourly(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("1h").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])


# ── Indicator computation on hourly bars ─────────────────────────────────────

def compute_atr(h: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = pd.concat([
        h["high"] - h["low"],
        (h["high"] - h["close"].shift(1)).abs(),
        (h["low"] - h["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_atr_pct(h: pd.DataFrame, period: int = 14) -> pd.Series:
    return compute_atr(h, period) / h["close"] * 100


def compute_bbw(h: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> pd.Series:
    """Bollinger Band Width = (upper - lower) / middle."""
    middle = h["close"].rolling(period).mean()
    std = h["close"].rolling(period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return (upper - lower) / middle * 100


def compute_realised_vol(h: pd.DataFrame, period: int = 20) -> pd.Series:
    log_ret = np.log(h["close"] / h["close"].shift(1))
    return log_ret.rolling(period).std() * 100


def compute_obv(h: pd.DataFrame) -> pd.Series:
    direction = np.sign(h["close"].diff())
    return (h["volume"] * direction).cumsum()


def compute_obv_slope(h: pd.DataFrame, period: int = 20) -> pd.Series:
    """Slope of OBV via rolling linear regression over `period` bars."""
    obv = compute_obv(h)
    x = np.arange(period, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    slopes = pd.Series(np.nan, index=obv.index)
    obv_vals = obv.values
    for i in range(period - 1, len(obv_vals)):
        window = obv_vals[i - period + 1 : i + 1]
        if np.any(np.isnan(window)):
            continue
        y_mean = window.mean()
        slopes.iloc[i] = ((x - x_mean) * (window - y_mean)).sum() / x_var
    return slopes


def compute_volume_imbalance(h: pd.DataFrame, period: int = 20) -> pd.Series:
    """Ratio of volume on up-candles to volume on down-candles, rolling."""
    up = (h["close"] >= h["open"]).astype(float)
    down = 1.0 - up
    vol_up = (h["volume"] * up).rolling(period).sum()
    vol_down = (h["volume"] * down).rolling(period).sum()
    return vol_up / vol_down.replace(0, np.nan)


# ── Pair entries with exits ──────────────────────────────────────────────────

def build_trades(trade_log_path: str) -> pd.DataFrame:
    df = pd.read_csv(trade_log_path)
    df["date"] = pd.to_datetime(df["date"])
    actions = df[df["action"].isin(["BUY", "SELL", "SHORT", "COVER"])].copy()
    actions["details_parsed"] = actions["details"].apply(json.loads)

    trades = []
    pending_entry = None

    for _, row in actions.iterrows():
        action = row["action"]
        if action in ("BUY", "SHORT"):
            pending_entry = row
        elif action in ("SELL", "COVER") and pending_entry is not None:
            details = row["details_parsed"]
            entry_details = pending_entry["details_parsed"]
            pnl_pct = details.get("pnl_pct", 0.0)
            bars_held = details.get("bars_held", 1)
            hours_held = bars_held * 5 / 60
            days_held = hours_held / 24
            if days_held < 1 / 24:  # less than 1 hour, set floor
                days_held = 1 / 24
            pnl_per_day = pnl_pct / days_held

            trades.append({
                "entry_date": pending_entry["date"],
                "exit_date": row["date"],
                "direction": "long" if pending_entry["action"] == "BUY" else "short",
                "entry_price": pending_entry["price"],
                "exit_price": row["price"],
                "pnl_pct": pnl_pct,
                "bars_held": bars_held,
                "days_held": round(days_held, 4),
                "pnl_pct_per_day": round(pnl_per_day, 4),
                "exit_reason": details.get("exit_reason", ""),
                "entry_reason": entry_details.get("entry_reason", ""),
            })
            pending_entry = None

    return pd.DataFrame(trades)


# ── Main analysis ────────────────────────────────────────────────────────────

def main():
    print("Loading data ...")
    price_5m = load_price_data(PRICE_DATA_PATH)
    hourly = resample_hourly(price_5m)
    print(f"  5m bars: {len(price_5m):,},  1h bars: {len(hourly):,}")

    print("Computing indicators on 1h bars ...")
    ind = pd.DataFrame(index=hourly.index)
    ind["atr_pct"] = compute_atr_pct(hourly)
    ind["bbw"] = compute_bbw(hourly)
    ind["realised_vol"] = compute_realised_vol(hourly)
    ind["obv_slope"] = compute_obv_slope(hourly)
    ind["volume_imbalance"] = compute_volume_imbalance(hourly)
    print(f"  Indicators computed: {list(ind.columns)}")

    print("Building trade pairs ...")
    trades = build_trades(TRADE_LOG_PATH)
    print(f"  Total round-trip trades: {len(trades)}")

    # Look up indicator values at each trade's entry (floor to nearest hour)
    for col in ind.columns:
        vals = []
        for entry_dt in trades["entry_date"]:
            floored = entry_dt.floor("h")
            idx = ind.index.get_indexer([floored], method="ffill")[0]
            if idx >= 0:
                vals.append(ind[col].iloc[idx])
            else:
                vals.append(np.nan)
        trades[col] = vals

    # Classify flat
    trades["abs_pnl_per_day"] = trades["pnl_pct_per_day"].abs()
    trades["is_flat"] = (trades["abs_pnl_per_day"] < FLAT_THRESHOLD_PCT_PER_DAY).astype(int)

    n_flat = trades["is_flat"].sum()
    n_non_flat = len(trades) - n_flat
    print(f"\n  Flat trades (< {FLAT_THRESHOLD_PCT_PER_DAY}%/day): {n_flat}")
    print(f"  Non-flat trades: {n_non_flat}")

    # ── Correlation analysis ─────────────────────────────────────────────
    indicator_cols = ["atr_pct", "bbw", "realised_vol", "obv_slope", "volume_imbalance"]
    indicator_labels = {
        "atr_pct":           "ATR% (14)",
        "bbw":               "Bollinger Band Width (20, 2σ)",
        "realised_vol":      "Realised Volatility (20h)",
        "obv_slope":         "OBV Slope (20h)",
        "volume_imbalance":  "Volume Imbalance Ratio (20h)",
    }

    print("\n" + "=" * 80)
    print("CORRELATION ANALYSIS: Indicator values at entry vs flat outcome")
    print("=" * 80)

    for col in indicator_cols:
        valid = trades[[col, "is_flat", "abs_pnl_per_day"]].dropna()
        if len(valid) < 10:
            print(f"\n{indicator_labels[col]}: insufficient data")
            continue

        # Point-biserial: is_flat (binary) vs indicator (continuous)
        pb_r, pb_p = sp_stats.pointbiserialr(valid["is_flat"], valid[col])

        # Pearson: abs_pnl_per_day vs indicator
        pr_r, pr_p = sp_stats.pearsonr(valid["abs_pnl_per_day"], valid[col])

        # Spearman: more robust to outliers
        sp_r, sp_p = sp_stats.spearmanr(valid["abs_pnl_per_day"], valid[col])

        # Mean indicator value for flat vs non-flat
        flat_mean = valid.loc[valid["is_flat"] == 1, col].mean()
        non_flat_mean = valid.loc[valid["is_flat"] == 0, col].mean()

        print(f"\n{'─' * 70}")
        print(f"  {indicator_labels[col]}")
        print(f"{'─' * 70}")
        print(f"  Mean at entry (flat trades):     {flat_mean:>12.4f}")
        print(f"  Mean at entry (non-flat trades):  {non_flat_mean:>12.4f}")
        print(f"  Δ (flat − non-flat):             {flat_mean - non_flat_mean:>12.4f}")
        print()
        print(f"  Point-biserial  r = {pb_r:+.4f}   p = {pb_p:.4f}  {'***' if pb_p < 0.001 else '**' if pb_p < 0.01 else '*' if pb_p < 0.05 else ''}")
        print(f"  Pearson(|pnl/d| vs ind)  r = {pr_r:+.4f}   p = {pr_p:.4f}  {'***' if pr_p < 0.001 else '**' if pr_p < 0.01 else '*' if pr_p < 0.05 else ''}")
        print(f"  Spearman(|pnl/d| vs ind) ρ = {sp_r:+.4f}   p = {sp_p:.4f}  {'***' if sp_p < 0.001 else '**' if sp_p < 0.01 else '*' if sp_p < 0.05 else ''}")

    # ── Export flat trades CSV ───────────────────────────────────────────
    flat_trades = trades[trades["is_flat"] == 1].copy()
    flat_csv_path = "reports/flat_trades_dev.csv"
    export_cols = [
        "entry_date", "exit_date", "direction", "entry_price", "exit_price",
        "pnl_pct", "days_held", "pnl_pct_per_day", "entry_reason", "exit_reason",
    ] + indicator_cols
    flat_trades[export_cols].to_csv(flat_csv_path, index=False, float_format="%.4f")
    print(f"\n{'=' * 80}")
    print(f"Flat trades CSV exported: {flat_csv_path}  ({len(flat_trades)} trades)")
    print("=" * 80)

    # ── Summary table ────────────────────────────────────────────────────
    print(f"\nFlat trade breakdown by direction:")
    for d in ["long", "short"]:
        sub = flat_trades[flat_trades["direction"] == d]
        print(f"  {d:>5s}: {len(sub)} trades, avg pnl/day = {sub['pnl_pct_per_day'].mean():+.4f}%")

    print(f"\nFlat trade breakdown by exit reason:")
    for reason in flat_trades["exit_reason"].unique():
        sub = flat_trades[flat_trades["exit_reason"] == reason]
        print(f"  {reason}: {len(sub)} trades")


if __name__ == "__main__":
    main()
