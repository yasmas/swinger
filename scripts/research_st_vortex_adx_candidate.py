#!/usr/bin/env python3
"""Small, inspectable ST/Vortex/ADX candidate runs for QQQ 2026."""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strategies.intraday_indicators import compute_realised_vol, compute_supertrend, compute_vortex  # noqa: E402
from strategies.macd_rsi_advanced import compute_adx  # noqa: E402

DATA_FILE = REPO / "data" / "QQQ-5m-2026.csv"
OUTPUT_ROOT = REPO / "reports" / "st-vortex-adx-candidate"
INITIAL_CASH = 100_000.0
COST_PCT = 0.05
SIGNAL_FREQ = "30min"


@dataclass(frozen=True)
class Candidate:
    name: str
    st_atr_period: int = 12
    st_multiplier: float = 1.5
    vortex_period: int = 21
    vortex_ema_period: int = 3
    vortex_predict: bool = True
    vortex_margin: float = 0.0
    adx_period: int = 12
    adx_floor: float = 20.0
    adx_near_floor: float = 18.0
    adx_slope_min: float = 0.6
    allow_short: bool = True
    rth_only_flips: bool = True
    vol_ratio_enabled: bool = False
    vol_ratio_short_period: int = 4
    vol_ratio_long_period: int = 48
    vol_ratio_min: float = 0.8
    reject_late_contradiction_bars: int = 8


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


def completed_signal_ohlcv(price: pd.DataFrame) -> pd.DataFrame:
    signal = (
        price.resample(SIGNAL_FREQ)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    if signal.empty:
        return signal
    last_5m_ts = price.index[-1]
    freq = pd.tseries.frequencies.to_offset(SIGNAL_FREQ)
    last_signal_start = last_5m_ts.floor(SIGNAL_FREQ)
    last_signal_end = last_signal_start + freq - pd.Timedelta(minutes=5)
    if last_5m_ts < last_signal_end:
        signal = signal.iloc[:-1]
    return signal


def build_indicators(signal: pd.DataFrame, c: Candidate) -> pd.DataFrame:
    st_line, st_bull = compute_supertrend(
        signal["high"], signal["low"], signal["close"], c.st_atr_period, c.st_multiplier
    )
    vi_plus, vi_minus = compute_vortex(
        signal["high"], signal["low"], signal["close"], c.vortex_period
    )
    vi_plus_ema = vi_plus.ewm(span=c.vortex_ema_period, adjust=False).mean()
    vi_minus_ema = vi_minus.ewm(span=c.vortex_ema_period, adjust=False).mean()
    vortex_diff = vi_plus_ema - vi_minus_ema
    adx = compute_adx(signal["high"], signal["low"], signal["close"], c.adx_period)
    vol_short = compute_realised_vol(signal["close"], c.vol_ratio_short_period, annualize=False)
    vol_long = vol_short.shift(1).rolling(c.vol_ratio_long_period, min_periods=c.vol_ratio_long_period).mean()
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
            "adx_slope": adx.diff(),
            "vol_ratio": vol_short / vol_long.replace(0.0, np.nan),
        },
        index=signal.index,
    )


def is_rth_exec(exec_ts: pd.Timestamp) -> bool:
    eastern = exec_ts.tz_localize("UTC").tz_convert("America/New_York")
    t = eastern.time()
    return pd.Timestamp("09:30").time() <= t <= pd.Timestamp("16:00").time()


def vortex_allows(row: pd.Series, direction: str, c: Candidate) -> bool:
    diff = row["vortex_diff"]
    slope = row["vortex_diff_slope"]
    if pd.isna(diff):
        return False
    if direction == "long" and diff >= c.vortex_margin:
        return True
    if direction == "short" and diff <= -c.vortex_margin:
        return True
    if not c.vortex_predict or pd.isna(slope):
        return False
    projected = diff + slope
    if direction == "long":
        return bool(projected >= c.vortex_margin and slope > 0)
    return bool(projected <= -c.vortex_margin and slope < 0)


def adx_allows(row: pd.Series, c: Candidate) -> bool:
    adx = row["adx"]
    slope = row["adx_slope"]
    if pd.isna(adx):
        return False
    if float(adx) >= c.adx_floor:
        return True
    return bool(
        not pd.isna(slope)
        and float(adx) >= c.adx_near_floor
        and float(slope) >= c.adx_slope_min
    )


def filters_allow(row: pd.Series, direction: str, c: Candidate) -> bool:
    if not vortex_allows(row, direction, c):
        return False
    if not adx_allows(row, c):
        return False
    if c.vol_ratio_enabled:
        ratio = row["vol_ratio"]
        if pd.isna(ratio) or float(ratio) < c.vol_ratio_min:
            return False
    return True


def simulate(price: pd.DataFrame, signal: pd.DataFrame, ind: pd.DataFrame, c: Candidate) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    cash = INITIAL_CASH
    long_qty = 0.0
    short_qty = 0.0
    total_costs = 0.0
    position = "flat"
    contradiction: str | None = None
    contradiction_start = -1
    actions: list[dict] = []
    equity: list[dict] = []
    prev_st: bool | None = None

    def value(px: float) -> float:
        return cash + long_qty * px - short_qty * px

    def log(ts: pd.Timestamp, action: str, qty: float, px: float, reason: str, row: pd.Series) -> None:
        actions.append(
            {
                "date": ts,
                "action": action,
                "quantity": qty,
                "price": px,
                "portfolio_value": value(px),
                "reason": reason,
                "st_bull": bool(row["st_bull"]),
                "vortex_diff": row["vortex_diff"],
                "vi_plus_ema": row["vi_plus_ema"],
                "vi_minus_ema": row["vi_minus_ema"],
                "adx": row["adx"],
                "adx_slope": row["adx_slope"],
                "vol_ratio": row["vol_ratio"],
            }
        )

    def close_long(px: float, ts: pd.Timestamp, reason: str, row: pd.Series) -> None:
        nonlocal cash, long_qty, total_costs, position
        if long_qty <= 0:
            return
        qty = long_qty
        notional = qty * px
        cash += notional
        total_costs += notional * COST_PCT / 100.0
        long_qty = 0.0
        position = "flat"
        log(ts, "SELL", qty, px, reason, row)

    def close_short(px: float, ts: pd.Timestamp, reason: str, row: pd.Series) -> None:
        nonlocal cash, short_qty, total_costs, position
        if short_qty <= 0:
            return
        qty = short_qty
        notional = qty * px
        cash -= notional
        total_costs += notional * COST_PCT / 100.0
        short_qty = 0.0
        position = "flat"
        log(ts, "COVER", qty, px, reason, row)

    def open_long(px: float, ts: pd.Timestamp, reason: str, row: pd.Series) -> None:
        nonlocal cash, long_qty, total_costs, position
        qty = cash * 0.9999 / px
        if qty <= 0:
            return
        notional = qty * px
        cash -= notional
        total_costs += notional * COST_PCT / 100.0
        long_qty = qty
        position = "long"
        log(ts, "BUY", qty, px, reason, row)

    def open_short(px: float, ts: pd.Timestamp, reason: str, row: pd.Series) -> None:
        nonlocal cash, short_qty, total_costs, position
        qty = value(px) * 0.9999 / px
        if qty <= 0:
            return
        notional = qty * px
        cash += notional
        total_costs += notional * COST_PCT / 100.0
        short_qty = qty
        position = "short"
        log(ts, "SHORT", qty, px, reason, row)

    warmup = max(c.st_atr_period * 2, c.vortex_period + c.vortex_ema_period + 3, c.adx_period + 3)
    if c.vol_ratio_enabled:
        warmup = max(warmup, c.vol_ratio_long_period + c.vol_ratio_short_period + 3)

    for i in range(warmup, len(signal)):
        sig_ts = signal.index[i]
        exec_ts = sig_ts + pd.Timedelta(minutes=25)
        if exec_ts not in price.index:
            continue
        px = float(price.loc[exec_ts, "close"])
        row = ind.iloc[i]
        st_bull = bool(row["st_bull"])
        desired = "long" if st_bull else "short"
        st_flip = prev_st is not None and st_bull != prev_st
        prev_st = st_bull

        if c.rth_only_flips and not is_rth_exec(exec_ts):
            equity.append({"date": exec_ts, "portfolio_value": value(px)})
            continue

        if contradiction and desired != contradiction:
            contradiction = None
            contradiction_start = -1

        if position == "flat":
            if desired == "long" and filters_allow(row, "long", c):
                open_long(px, exec_ts, "flat_st_long_confirmed", row)
            elif desired == "short" and c.allow_short and filters_allow(row, "short", c):
                open_short(px, exec_ts, "flat_st_short_confirmed", row)
            equity.append({"date": exec_ts, "portfolio_value": value(px)})
            continue

        if st_flip:
            if desired == "long":
                if position == "long":
                    contradiction = None
                    contradiction_start = -1
                    equity.append({"date": exec_ts, "portfolio_value": value(px)})
                    continue
                if filters_allow(row, "long", c):
                    close_short(px, exec_ts, "st_bull_confirmed", row)
                    open_long(px, exec_ts, "st_bull_confirmed", row)
                    contradiction = None
                else:
                    contradiction = "long"
                    contradiction_start = i
            else:
                if position == "short":
                    contradiction = None
                    contradiction_start = -1
                    equity.append({"date": exec_ts, "portfolio_value": value(px)})
                    continue
                if filters_allow(row, "short", c):
                    close_long(px, exec_ts, "st_bear_confirmed", row)
                    if c.allow_short:
                        open_short(px, exec_ts, "st_bear_confirmed", row)
                    contradiction = None
                else:
                    contradiction = "short"
                    contradiction_start = i
        elif contradiction:
            age = i - contradiction_start
            if age <= c.reject_late_contradiction_bars and filters_allow(row, contradiction, c):
                if contradiction == "long":
                    close_short(px, exec_ts, "delayed_bull_confirmed", row)
                    open_long(px, exec_ts, "delayed_bull_confirmed", row)
                else:
                    close_long(px, exec_ts, "delayed_bear_confirmed", row)
                    if c.allow_short:
                        open_short(px, exec_ts, "delayed_bear_confirmed", row)
                contradiction = None
                contradiction_start = -1
            elif age > c.reject_late_contradiction_bars:
                contradiction = None
                contradiction_start = -1

        equity.append({"date": exec_ts, "portfolio_value": value(px)})

    final_px = float(price["close"].iloc[-1])
    final_value = value(final_px)
    actions_df = pd.DataFrame(actions)
    equity_df = pd.DataFrame(equity)

    if equity_df.empty:
        max_dd = 0.0
        sharpe = 0.0
    else:
        values = equity_df["portfolio_value"]
        max_dd = float(((values - values.cummax()) / values.cummax() * 100.0).min())
        daily = equity_df.set_index("date")["portfolio_value"].resample("D").last().dropna().pct_change().dropna()
        sharpe = float(daily.mean() / daily.std() * math.sqrt(252)) if daily.std() > 0 else 0.0

    closed = []
    entry = None
    for _, row in actions_df.iterrows() if not actions_df.empty else []:
        if row["action"] in ("BUY", "SHORT"):
            entry = (row["action"], row["price"])
        elif row["action"] in ("SELL", "COVER") and entry is not None:
            side, entry_px = entry
            closed.append(row["price"] / entry_px - 1.0 if side == "BUY" else entry_px / row["price"] - 1.0)
            entry = None
    win_rate = float(np.mean([x > 0 for x in closed]) * 100.0) if closed else np.nan

    summary = {
        "name": c.name,
        "final_value": final_value,
        "gross_return_pct": (final_value / INITIAL_CASH - 1.0) * 100.0,
        "total_costs": total_costs,
        "after_cost_return_pct": ((final_value - total_costs) / INITIAL_CASH - 1.0) * 100.0,
        "max_dd_pct": max_dd,
        "sharpe": sharpe,
        "num_actions": int(len(actions_df)),
        "round_trips": int(len(closed)),
        "win_rate": win_rate,
        **asdict(c),
    }
    return summary, actions_df, equity_df


def candidates() -> list[Candidate]:
    return [
        Candidate(name="st12_15_vema3_adx12_floor20"),
        Candidate(name="st12_15_vema2_adx10_floor18_fast", vortex_ema_period=2, adx_period=10, adx_floor=18, adx_near_floor=16, adx_slope_min=0.5),
        Candidate(name="st12_175_vema3_adx12_floor20", st_multiplier=1.75),
        Candidate(name="st14_175_vema3_adx12_floor20", st_atr_period=14, st_multiplier=1.75),
        Candidate(name="st12_15_vema3_adx12_floor20_vol", vol_ratio_enabled=True, vol_ratio_min=0.8, vol_ratio_long_period=48),
        Candidate(name="st12_15_vema3_adx12_floor20_allhours", rth_only_flips=False),
        Candidate(name="st12_15_vema3_adx12_floor20_longonly", allow_short=False),
        Candidate(name="st10_15_vema2_adx10_floor18_fast", st_atr_period=10, vortex_ema_period=2, adx_period=10, adx_floor=18, adx_near_floor=16, adx_slope_min=0.5),
    ]


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    price = load_price_data()
    signal = completed_signal_ohlcv(price)
    bnh = (float(price["close"].iloc[-1]) / float(price["close"].iloc[0]) - 1.0) * 100.0

    rows = []
    for c in candidates():
        ind = build_indicators(signal, c)
        summary, actions, equity = simulate(price, signal, ind, c)
        summary["bnh_return_pct"] = bnh
        summary["target_2x_bnh_pct"] = bnh * 2.0
        rows.append(summary)
        actions.to_csv(OUTPUT_ROOT / f"{c.name}_actions.csv", index=False)
        equity.to_csv(OUTPUT_ROOT / f"{c.name}_equity.csv", index=False)
        (OUTPUT_ROOT / f"{c.name}_params.json").write_text(json.dumps(asdict(c), indent=2))
        print(
            f"{c.name:<42} net={summary['after_cost_return_pct']:+6.2f}% "
            f"gross={summary['gross_return_pct']:+6.2f}% WR={summary['win_rate']:5.1f}% "
            f"RT={summary['round_trips']:>3} DD={summary['max_dd_pct']:>6.2f}%",
            flush=True,
        )

    out = pd.DataFrame(rows).sort_values(["after_cost_return_pct", "sharpe"], ascending=[False, False])
    out.to_csv(OUTPUT_ROOT / "summary.csv", index=False)
    print("\nTop candidates", flush=True)
    print(
        out[
            [
                "name",
                "after_cost_return_pct",
                "gross_return_pct",
                "bnh_return_pct",
                "target_2x_bnh_pct",
                "win_rate",
                "round_trips",
                "num_actions",
                "max_dd_pct",
                "sharpe",
            ]
        ].to_string(index=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
