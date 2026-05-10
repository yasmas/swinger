#!/usr/bin/env python3
"""Idea #6 — independent indicators window scan.

For each rejection event, scan a window of 30-min bars around the rejection
for three indicators that DON'T share information with ST/ADX/MACD:

  1. HH/LL structural break  — long-rejection (flip→short) fires if current
     low < min(low[t-K..t-1]); short-rejection fires if current high
     > max(high[t-K..t-1]). Pure price action.
  2. DMI dominance / cross   — long-rejection fires if -DI > +DI (or just
     crossed above) in flip direction; short-rejection symmetric.
  3. Anchored-VWAP from entry — long-rejection fires if close < VWAP_anchored
     (price below volume-weighted avg since entry); short symmetric.

Re-runs the ship config to also capture entry_ts (anchored VWAP needs it).
Aggregates per-fate firing rates and checks whether any combination
discriminates rejection-was-right (fast_exit) from rejection-was-wrong
(st_flip_ratio_safety).
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

COST_PER_TRADE_PCT = 0.05
RESAMPLE = "30min"
HHLL_LOOKBACK = 10        # K bars (=5h)
DMI_PERIOD = 14
N_BEFORE = 3
N_AFTER = 6


def _run_one(args: tuple) -> tuple[str, pd.DataFrame]:
    wk, params = args
    win = WINDOWS[wk]
    slice_file = str(SLICE_DIR / f"{wk}.csv")
    tmp = tempfile.mkdtemp(prefix="idea6w_")
    try:
        cfg = Config({
            "backtest": {
                "name": f"idea6w_{wk}", "version": "idea6w-indep-scan",
                "initial_cash": 100000.0,
                "start_date": win["start"], "end_date": win["end"],
            },
            "data_source": {
                "type": "csv_file", "parser": "coinbase_intx_kline",
                "params": {"file_path": slice_file, "symbol": "ETH-PERP-INTX"},
            },
            "strategies": [{"type": "lazy_swing", "params": {**BASE_PARAMS, **params}}],
        })
        result = Controller(cfg, output_dir=tmp).run()[0]
        tl = TradeLogReader().read(result.trade_log_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return wk, tl


def _extract_rejections(wk: str, tl: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    cur_direction: str | None = None
    cur_entry_price: float | None = None
    cur_entry_ts: pd.Timestamp | None = None
    records = list(tl.itertuples(index=True))
    for i, rec in enumerate(records):
        action = rec.action
        details = rec.details if isinstance(rec.details, dict) else {}
        ts = rec.date
        price = float(rec.price)
        if action == "BUY":
            cur_direction = "long"; cur_entry_price = price; cur_entry_ts = ts
            continue
        if action == "SHORT":
            cur_direction = "short"; cur_entry_price = price; cur_entry_ts = ts
            continue
        if action in ("SELL", "COVER"):
            cur_direction = None; cur_entry_price = None; cur_entry_ts = None
            continue
        if details.get("reason") != "st_flip_ratio_rejected_hold":
            continue
        if cur_direction is None:
            continue

        # actual exit
        actual_exit_price = None
        actual_exit_reason = None
        for j in range(i + 1, len(records)):
            r2 = records[j]
            if r2.action in ("SELL", "COVER"):
                actual_exit_price = float(r2.price)
                d2 = r2.details if isinstance(r2.details, dict) else {}
                actual_exit_reason = d2.get("exit_reason", "unknown")
                break
        # next same-direction entry → for hypothetical flipped exit
        entry_action = "BUY" if cur_direction == "long" else "SHORT"
        next_same_dir_price = None
        for j in range(i + 1, len(records)):
            r2 = records[j]
            if r2.action == entry_action:
                next_same_dir_price = float(r2.price)
                break
        if cur_direction == "long":
            held_pnl = ((actual_exit_price / price - 1) * 100 - COST_PER_TRADE_PCT) if actual_exit_price else None
            flip_pnl = ((price / next_same_dir_price - 1) * 100 - COST_PER_TRADE_PCT) if next_same_dir_price else None
        else:
            held_pnl = ((price / actual_exit_price - 1) * 100 - COST_PER_TRADE_PCT) if actual_exit_price else None
            flip_pnl = ((next_same_dir_price / price - 1) * 100 - COST_PER_TRADE_PCT) if next_same_dir_price else None
        rows.append({
            "window": wk,
            "rejection_ts": ts,
            "entry_ts": cur_entry_ts,
            "direction": cur_direction,
            "entry_price": cur_entry_price,
            "rejection_price": price,
            "actual_exit_reason": actual_exit_reason,
            "held_pnl_pct": held_pnl,
            "flipped_pnl_pct": flip_pnl,
        })
    return pd.DataFrame(rows)


def _resample_30m(slice_csv: Path, start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(slice_csv)
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    df = df.set_index("ts")
    rs = df.resample(RESAMPLE).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    rs = rs[(rs.index >= pd.Timestamp(start)) & (rs.index < pd.Timestamp(end))]
    return rs


def _compute_indep_signals(rs: pd.DataFrame) -> pd.DataFrame:
    """Compute HH/LL break, DMI components, plus volume*price for VWAP."""
    df = rs.copy()
    # HH/LL using prior K bars (exclude current)
    prior_high = df["high"].shift(1).rolling(HHLL_LOOKBACK, min_periods=HHLL_LOOKBACK).max()
    prior_low = df["low"].shift(1).rolling(HHLL_LOOKBACK, min_periods=HHLL_LOOKBACK).min()
    # Long-rejection (flip→short) fires if low < prior min low (lower low / breakdown)
    df["ll_break"] = df["low"] < prior_low
    # Short-rejection (flip→long) fires if high > prior max high (higher high / breakout)
    df["hh_break"] = df["high"] > prior_high

    # DMI from Wilder
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    plus_dm = (df["high"] - prev_high).where((df["high"] - prev_high) > (prev_low - df["low"]), 0.0).clip(lower=0)
    minus_dm = (prev_low - df["low"]).where((prev_low - df["low"]) > (df["high"] - prev_high), 0.0).clip(lower=0)
    atr = tr.ewm(alpha=1 / DMI_PERIOD, min_periods=DMI_PERIOD, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / DMI_PERIOD, min_periods=DMI_PERIOD, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / DMI_PERIOD, min_periods=DMI_PERIOD, adjust=False).mean() / atr)
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di
    # DMI dominance
    df["dmi_minus_dom"] = minus_di > plus_di  # bearish dominance (long-rej fires)
    df["dmi_plus_dom"] = plus_di > minus_di   # bullish dominance (short-rej fires)
    # DMI cross
    prev_pdi = plus_di.shift(1)
    prev_mdi = minus_di.shift(1)
    df["dmi_cross_to_minus"] = (prev_pdi >= prev_mdi) & (plus_di < minus_di)
    df["dmi_cross_to_plus"] = (prev_pdi <= prev_mdi) & (plus_di > minus_di)

    # Track bar's typical price for VWAP. VWAP itself is anchored per-trade
    # so we just keep volume + tp here.
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3.0
    return df


def _scan_event(rs_signals: pd.DataFrame, rejection_ts: pd.Timestamp,
                entry_ts: pd.Timestamp, direction: str) -> dict:
    """Compute indicator window state for a single rejection event."""
    bar_start = rejection_ts - pd.Timedelta(RESAMPLE)
    idx_arr = rs_signals.index.get_indexer([bar_start], method="nearest")
    if len(idx_arr) == 0 or idx_arr[0] < 0:
        return {}
    center_i = int(idx_arr[0])
    n = len(rs_signals)

    # Anchored VWAP from entry. Find entry bar.
    entry_bar_start = entry_ts - pd.Timedelta(RESAMPLE) if pd.notna(entry_ts) else None
    if entry_bar_start is not None:
        entry_idx = rs_signals.index.get_indexer([entry_bar_start], method="nearest")
        entry_i = int(entry_idx[0]) if len(entry_idx) > 0 else center_i
        entry_i = max(0, entry_i)
    else:
        entry_i = center_i

    # Pre-extract a slice covering [entry_i, max(center_i + N_AFTER, entry_i)]
    end_i = min(n - 1, max(center_i + N_AFTER, entry_i))
    sub = rs_signals.iloc[entry_i: end_i + 1]
    cum_vp = (sub["tp"] * sub["volume"]).cumsum()
    cum_v = sub["volume"].cumsum().replace(0, np.nan)
    vwap_anchored = cum_vp / cum_v

    # Indicator booleans per offset
    if direction == "long":
        hhll_col = "ll_break"
        dmi_dom_col = "dmi_minus_dom"
        dmi_cross_col = "dmi_cross_to_minus"
        # vwap_signal: close < VWAP (price below avg cost basis since entry)
    else:
        hhll_col = "hh_break"
        dmi_dom_col = "dmi_plus_dom"
        dmi_cross_col = "dmi_cross_to_plus"

    offsets = list(range(-N_BEFORE, N_AFTER + 1))
    out: dict = {}
    hhll_per: dict[int, bool] = {}
    dmi_dom_per: dict[int, bool] = {}
    dmi_cross_per: dict[int, bool] = {}
    vwap_per: dict[int, bool] = {}
    for off in offsets:
        i = center_i + off
        if 0 <= i < n:
            row = rs_signals.iloc[i]
            hhll_per[off] = bool(row[hhll_col]) if not pd.isna(row[hhll_col]) else False
            dmi_dom_per[off] = bool(row[dmi_dom_col]) if not pd.isna(row[dmi_dom_col]) else False
            dmi_cross_per[off] = bool(row[dmi_cross_col]) if not pd.isna(row[dmi_cross_col]) else False
            # VWAP at this offset: if i ≥ entry_i and within sub
            if i >= entry_i and i - entry_i < len(vwap_anchored):
                vw = vwap_anchored.iloc[i - entry_i]
                cl = float(row["close"])
                if pd.notna(vw):
                    vwap_per[off] = (cl < vw) if direction == "long" else (cl > vw)
                else:
                    vwap_per[off] = False
            else:
                vwap_per[off] = False
        else:
            hhll_per[off] = False
            dmi_dom_per[off] = False
            dmi_cross_per[off] = False
            vwap_per[off] = False

    out["hhll_at_0"] = hhll_per.get(0, False)
    out["hhll_any"] = any(hhll_per.values())
    out["dmi_dom_at_0"] = dmi_dom_per.get(0, False)
    out["dmi_dom_any"] = any(dmi_dom_per.values())
    out["dmi_cross_at_0"] = dmi_cross_per.get(0, False)
    out["dmi_cross_any"] = any(dmi_cross_per.values())
    out["vwap_at_0"] = vwap_per.get(0, False)
    out["vwap_any"] = any(vwap_per.values())
    return out


def main() -> None:
    print(f"Running ship config across {len(WINDOWS)} quarters\n")
    t0 = time.time()
    tasks = [(wk, SHIP_PARAMS) for wk in WINDOWS]
    with mp.Pool(min(8, len(tasks))) as pool:
        run_results = pool.map(_run_one, tasks)
    print(f"Backtests done in {time.time()-t0:.0f}s. Extracting rejections + signals...\n")

    events_dfs = [_extract_rejections(wk, tl) for wk, tl in run_results]
    events = pd.concat([d for d in events_dfs if not d.empty], ignore_index=True)
    print(f"Total rejections: {len(events)}")

    # Compute signals per window
    sig_cache = {}
    for wk, win in WINDOWS.items():
        rs = _resample_30m(SLICE_DIR / f"{wk}.csv", win["start"], win["end"])
        sig_cache[wk] = _compute_indep_signals(rs)

    extra = []
    for _, row in events.iterrows():
        rs = sig_cache.get(row["window"])
        if rs is None:
            extra.append({})
            continue
        scan = _scan_event(rs, pd.Timestamp(row["rejection_ts"]),
                           pd.Timestamp(row["entry_ts"]) if pd.notna(row["entry_ts"]) else pd.NaT,
                           row["direction"])
        extra.append(scan)
    out = pd.concat([events.reset_index(drop=True), pd.DataFrame(extra)], axis=1)

    out_dir = REPO / "data" / "backtests" / "eth" / "idea6_indep_window_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_dir / "events_with_indep_signals.csv", index=False)

    # ---- Per-fate firing rates ----
    print("\n" + "=" * 145)
    print(f"Independent-indicator firing rates by fate  (window = [-{N_BEFORE}, +{N_AFTER}] 30m bars)")
    print("=" * 145)
    print(f"{'fate':<22} {'N':>4}  "
          f"{'HHLL@0':>7} {'HHLLany':>8}  "
          f"{'DMIdom@0':>9} {'DMIdomA':>8}  "
          f"{'DMIxc@0':>8} {'DMIxcA':>8}  "
          f"{'VWAP@0':>7} {'VWAPany':>8}  "
          f"{'meanHeld%':>10} {'meanFlip%':>10}")
    print("-" * 145)
    by_fate = out.groupby("actual_exit_reason")
    for fate, sub in sorted(by_fate, key=lambda kv: -len(kv[1])):
        print(f"{fate:<22} {len(sub):>4}  "
              f"{sub['hhll_at_0'].mean()*100:>+6.1f}% {sub['hhll_any'].mean()*100:>+7.1f}%  "
              f"{sub['dmi_dom_at_0'].mean()*100:>+8.1f}% {sub['dmi_dom_any'].mean()*100:>+7.1f}%  "
              f"{sub['dmi_cross_at_0'].mean()*100:>+7.1f}% {sub['dmi_cross_any'].mean()*100:>+7.1f}%  "
              f"{sub['vwap_at_0'].mean()*100:>+6.1f}% {sub['vwap_any'].mean()*100:>+7.1f}%  "
              f"{sub['held_pnl_pct'].mean():>+10.3f} {sub['flipped_pnl_pct'].mean():>+10.3f}")

    # ---- Discrimination tests: split by each indicator and look at outcomes ----
    print("\n" + "=" * 130)
    print("Outcome split by each indicator firing in window")
    print("=" * 130)
    print(f"{'group':<32} {'N':>4} {'safety_n':>9} {'fast_n':>7} "
          f"{'meanHeld%':>10} {'meanFlip%':>10} {'edge_flip-held%':>16}")
    print("-" * 130)

    def _print_split(label: str, sub: pd.DataFrame) -> None:
        n = len(sub)
        if n == 0:
            print(f"{label:<32} {n:>4}  (empty)")
            return
        safety = (sub["actual_exit_reason"] == "st_flip_ratio_safety").sum()
        fast = (sub["actual_exit_reason"] == "fast_exit").sum()
        held = sub["held_pnl_pct"].mean()
        flip = sub["flipped_pnl_pct"].mean()
        print(f"{label:<32} {n:>4} {safety:>9d} {fast:>7d} "
              f"{held:>+10.3f} {flip:>+10.3f} {flip-held:>+15.3f}%")

    _print_split("HH/LL break fired in window", out[out["hhll_any"]])
    _print_split("HH/LL break silent",          out[~out["hhll_any"]])
    _print_split("DMI dominance in window",     out[out["dmi_dom_any"]])
    _print_split("DMI dominance silent",        out[~out["dmi_dom_any"]])
    _print_split("DMI cross in window",         out[out["dmi_cross_any"]])
    _print_split("DMI cross silent",            out[~out["dmi_cross_any"]])
    _print_split("VWAP-against fired in window",out[out["vwap_any"]])
    _print_split("VWAP-against silent",         out[~out["vwap_any"]])

    # 2-of-3 confirmations (HH/LL + DMI cross + VWAP-against) — looks like the
    # canonical "real reversal" combined signal.
    confirms = (
        out["hhll_any"].astype(int)
        + out["dmi_cross_any"].astype(int)
        + out["vwap_any"].astype(int)
    )
    _print_split("≥2 of (HHLL, DMIxc, VWAP)",   out[confirms >= 2])
    _print_split("All 3 of (HHLL, DMIxc, VWAP)",out[confirms == 3])
    _print_split("None of (HHLL, DMIxc, VWAP)", out[confirms == 0])

    # By-fate cross-tab on bc_any-equivalent: VWAP@0 (most-anchor-like)
    print(f"\nResults: {out_dir}")
    print(f"Total time: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
