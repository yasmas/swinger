#!/usr/bin/env python3
"""Idea #4 research — vol-expansion-in-favor characterisation.

For every in-position bar across 8 quarters, identify "big-vol-in-favor"
events (5m bar with True Range ≥ ATR-pct-95 of rolling 24h, bar moved in
position-favorable direction, current gain ≥ min_gain). Then characterise:

  1. Forward return in position-favor direction at K = 1, 3, 6, 12, 48 bars
     (5/15/30/60 min, 4h)
  2. Reverse-within-1h fraction (forward gain at K=12 ≤ 0)
  3. Continuation distribution: of continuers (forward gain at K=12 > 0),
     how much further by K=48?
  4. EV comparison vs holding: gain-at-event vs gain-at-actual-exit
"""
from __future__ import annotations

import multiprocessing as mp
import shutil
import sys
import tempfile
import time
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

WINDOWS = {
    "2024_Q1": {"start": "2024-01-01", "end": "2024-04-01"},
    "2024_Q2": {"start": "2024-04-01", "end": "2024-07-01"},
    "2024_Q3": {"start": "2024-07-01", "end": "2024-10-01"},
    "2024_Q4": {"start": "2024-10-01", "end": "2025-01-01"},
    "2025_Q1": {"start": "2025-01-01", "end": "2025-04-01"},
    "2025_Q2": {"start": "2025-04-01", "end": "2025-07-01"},
    "2026_Q1": {"start": "2026-01-01", "end": "2026-04-01"},
    "2026_Q2": {"start": "2026-04-01", "end": "2026-05-08"},
}
SLICE_DIR = REPO / "data" / "backtests" / "eth" / "profit_exit_grid_slices"

# Same ship config as Phase 0 — gives us realistic in-position bars.
BASE_PARAMS: dict = {
    "resample_interval": "30min",
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
}

SHIP_PARAMS: dict = {
    "regime_trail_mode": "combined_bc",
    "regime_exhaustion_adx_lookback": 12,
    "regime_exhaustion_adx_drop_pct": 3.5,
    "regime_exhaustion_prev_adx_min": 20.0,
    "profit_exit_macd_fast": 8,
    "profit_exit_macd_slow": 21,
    "profit_exit_macd_signal_period": 9,
    "profit_exit_macd_histogram_bars": 2,
    "profit_exit_macd_condition": "cross",
    "combined_bc_window_bars": 6,
    "trail_stop_min_gain_pct": 2.0,
    "trail_stop_exit_on_signal": False,
    "trail_stop_giveback_window_bars": 2,
}

# Vol-event detection params
ATR_WINDOW_BARS = 288  # 24h on 5m bars
ATR_PCTILE = 0.95
MIN_GAIN_PCT = 1.5  # only count events when current trade gain >= this
FORWARD_KS = [1, 3, 6, 12, 48]  # 5, 15, 30, 60, 240 min ahead

# Buckets for current gain at event time
GAIN_BUCKETS = [
    (1.5, 3.0, "[1.5%,3%)"),
    (3.0, 5.0, "[3%,5%)"),
    (5.0, 8.0, "[5%,8%)"),
    (8.0, float("inf"), "[8%+)"),
]


def _run_ship_one(args: tuple) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    """Run ship config, return (window_key, trade_log, price_feed)."""
    wk, params = args
    win = WINDOWS[wk]
    slice_file = SLICE_DIR / f"{wk}.csv"
    tmp = tempfile.mkdtemp(prefix="idea4_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"idea4_{wk}", "version": "idea4-volexpansion-research",
                "initial_cash": 100000.0,
                "start_date": win["start"], "end_date": win["end"],
            },
            "data_source": {
                "type": "csv_file", "parser": "coinbase_intx_kline",
                "params": {"file_path": str(slice_file), "symbol": "ETH-PERP-INTX"},
            },
            "strategies": [{"type": "lazy_swing", "params": {**BASE_PARAMS, **params}}],
        })
        result = Controller(cfg, output_dir=tmp).run()[0]
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Load full price feed and slice to backtest window.
    pf = pd.read_csv(slice_file)
    pf["ts"] = pd.to_datetime(pf["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    start = pd.Timestamp(win["start"])
    end = pd.Timestamp(win["end"])
    pf = pf[(pf["ts"] >= start) & (pf["ts"] < end)].reset_index(drop=True)
    return wk, tl, pf


def _compute_vol_event_features(pf: pd.DataFrame) -> pd.DataFrame:
    """Add tr, rolling_atr, atr_p95, vol_event_flag, signed_close_change."""
    df = pf.copy()
    prev_close = df["close"].shift(1)
    tr_components = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - prev_close).abs(),
         (df["low"] - prev_close).abs()],
        axis=1,
    )
    df["tr"] = tr_components.max(axis=1)
    df["rolling_atr"] = df["tr"].rolling(ATR_WINDOW_BARS, min_periods=ATR_WINDOW_BARS).mean()
    df["atr_p95"] = df["tr"].rolling(ATR_WINDOW_BARS, min_periods=ATR_WINDOW_BARS).quantile(ATR_PCTILE)
    df["bar_change_pct"] = (df["close"] - df["open"]) / df["open"] * 100.0
    df["bar_range_pct"] = (df["high"] - df["low"]) / df["close"] * 100.0
    return df


def _build_trade_intervals(tl: pd.DataFrame) -> list[dict]:
    """Pair entries (BUY/SHORT) with exits (SELL/COVER) in time order."""
    trades: list[dict] = []
    open_trade: dict | None = None
    for _, row in tl.iterrows():
        action = row["action"]
        ts = row["date"]
        d = row.get("details") or {}
        if action == "BUY":
            open_trade = {"direction": "long", "entry_ts": ts, "entry_price": row["price"]}
        elif action == "SHORT":
            open_trade = {"direction": "short", "entry_ts": ts, "entry_price": row["price"]}
        elif action in ("SELL", "COVER") and open_trade is not None:
            open_trade["exit_ts"] = ts
            open_trade["exit_price"] = row["price"]
            open_trade["exit_reason"] = d.get("exit_reason", "unknown")
            open_trade["exit_pnl_pct"] = d.get("pnl_pct")
            trades.append(open_trade)
            open_trade = None
    return trades


def _scan_window(args: tuple) -> pd.DataFrame:
    wk, tl, pf = args
    pf = _compute_vol_event_features(pf)
    trades = _build_trade_intervals(tl)
    rows: list[dict] = []
    for trade in trades:
        direction = trade["direction"]
        entry_ts = trade["entry_ts"]
        exit_ts = trade["exit_ts"]
        entry_price = float(trade["entry_price"])
        exit_pnl = float(trade["exit_pnl_pct"]) if trade["exit_pnl_pct"] is not None else float("nan")
        # Slice price feed to the trade's lifetime.
        mask = (pf["ts"] >= entry_ts) & (pf["ts"] <= exit_ts)
        sub = pf.loc[mask].reset_index(drop=True)
        if len(sub) < 2:
            continue
        n = len(sub)
        for i in range(n):
            row = sub.iloc[i]
            atr_p95 = row["atr_p95"]
            tr = row["tr"]
            if pd.isna(atr_p95) or tr < atr_p95:
                continue
            # Bar must move IN our favor (long: close>open; short: close<open).
            bar_change_pct = float(row["bar_change_pct"])
            in_favor = bar_change_pct > 0 if direction == "long" else bar_change_pct < 0
            if not in_favor:
                continue
            # Current gain (peak-favorable price vs entry).
            close = float(row["close"])
            if direction == "long":
                cur_gain_pct = (close / entry_price - 1) * 100
            else:
                cur_gain_pct = (entry_price / close - 1) * 100
            if cur_gain_pct < MIN_GAIN_PCT:
                continue
            # Forward returns at K bars (in favor direction). Stop at exit_ts.
            forwards: dict[int, float | None] = {}
            for k in FORWARD_KS:
                if i + k < n:
                    fclose = float(sub.iloc[i + k]["close"])
                    if direction == "long":
                        fwd = (fclose / close - 1) * 100
                    else:
                        fwd = (close / fclose - 1) * 100
                    forwards[k] = fwd
                else:
                    forwards[k] = None
            # Remaining peak (max favorable move) and remaining exit
            rest = sub.iloc[i + 1:] if i + 1 < n else None
            if rest is not None and len(rest):
                if direction == "long":
                    peak_after = float(rest["close"].max())
                    rem_peak_pct = (peak_after / close - 1) * 100
                else:
                    peak_after = float(rest["close"].min())
                    rem_peak_pct = (close / peak_after - 1) * 100
                final_close = float(sub.iloc[-1]["close"])
                if direction == "long":
                    rem_exit_pct = (final_close / close - 1) * 100
                else:
                    rem_exit_pct = (close / final_close - 1) * 100
            else:
                rem_peak_pct = float("nan")
                rem_exit_pct = float("nan")
            rows.append({
                "window": wk,
                "ts": row["ts"],
                "direction": direction,
                "entry_ts": entry_ts,
                "exit_ts": exit_ts,
                "exit_reason": trade["exit_reason"],
                "exit_pnl_pct": exit_pnl,
                "cur_gain_pct": cur_gain_pct,
                "bar_change_pct": bar_change_pct,
                "tr": float(tr),
                "atr_p95": float(atr_p95),
                "tr_over_atr_p95": float(tr / atr_p95) if atr_p95 > 0 else float("nan"),
                "rem_peak_pct": rem_peak_pct,
                "rem_exit_pct": rem_exit_pct,
                **{f"fwd_{k}": forwards[k] for k in FORWARD_KS},
            })
    return pd.DataFrame(rows)


def _bucket_label(gain: float) -> str | None:
    for lo, hi, label in GAIN_BUCKETS:
        if lo <= gain < hi:
            return label
    return None


def main() -> None:
    print(f"Running ship config across {len(WINDOWS)} quarters\n")
    t0 = time.time()
    tasks_run = [(wk, SHIP_PARAMS) for wk in WINDOWS]
    with mp.Pool(min(8, len(tasks_run))) as pool:
        run_results = pool.map(_run_ship_one, tasks_run)

    print("Scanning trade intervals for vol-expansion-in-favor events...\n")
    scan_results = [_scan_window(r) for r in run_results]
    events = pd.concat([df for df in scan_results if not df.empty], ignore_index=True)

    out_dir = REPO / "data" / "backtests" / "eth" / "idea4_volexpansion_research"
    out_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(out_dir / "events_all.csv", index=False)

    print(f"Total vol-events across 8 quarters: {len(events)}")
    if events.empty:
        print("No events found; exiting.")
        return

    # Per-quarter event count.
    print("\n" + "=" * 90)
    print("Events per quarter")
    print("=" * 90)
    counts = events.groupby("window").size()
    for wk in WINDOWS:
        print(f"  {wk}: {int(counts.get(wk, 0))}")

    # Pooled forward-return distribution.
    print("\n" + "=" * 110)
    print(f"Forward returns from event bar (in favor direction) — N = {len(events)}")
    print("=" * 110)
    print(f"{'K bars':>8} {'≈ time':>9} {'mean%':>8} {'median%':>9} {'p10':>8} {'p25':>8} "
          f"{'p75':>8} {'p90':>8} {'pos%':>8} {'reverse%':>10}")
    print("-" * 110)
    times = {1: "5m", 3: "15m", 6: "30m", 12: "60m", 48: "4h"}
    for k in FORWARD_KS:
        col = f"fwd_{k}"
        s = events[col].dropna()
        if len(s) == 0:
            continue
        # "reverse%" = fraction of events whose forward gain went *negative* (i.e., against position move).
        reverse = (s < 0).mean() * 100
        positive = (s > 0).mean() * 100
        print(f"{k:>8} {times[k]:>9} {s.mean():>+8.3f} {s.median():>+9.3f} "
              f"{s.quantile(0.10):>+8.3f} {s.quantile(0.25):>+8.3f} "
              f"{s.quantile(0.75):>+8.3f} {s.quantile(0.90):>+8.3f} "
              f"{positive:>+7.1f}% {reverse:>+9.1f}%")

    # Per-bucket: continuation / reversal at 1h, EV comparison.
    print("\n" + "=" * 130)
    print("Bucketed by current gain at event time")
    print("=" * 130)
    print(f"{'Bucket':<12} {'N':>5} {'rev1h%':>8} {'cont1h_4h':>11} "
          f"{'remPk%':>9} {'remExit%':>10} {'EV(exit-now)':>14} {'EV(hold)':>10} {'edge':>8}")
    print("-" * 130)
    events["bucket"] = events["cur_gain_pct"].apply(_bucket_label)
    for lo, hi, label in GAIN_BUCKETS:
        sub = events[events["bucket"] == label]
        if sub.empty:
            print(f"{label:<12} no events")
            continue
        s_1h = sub["fwd_12"].dropna()
        s_4h = sub["fwd_48"].dropna()
        # Reversal at 1h: how often forward 1h is negative (against position)
        reverse_1h = (s_1h < 0).mean() * 100 if len(s_1h) else float("nan")
        # Of continuers (positive at 1h), median additional gain at 4h
        if len(s_4h):
            mask_continue = sub.loc[s_4h.index, "fwd_12"] > 0
            cont_4h_med = s_4h[mask_continue].median() if mask_continue.any() else float("nan")
        else:
            cont_4h_med = float("nan")
        rem_peak_med = sub["rem_peak_pct"].median()
        rem_exit_med = sub["rem_exit_pct"].median()
        # EV-now: exit at event close → captures cur_gain (already locked) + 0 forward
        # EV-hold: keep position until trade's actual exit → adds rem_exit_pct
        # The relative "edge" is: if you exit at event, you give up rem_exit_pct
        # of additional move (but free up capital for re-entry).
        ev_exit_now = 0.0  # baseline (no further move)
        ev_hold = sub["rem_exit_pct"].mean()
        edge = ev_exit_now - ev_hold
        print(f"{label:<12} {len(sub):>5d} {reverse_1h:>+7.1f}% {cont_4h_med:>+10.3f}% "
              f"{rem_peak_med:>+9.3f} {rem_exit_med:>+10.3f} "
              f"{ev_exit_now:>+13.3f}% {ev_hold:>+9.3f}% {edge:>+7.3f}")
    print()
    print("Legend:")
    print("  rev1h%      = % of events where forward 1h move went against position")
    print("  cont1h_4h%  = of events that didn't reverse at 1h, median additional 4h move")
    print("  remPk%/remExit% = median *remaining* peak / actual exit gain after the event")
    print("  EV(exit-now) - EV(hold) > 0 means exiting at event would have captured more on average")

    # Total-EV decision summary
    print("\n" + "=" * 90)
    print("Decision-gate summary")
    print("=" * 90)
    print(f"Total events: {len(events)}")
    print(f"Mean rem_exit_pct (pnl from event-bar to trade exit): "
          f"{events['rem_exit_pct'].mean():+.3f}%")
    print(f"Mean rem_peak_pct (best-case 'leave on table' if perfect timing): "
          f"{events['rem_peak_pct'].mean():+.3f}%")
    print()
    print("Read: if rem_exit_pct is consistently *negative*, holding loses money on average")
    print("after the vol-event — exit-at-event would have been the right call.")
    print("If positive, holding is the right call.")
    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
