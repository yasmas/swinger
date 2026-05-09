#!/usr/bin/env python3
"""For 5 trades on 2026_Q1, show per-bar histogram + filter status to explain
why hist_x50/x75 underperformed hist_x0 in the grid.

Picks a backtest config (BC_n6_f8s21g9_cross_mg2.0 — the cross winner) to
establish actual trade boundaries (entry/exit), then walks each trade bar by
bar showing: histogram, running peak, and which of {cross, hist_x0, hist_x50,
hist_x75} would have fired and on which bars.

Also reports whether B (adx_exhaustion) fired during the trade and whether any
combined_bc(B, C-filter) AND-gate would have triggered within window=6.
"""
from __future__ import annotations

import sys
import tempfile
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import Config  # noqa: E402
from controller import Controller  # noqa: E402
from trade_log import TradeLogReader  # noqa: E402
from strategies.macd_rsi_advanced import compute_adx  # noqa: E402

SLICE_FILE = REPO / "data" / "backtests" / "eth" / "profit_exit_grid_slices" / "2026_Q1.csv"
RESAMPLE = "30min"

# Pinned signal params (the ones we actually tested in the grid)
B_PARAMS = {
    "adx_lookback": 6,
    "adx_drop_pct": 3.5,
    "prev_adx_min": 20.0,
}
MACD = {"fast": 8, "slow": 21, "signal": 9}
WINDOW_BARS_5M = 6  # combined_bc N=6


def _build_indicators() -> pd.DataFrame:
    """Load 5m → resample to 30min → compute MACD hist + ADX series."""
    df5 = pd.read_csv(SLICE_FILE)
    df5["dt"] = pd.to_datetime(df5["open_time"], unit="ms", utc=True)
    df5 = df5.set_index("dt")[["open", "high", "low", "close", "volume"]]

    o = df5["open"].resample(RESAMPLE).first()
    h = df5["high"].resample(RESAMPLE).max()
    l = df5["low"].resample(RESAMPLE).min()
    c = df5["close"].resample(RESAMPLE).last()
    v = df5["volume"].resample(RESAMPLE).sum()
    df30 = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}).dropna()

    ema_f = df30["close"].ewm(span=MACD["fast"], adjust=False).mean()
    ema_s = df30["close"].ewm(span=MACD["slow"], adjust=False).mean()
    df30["macd_line"] = ema_f - ema_s
    df30["macd_sig"] = df30["macd_line"].ewm(span=MACD["signal"], adjust=False).mean()
    df30["hist"] = df30["macd_line"] - df30["macd_sig"]

    df30["adx"] = compute_adx(df30["high"], df30["low"], df30["close"], period=14)
    df30["adx_pct_change"] = df30["adx"] / df30["adx"].shift(B_PARAMS["adx_lookback"]) - 1.0
    df30["adx_pct_change"] = df30["adx_pct_change"] * 100.0

    return df30


def _run_backtest_for_trades() -> pd.DataFrame:
    """Run BC_n6_f8s21g9_cross_mg2.0 to get actual entry/exit boundaries."""
    BASE = {
        "resample_interval": RESAMPLE,
        "supertrend_atr_period": 25, "supertrend_multiplier": 1.75,
        "adaptive_st_vol_period": 24, "adaptive_st_vol_long_period": 336,
        "adaptive_st_enter_ratio_threshold": 1.0,
        "adaptive_st_exit_ratio_threshold": 0.85,
        "adaptive_st_min_high_bars": 48,
        "flip_vol_ratio_enabled": True,
        "flip_vol_ratio_short_period": 4, "flip_vol_ratio_long_period": 336,
        "flip_vol_ratio_regime_mode": "squared",
        "flip_vol_ratio_regime_low_min": 0.7, "flip_vol_ratio_regime_high_min": 1.0,
        "flip_vol_ratio_regime_low_stop_pct": 1.0,
        "flip_vol_ratio_regime_high_stop_pct": 2.5,
        "flip_vol_ratio_regime_power": 1.5,
        "hmacd_fast": 24, "hmacd_slow": 51, "hmacd_signal": 12,
        "cost_per_trade_pct": 0.05,
        "fast_exit_enabled": True, "fast_exit_cooldown_bars": 4,
        "fast_exit_rvol_short_period": 24, "fast_exit_rvol_long_period": 2016,
        "fast_exit_rvol_low_min": 1.1, "fast_exit_rvol_high_min": 1.3,
        "fast_exit_reentry_confirm": True,
        "flat_realign_hourly_closes": 0,
        "regime_trail_enabled": True,
        "regime_momentum_adx_period": 14, "regime_momentum_adx_min": 40.0,
        "regime_momentum_er_period": 24, "regime_momentum_er_min": 0.40,
        "regime_momentum_adx_delta_bars": 2, "regime_momentum_adx_delta_min": 1.0,
        "regime_momentum_vol_period": 24, "regime_momentum_vol_long_period": 336,
        "regime_momentum_vol_ratio_max": 1.0,
        "trail_stop_pct": 0.75, "trail_stop_atr_multiple": 0.75,
        "trail_stop_cooldown_bars": 0, "trail_stop_reentry_enabled": False,
        "trail_stop_exit_on_signal": True,
        "regime_trail_mode": "combined_bc",
        "regime_exhaustion_adx_lookback": B_PARAMS["adx_lookback"],
        "regime_exhaustion_adx_drop_pct": B_PARAMS["adx_drop_pct"],
        "regime_exhaustion_prev_adx_min": B_PARAMS["prev_adx_min"],
        "profit_exit_macd_fast": MACD["fast"],
        "profit_exit_macd_slow": MACD["slow"],
        "profit_exit_macd_signal_period": MACD["signal"],
        "profit_exit_macd_condition": "cross",
        "profit_exit_macd_histogram_bars": 2,
        "combined_bc_window_bars": WINDOW_BARS_5M,
        "trail_stop_min_gain_pct": 2.0,
    }
    cfg = Config({
        "backtest": {
            "name": "filter_analysis", "version": "filter-analysis",
            "initial_cash": 100000.0,
            "start_date": "2026-01-01", "end_date": "2026-04-01",
        },
        "data_source": {
            "type": "csv_file", "parser": "coinbase_intx_kline",
            "params": {"file_path": str(SLICE_FILE), "symbol": "ETH-PERP-INTX"},
        },
        "strategies": [{"type": "lazy_swing", "params": BASE}],
    })
    tmp = tempfile.mkdtemp(prefix="filter_an_")
    try:
        result = Controller(cfg, output_dir=tmp).run()[0]
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return tl


def _pair_trades(tl: pd.DataFrame) -> list[dict]:
    """Pair entry (BUY/SHORT) → exit (SELL/COVER) bars; return list of dicts."""
    trades = []
    cur_entry = None
    for _, row in tl.iterrows():
        action = row["action"]
        ts_raw = pd.Timestamp(row["date"])
        ts = ts_raw if ts_raw.tzinfo else ts_raw.tz_localize("UTC")
        price = float(row["price"])
        if action in ("BUY", "SHORT"):
            cur_entry = {
                "entry_time": ts, "entry_price": price,
                "direction": "long" if action == "BUY" else "short",
            }
        elif action in ("SELL", "COVER") and cur_entry is not None:
            details = row.get("details") or {}
            cur_entry.update({
                "exit_time": ts, "exit_price": price,
                "pnl_pct": float(details.get("pnl_pct", 0.0)),
                "exit_reason": details.get("exit_reason", "unknown"),
            })
            trades.append(cur_entry)
            cur_entry = None
    return trades


def _filter_status(direction: str, hist_seq: list[float], peak: float | None) -> dict:
    """Given the last 3 hist values [curr, prev, prev2], compute filter fires."""
    if len(hist_seq) < 3:
        return {"x0": False, "x50": False, "x75": False, "cross": False}
    curr, prev, prev2 = hist_seq[0], hist_seq[1], hist_seq[2]

    if direction == "long":
        consec = (curr < prev) and (prev < prev2)
        cross = (prev >= 0.0) and (curr < 0.0)
        x0 = consec
        x50 = x75 = False
        if consec and peak is not None and peak > 0.0:
            x50 = curr <= peak * 0.5
            x75 = curr <= peak * 0.25
    else:
        consec = (curr > prev) and (prev > prev2)
        cross = (prev <= 0.0) and (curr > 0.0)
        x0 = consec
        x50 = x75 = False
        if consec and peak is not None and peak < 0.0:
            x50 = curr >= peak * 0.5
            x75 = curr >= peak * 0.25
    return {"x0": x0, "x50": x50, "x75": x75, "cross": cross}


def _b_signal(adx_pct_change: float, prev_adx: float) -> bool:
    return (
        not pd.isna(prev_adx)
        and not pd.isna(adx_pct_change)
        and prev_adx >= B_PARAMS["prev_adx_min"]
        and adx_pct_change <= -B_PARAMS["adx_drop_pct"]
    )


def _analyze_trade(trade: dict, ind: pd.DataFrame, idx: int) -> None:
    direction = trade["direction"]
    entry_t = trade["entry_time"]
    exit_t = trade["exit_time"]

    # Find the 30-min bars from entry to exit (inclusive)
    mask = (ind.index >= entry_t.floor(RESAMPLE)) & (ind.index <= exit_t.ceil(RESAMPLE))
    sub = ind.loc[mask].copy()

    if len(sub) < 3:
        print(f"  [trade too short to analyze: {len(sub)} bars]")
        return

    # Track running peak
    peak: float | None = None
    rows = []
    fire_record = {"x0": [], "x50": [], "x75": [], "cross": [], "B": [], "B_then_C_within_N": []}
    # Look back over the last `WINDOW_BARS_5M / 6 = 1` 30m-bars-equiv... actually
    # the BC window is 6 5m bars = 1 30m bar in the worst case. So when checking
    # "B fired within last N bars", in 30m terms that's roughly 1 prior bar.
    # We'll just track: "did B fire in the same or prev 30m bar as C?"
    b_fired_prev_bar = False
    b_fired_curr_bar = False

    print(
        f"\nTrade #{idx}: {direction.upper()} @ {trade['entry_price']:.2f} → "
        f"exit @ {trade['exit_price']:.2f}  ({trade['pnl_pct']:+.2f}%)"
        f"  reason={trade['exit_reason']}"
    )
    print(f"  Entry: {entry_t}  Exit: {exit_t}  ({len(sub)} 30m bars)")
    print(f"  {'time':>17} {'price':>9} {'hist':>8} {'peak':>8} "
          f"{'B':>2} {'x0':>3} {'x50':>3} {'x75':>3} {'cross':>5} {'BC_x0':>6} {'BC_x50':>7} {'BC_x75':>7} {'BC_cr':>6}")
    print("  " + "-" * 130)

    hist_window: list[float] = []
    for ts, row in sub.iterrows():
        hv = row["hist"]
        if pd.isna(hv):
            continue
        # Update running peak
        if peak is None:
            peak = float(hv)
        elif direction == "long" and hv > peak:
            peak = float(hv)
        elif direction == "short" and hv < peak:
            peak = float(hv)

        hist_window = [float(hv)] + hist_window[:2]
        st = _filter_status(direction, hist_window, peak)

        b_fired_prev_bar = b_fired_curr_bar
        b_fired_curr_bar = _b_signal(row["adx_pct_change"], ind["adx"].shift(B_PARAMS["adx_lookback"]).loc[ts])

        # Combined-BC AND gate (approximation): C fires AND B fired this or prev bar
        b_in_window = b_fired_curr_bar or b_fired_prev_bar
        bc_x0 = st["x0"] and b_in_window
        bc_x50 = st["x50"] and b_in_window
        bc_x75 = st["x75"] and b_in_window
        bc_cr = st["cross"] and b_in_window

        for k in ("x0", "x50", "x75", "cross"):
            if st[k]:
                fire_record[k].append(ts)
        if b_fired_curr_bar:
            fire_record["B"].append(ts)
        for tag, fired in [
            ("BC_x0", bc_x0), ("BC_x50", bc_x50), ("BC_x75", bc_x75), ("BC_cr", bc_cr),
        ]:
            pass  # printed inline

        peak_str = f"{peak:>+8.4f}" if peak is not None else "       –"
        print(
            f"  {ts.strftime('%Y-%m-%d %H:%M'):>17} {row['close']:>9.2f} "
            f"{hv:>+8.4f} {peak_str} "
            f"{'Y' if b_fired_curr_bar else '·':>2} "
            f"{'Y' if st['x0'] else '·':>3} "
            f"{'Y' if st['x50'] else '·':>3} "
            f"{'Y' if st['x75'] else '·':>3} "
            f"{'Y' if st['cross'] else '·':>5} "
            f"{'Y' if bc_x0 else '·':>6} "
            f"{'Y' if bc_x50 else '·':>7} "
            f"{'Y' if bc_x75 else '·':>7} "
            f"{'Y' if bc_cr else '·':>6}"
        )

    # Summary of first-fire per filter
    print(f"\n  First-fire summary:")
    for k in ("x0", "x50", "x75", "cross", "B"):
        ts_list = fire_record[k]
        if ts_list:
            t = ts_list[0]
            # find price at that bar
            p = ind.loc[t, "close"] if t in ind.index else None
            p_str = f" @ {p:.2f}" if p is not None else ""
            print(f"    {k:<6}: {t}{p_str}  (total fires: {len(ts_list)})")
        else:
            print(f"    {k:<6}: never fired")


def main() -> None:
    print("Loading + computing indicators...")
    ind = _build_indicators()
    print(f"  {len(ind)} 30m bars from {ind.index[0]} to {ind.index[-1]}")

    print("\nRunning backtest to get trade boundaries...")
    tl = _run_backtest_for_trades()
    trades = _pair_trades(tl)
    print(f"  {len(trades)} closed trades")

    # Pick 5 representative: 2 biggest winners, 2 biggest losers, 1 mid
    sorted_by_pnl = sorted(trades, key=lambda t: t["pnl_pct"])
    if len(sorted_by_pnl) < 5:
        picks = sorted_by_pnl
    else:
        picks = [
            sorted_by_pnl[-1],   # biggest winner
            sorted_by_pnl[-2],   # 2nd biggest winner
            sorted_by_pnl[len(sorted_by_pnl) // 2],  # middle
            sorted_by_pnl[1],    # 2nd worst
            sorted_by_pnl[0],    # worst
        ]
    print(f"\nPicked 5 trades for analysis:")
    for i, t in enumerate(picks, 1):
        print(f"  #{i}  {t['direction']:>5}  pnl={t['pnl_pct']:+7.2f}%  "
              f"entry={t['entry_time']:%Y-%m-%d %H:%M}  exit={t['exit_time']:%Y-%m-%d %H:%M}  "
              f"reason={t['exit_reason']}")

    for i, t in enumerate(picks, 1):
        _analyze_trade(t, ind, i)


if __name__ == "__main__":
    main()
