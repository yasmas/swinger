#!/usr/bin/env python3
"""Research sweep for a Supertrend/Vortex/ADX QQQ 2026 strategy.

This is a fast, self-contained harness used before promoting a candidate into
the strategy registry. It models completed 30m signal bars and trades at the
5m close that completes each signal bar.
"""

from __future__ import annotations

import itertools
import math
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
    compute_realised_vol,
    compute_supertrend,
    compute_vortex,
)
from strategies.macd_rsi_advanced import compute_adx  # noqa: E402

DATA_FILE = REPO / "data" / "QQQ-5m-2026.csv"
OUTPUT_ROOT = REPO / "reports" / "st-vortex-adx-qqq-2026"
SUMMARY_PATH = OUTPUT_ROOT / "summary.csv"

INITIAL_CASH = 100_000.0
COST_PCT = 0.05
SIGNAL_FREQ = "30min"


@dataclass(frozen=True)
class Params:
    st_atr_period: int
    st_multiplier: float
    vortex_period: int
    vortex_ema_period: int
    vortex_predict: bool
    vortex_margin: float
    adx_period: int
    adx_floor: float
    adx_near_floor: float
    adx_slope_min: float
    allow_short: bool
    rth_only_flips: bool
    vol_ratio_enabled: bool
    vol_ratio_short_period: int
    vol_ratio_long_period: int
    vol_ratio_min: float

    def tag(self) -> str:
        short = "ls" if self.allow_short else "lo"
        rth = "rth" if self.rth_only_flips else "all"
        vol = f"vr{self.vol_ratio_min:g}" if self.vol_ratio_enabled else "vr0"
        return (
            f"st{self.st_atr_period}x{self.st_multiplier:g}_"
            f"v{self.vortex_period}e{self.vortex_ema_period}_"
            f"adx{self.adx_period}f{self.adx_floor:g}_"
            f"{short}_{rth}_{vol}"
        ).replace(".", "p")


def load_price_data() -> pd.DataFrame:
    raw = pd.read_csv(DATA_FILE)
    timestamps = raw["open_time"].astype(float)
    ms_timestamps = timestamps.where(timestamps < 1e15, timestamps / 1000)
    raw["date"] = pd.to_datetime(ms_timestamps, unit="ms", utc=True)
    raw["date"] = raw["date"].dt.tz_localize(None)
    raw = raw.set_index("date").sort_index()
    for col in ["open", "high", "low", "close", "volume"]:
        raw[col] = raw[col].astype(float)
    return raw[["open", "high", "low", "close", "volume"]]


def completed_signal_ohlcv(price: pd.DataFrame, freq: str) -> pd.DataFrame:
    signal = (
        price.resample(freq)
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
    if signal.empty:
        return signal
    resample_freq = pd.tseries.frequencies.to_offset(freq)
    last_5m_ts = price.index[-1]
    last_signal_start = last_5m_ts.floor(freq)
    last_signal_end = last_signal_start + resample_freq - pd.Timedelta(minutes=5)
    if last_5m_ts < last_signal_end:
        signal = signal.iloc[:-1]
    return signal


def build_indicator_frame(signal: pd.DataFrame, p: Params) -> pd.DataFrame:
    st_line, st_bull = compute_supertrend(
        signal["high"],
        signal["low"],
        signal["close"],
        p.st_atr_period,
        p.st_multiplier,
    )
    vi_plus, vi_minus = compute_vortex(
        signal["high"],
        signal["low"],
        signal["close"],
        p.vortex_period,
    )
    vi_plus_ema = vi_plus.ewm(span=p.vortex_ema_period, adjust=False).mean()
    vi_minus_ema = vi_minus.ewm(span=p.vortex_ema_period, adjust=False).mean()
    vortex_diff = vi_plus_ema - vi_minus_ema
    adx = compute_adx(signal["high"], signal["low"], signal["close"], p.adx_period)
    adx_slope = adx.diff()

    vol_short = compute_realised_vol(
        signal["close"],
        period=p.vol_ratio_short_period,
        annualize=False,
    )
    vol_long = vol_short.shift(1).rolling(
        p.vol_ratio_long_period,
        min_periods=p.vol_ratio_long_period,
    ).mean()
    vol_ratio = vol_short / vol_long.replace(0.0, np.nan)

    return pd.DataFrame(
        {
            "st_line": st_line,
            "st_bull": st_bull.astype(bool),
            "vi_plus": vi_plus,
            "vi_minus": vi_minus,
            "vi_plus_ema": vi_plus_ema,
            "vi_minus_ema": vi_minus_ema,
            "vortex_diff": vortex_diff,
            "vortex_diff_slope": vortex_diff.diff(),
            "adx": adx,
            "adx_slope": adx_slope,
            "vol_ratio": vol_ratio,
        },
        index=signal.index,
    )


def is_rth_signal_close(signal_start: pd.Timestamp) -> bool:
    # Signal start is UTC-naive. The executable 5m close is at start + 25m.
    execution_ts = signal_start + pd.Timedelta(minutes=25)
    eastern = execution_ts.tz_localize("UTC").tz_convert("America/New_York")
    t = eastern.time()
    return t >= pd.Timestamp("09:30").time() and t <= pd.Timestamp("16:00").time()


def vortex_allows(row: pd.Series, prev: pd.Series, direction: str, p: Params) -> bool:
    diff = row["vortex_diff"]
    if pd.isna(diff):
        return False
    margin = p.vortex_margin
    if direction == "long" and diff >= margin:
        return True
    if direction == "short" and diff <= -margin:
        return True
    if not p.vortex_predict or prev is None:
        return False
    slope = row["vortex_diff_slope"]
    if pd.isna(slope):
        return False
    projected = diff + slope
    if direction == "long":
        return bool(projected >= margin and slope > 0)
    return bool(projected <= -margin and slope < 0)


def adx_allows(row: pd.Series, p: Params) -> bool:
    adx = row["adx"]
    slope = row["adx_slope"]
    if pd.isna(adx):
        return False
    if float(adx) >= p.adx_floor:
        return True
    if pd.isna(slope):
        return False
    return bool(float(adx) >= p.adx_near_floor and float(slope) >= p.adx_slope_min)


def vol_allows(row: pd.Series, p: Params) -> bool:
    if not p.vol_ratio_enabled:
        return True
    ratio = row["vol_ratio"]
    if pd.isna(ratio):
        return False
    return bool(float(ratio) >= p.vol_ratio_min)


def filters_allow(row: pd.Series, prev: pd.Series, direction: str, p: Params) -> bool:
    return (
        vortex_allows(row, prev, direction, p)
        and adx_allows(row, p)
        and vol_allows(row, p)
    )


def simulate(price: pd.DataFrame, signal: pd.DataFrame, ind: pd.DataFrame, p: Params) -> dict:
    cash = INITIAL_CASH
    long_qty = 0.0
    short_qty = 0.0
    total_costs = 0.0
    position = "flat"
    contradiction: str | None = None
    actions = []
    equity = []

    def mark_value(close: float) -> float:
        return cash + long_qty * close - short_qty * close

    def close_long(px: float, ts: pd.Timestamp, reason: str) -> None:
        nonlocal cash, long_qty, total_costs, position
        if long_qty <= 0:
            return
        notional = long_qty * px
        cash += notional
        total_costs += notional * COST_PCT / 100.0
        actions.append((ts, "SELL", px, reason))
        long_qty = 0.0
        position = "flat"

    def close_short(px: float, ts: pd.Timestamp, reason: str) -> None:
        nonlocal cash, short_qty, total_costs, position
        if short_qty <= 0:
            return
        notional = short_qty * px
        cash -= notional
        total_costs += notional * COST_PCT / 100.0
        actions.append((ts, "COVER", px, reason))
        short_qty = 0.0
        position = "flat"

    def open_long(px: float, ts: pd.Timestamp, reason: str) -> None:
        nonlocal cash, long_qty, total_costs, position
        qty = cash * 0.9999 / px
        if qty <= 0:
            return
        notional = qty * px
        cash -= notional
        total_costs += notional * COST_PCT / 100.0
        long_qty = qty
        position = "long"
        actions.append((ts, "BUY", px, reason))

    def open_short(px: float, ts: pd.Timestamp, reason: str) -> None:
        nonlocal cash, short_qty, total_costs, position
        qty = mark_value(px) * 0.9999 / px
        if qty <= 0:
            return
        notional = qty * px
        cash += notional
        total_costs += notional * COST_PCT / 100.0
        short_qty = qty
        position = "short"
        actions.append((ts, "SHORT", px, reason))

    start_i = max(
        p.st_atr_period * 2,
        p.vortex_period + p.vortex_ema_period + 3,
        p.adx_period + 3,
        p.vol_ratio_long_period if p.vol_ratio_enabled else 0,
    )
    prev_st = None

    for i in range(start_i, len(signal)):
        ts = signal.index[i]
        exec_ts = ts + pd.Timedelta(minutes=25)
        if exec_ts not in price.index:
            continue
        px = float(price.loc[exec_ts, "close"])
        row = ind.iloc[i]
        prev = ind.iloc[i - 1] if i > 0 else None
        if row.isna().all():
            continue

        st_bull = bool(row["st_bull"])
        desired = "long" if st_bull else "short"
        st_flip = prev_st is not None and st_bull != prev_st
        prev_st = st_bull

        if p.rth_only_flips and not is_rth_signal_close(ts):
            equity.append((exec_ts, mark_value(px)))
            continue

        if contradiction is not None and desired != contradiction:
            contradiction = None

        if position == "flat":
            if desired == "long" and filters_allow(row, prev, "long", p):
                open_long(px, exec_ts, "initial_st_long_confirmed")
            elif desired == "short" and p.allow_short and filters_allow(row, prev, "short", p):
                open_short(px, exec_ts, "initial_st_short_confirmed")
            equity.append((exec_ts, mark_value(px)))
            continue

        if st_flip:
            if desired == "long":
                if filters_allow(row, prev, "long", p):
                    close_short(px, exec_ts, "st_bull_confirmed")
                    open_long(px, exec_ts, "st_bull_confirmed")
                    contradiction = None
                else:
                    contradiction = "long"
            else:
                if not p.allow_short:
                    if filters_allow(row, prev, "short", p):
                        close_long(px, exec_ts, "st_bear_confirmed_long_only")
                        contradiction = None
                    else:
                        contradiction = "short"
                elif filters_allow(row, prev, "short", p):
                    close_long(px, exec_ts, "st_bear_confirmed")
                    open_short(px, exec_ts, "st_bear_confirmed")
                    contradiction = None
                else:
                    contradiction = "short"
        elif contradiction is not None:
            if contradiction == "long" and filters_allow(row, prev, "long", p):
                close_short(px, exec_ts, "delayed_bull_confirmed")
                open_long(px, exec_ts, "delayed_bull_confirmed")
                contradiction = None
            elif contradiction == "short" and filters_allow(row, prev, "short", p):
                if p.allow_short:
                    close_long(px, exec_ts, "delayed_bear_confirmed")
                    open_short(px, exec_ts, "delayed_bear_confirmed")
                else:
                    close_long(px, exec_ts, "delayed_bear_confirmed_long_only")
                contradiction = None

        equity.append((exec_ts, mark_value(px)))

    final_px = float(price["close"].iloc[-1])
    final_value = mark_value(final_px)
    after_cost_value = final_value - total_costs
    equity_series = pd.Series(
        [value for _, value in equity],
        index=[ts for ts, _ in equity],
        dtype=float,
    )
    if equity_series.empty:
        max_dd = 0.0
        sharpe = 0.0
    else:
        dd = (equity_series - equity_series.cummax()) / equity_series.cummax() * 100.0
        max_dd = float(dd.min())
        daily = equity_series.resample("D").last().dropna().pct_change().dropna()
        sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0

    closed = []
    entry = None
    for ts, action, px, reason in actions:
        if action in ("BUY", "SHORT"):
            entry = (action, px)
        elif action in ("SELL", "COVER") and entry is not None:
            side, entry_px = entry
            if side == "BUY":
                closed.append(px / entry_px - 1.0)
            else:
                closed.append(entry_px / px - 1.0)
            entry = None
    win_rate = float(np.mean([x > 0 for x in closed]) * 100.0) if closed else np.nan

    return {
        "tag": p.tag(),
        "return_pct": (final_value / INITIAL_CASH - 1.0) * 100.0,
        "after_cost_return_pct": (after_cost_value / INITIAL_CASH - 1.0) * 100.0,
        "max_dd_pct": max_dd,
        "sharpe": sharpe,
        "num_actions": len(actions),
        "round_trips": len(closed),
        "win_rate": win_rate,
        **p.__dict__,
    }


def build_param_grid() -> list[Params]:
    values = {
        "st_atr_period": [10, 12, 14],
        "st_multiplier": [1.5, 1.75, 2.0],
        "vortex_period": [14, 21],
        "vortex_ema_period": [2, 3, 5],
        "vortex_predict": [True, False],
        "vortex_margin": [0.0, 0.02],
        "adx_period": [10, 12, 14],
        "adx_floor": [18.0, 20.0, 22.0],
        "adx_near_floor": [16.0, 18.0],
        "adx_slope_min": [0.25, 0.75, 1.25],
        "allow_short": [True],
        "rth_only_flips": [True, False],
        "vol_ratio_enabled": [False, True],
        "vol_ratio_short_period": [4],
        "vol_ratio_long_period": [48, 96],
        "vol_ratio_min": [0.75, 1.0, 1.25],
    }
    keys = list(values.keys())
    params = []
    for combo in itertools.product(*(values[k] for k in keys)):
        item = dict(zip(keys, combo))
        if not item["vol_ratio_enabled"] and item["vol_ratio_min"] != 0.75:
            continue
        if not item["vol_ratio_enabled"] and item["vol_ratio_long_period"] != 48:
            continue
        if item["adx_near_floor"] >= item["adx_floor"]:
            continue
        params.append(Params(**item))
    return params


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    price = load_price_data()
    signal = completed_signal_ohlcv(price, SIGNAL_FREQ)

    bnh = (float(price["close"].iloc[-1]) / float(price["close"].iloc[0]) - 1.0) * 100.0
    print(f"Data: {price.index[0]} -> {price.index[-1]}")
    print(f"Buy-and-hold: {bnh:+.2f}%")

    rows = []
    params = build_param_grid()
    print(f"Testing {len(params)} parameter sets")
    for n, p in enumerate(params, start=1):
        ind = build_indicator_frame(signal, p)
        row = simulate(price, signal, ind, p)
        row["bnh_return_pct"] = bnh
        row["target_2x_bnh_pct"] = bnh * 2.0
        rows.append(row)
        if n % 250 == 0:
            print(f"  {n}/{len(params)}")

    out = pd.DataFrame(rows).sort_values(
        ["after_cost_return_pct", "sharpe", "max_dd_pct"],
        ascending=[False, False, False],
    )
    out.to_csv(SUMMARY_PATH, index=False)
    print("\nTop 20 after cost")
    print(
        out[
            [
                "tag",
                "after_cost_return_pct",
                "return_pct",
                "bnh_return_pct",
                "win_rate",
                "round_trips",
                "num_actions",
                "max_dd_pct",
                "sharpe",
                "st_atr_period",
                "st_multiplier",
                "vortex_period",
                "vortex_ema_period",
                "vortex_predict",
                "vortex_margin",
                "adx_period",
                "adx_floor",
                "adx_near_floor",
                "adx_slope_min",
                "rth_only_flips",
                "vol_ratio_enabled",
                "vol_ratio_long_period",
                "vol_ratio_min",
            ]
        ]
        .head(20)
        .to_string(index=False)
    )
    print(f"\nSummary: {SUMMARY_PATH}")


if __name__ == "__main__":
    main()

