#!/usr/bin/env python3
"""Analyze 2026 LazySwing trades that gave back meaningful open profit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from strategies.lazy_swing import LazySwingStrategy  # noqa: E402


DATA_FILE = REPO / "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv"
BASELINE_LOG = (
    REPO
    / "reports/lazyswing-strict-exhaustion-trail-comparison/2026/baseline"
    / "LazySwing_strict_exhaustion_2026_baseline_lazy_swing_strict-exhaustion-trail.csv"
)
OUT_DIR = REPO / "reports/lazyswing-2026-giveback-diagnostic"
START = pd.Timestamp("2026-03-01")
MIN_MFE_PCT = 1.5
MAX_EXIT_PNL_PCT = 0.5
COST_PCT = 0.05


PARAMS = {
    "resample_interval": "30min",
    "supertrend_atr_period": 25,
    "supertrend_multiplier": 1.75,
    "adaptive_st_vol_period": 24,
    "adaptive_st_vol_long_period": 336,
    "adaptive_st_enter_ratio_threshold": 1.0,
    "adaptive_st_exit_ratio_threshold": 0.85,
    "adaptive_st_min_high_bars": 48,
    "flip_vol_ratio_enabled": True,
    "flip_vol_ratio_short_period": 4,
    "flip_vol_ratio_long_period": 336,
    "flip_vol_ratio_regime_mode": "squared",
    "flip_vol_ratio_regime_low_min": 0.7,
    "flip_vol_ratio_regime_high_min": 1.0,
    "flip_vol_ratio_regime_low_stop_pct": 1.0,
    "flip_vol_ratio_regime_high_stop_pct": 2.5,
    "flip_vol_ratio_regime_power": 1.5,
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": COST_PCT,
    "fast_exit_enabled": True,
    "fast_exit_cooldown_bars": 4,
    "fast_exit_rvol_short_period": 24,
    "fast_exit_rvol_long_period": 2016,
    "fast_exit_rvol_low_min": 1.1,
    "fast_exit_rvol_high_min": 1.3,
    "fast_exit_reentry_confirm": True,
    "flat_realign_hourly_closes": 0,
    "regime_trail_enabled": True,
    "regime_trail_mode": "strict_exhaustion",
    "regime_momentum_adx_period": 14,
    "regime_momentum_adx_min": 40.0,
    "regime_momentum_er_period": 24,
    "regime_momentum_er_min": 0.40,
    "regime_momentum_adx_delta_bars": 2,
    "regime_momentum_adx_delta_min": 1.0,
    "regime_momentum_vol_period": 24,
    "regime_momentum_vol_long_period": 336,
    "regime_momentum_vol_ratio_max": 1.0,
    "regime_exhaustion_stretch_lookback": 3,
    "regime_exhaustion_kc_z_min": 2.0,
    "regime_exhaustion_bb_z_min": 3.0,
    "regime_exhaustion_adx_lookback": 2,
    "regime_exhaustion_prev_adx_min": 30.0,
    "regime_exhaustion_adx_drop_pct": 2.5,
    "trail_stop_pct": 0.75,
    "trail_stop_atr_multiple": 0.75,
    "trail_stop_min_gain_pct": 1.5,
    "trail_stop_cooldown_bars": 0,
    "trail_stop_reentry_pct": 0.5,
    "trail_stop_exit_on_signal": True,
    "trail_stop_reentry_enabled": False,
}


def load_prices() -> pd.DataFrame:
    df = pd.read_csv(DATA_FILE)
    df["date"] = pd.to_datetime(df["open_time"].astype(float), unit="ms", utc=True)
    df["date"] = df["date"].dt.tz_localize(None)
    df = df.set_index("date").sort_index()
    df = df.loc[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def parse_details(raw: str) -> dict:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def load_trades() -> list[dict]:
    log = pd.read_csv(BASELINE_LOG, parse_dates=["date"])
    trades: list[dict] = []
    current: dict | None = None
    for _, row in log.iterrows():
        action = row["action"]
        details = parse_details(row.get("details", ""))
        if action in {"BUY", "SHORT"}:
            if current is None:
                current = {
                    "entry_time": row["date"],
                    "entry_action": action,
                    "direction": "long" if action == "BUY" else "short",
                    "entry_price": float(row["price"]),
                    "entry_reason": details.get("entry_reason") or details.get("reason"),
                }
            continue
        if action in {"SELL", "COVER"} and current is not None:
            current.update({
                "exit_time": row["date"],
                "exit_action": action,
                "exit_price": float(row["price"]),
                "exit_reason": details.get("exit_reason") or details.get("reason"),
                "exit_pnl_pct": details.get("pnl_pct"),
            })
            trades.append(current)
            current = None
    return trades


def trade_path_stats(trade: dict, prices: pd.DataFrame) -> dict:
    path = prices.loc[trade["entry_time"]: trade["exit_time"]].copy()
    entry = trade["entry_price"]
    exit_price = trade["exit_price"]
    if trade["direction"] == "long":
        favorable = path["high"] / entry - 1.0
        adverse = path["low"] / entry - 1.0
        close_pnl = exit_price / entry - 1.0
        running_mfe = favorable.cummax() * 100.0
        running_mae = adverse.cummin() * 100.0
    else:
        favorable = entry / path["low"] - 1.0
        adverse = entry / path["high"] - 1.0
        close_pnl = entry / exit_price - 1.0
        running_mfe = favorable.cummax() * 100.0
        running_mae = adverse.cummin() * 100.0
    return {
        "path": path,
        "mfe_pct": float(favorable.max() * 100.0),
        "mae_pct": float(adverse.min() * 100.0),
        "computed_exit_pnl_pct": float(close_pnl * 100.0 - COST_PCT),
        "running_mfe": running_mfe,
        "running_mae": running_mae,
    }


def regime_rows(strategy: LazySwingStrategy, path: pd.DataFrame, running_mfe: pd.Series) -> pd.DataFrame:
    rows = []
    for ts in path.index:
        hourly_idx = strategy._5m_to_hourly.get(ts)
        if hourly_idx is None:
            continue
        info = strategy._regime_trail_info(hourly_idx)
        if not info.get("ready"):
            continue
        rows.append({
            "date": ts,
            "hourly_idx": hourly_idx,
            "running_mfe_pct": float(running_mfe.loc[ts]),
            "base_mode": info.get("base_mode"),
            "strict_exhaustion": bool(info.get("strict_exhaustion")),
            "mean_revert": info.get("base_mode") == "mean_revert",
            "momentum_decay": info.get("base_mode") == "momentum_decay",
            "momentum": info.get("base_mode") == "momentum",
            "neutral": info.get("base_mode") == "neutral",
            "adx": info.get("adx"),
            "adx_pct_change": info.get("adx_pct_change"),
            "prev_exhaustion_adx": info.get("prev_exhaustion_adx"),
            "efficiency": info.get("efficiency"),
            "slow_vol_ratio": info.get("slow_vol_ratio"),
            "kc_abs_z": info.get("kc_abs_z"),
            "bb_abs_z": info.get("bb_abs_z"),
            "kc_abs_z_recent": info.get("kc_abs_z_recent"),
            "bb_abs_z_recent": info.get("bb_abs_z_recent"),
            "stretched": bool(info.get("stretched")),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["strict_stretch_ok"] = (out["kc_abs_z_recent"] >= 2.0) | (out["bb_abs_z_recent"] >= 3.0)
    out["strict_prev_adx_ok"] = out["prev_exhaustion_adx"] >= 30.0
    out["strict_adx_fade_ok"] = out["adx_pct_change"] <= -2.5
    out["strict_not_momentum_ok"] = ~out["momentum"]
    out["relaxed_stretch_1_5_2_5"] = (out["kc_abs_z_recent"] >= 1.5) | (out["bb_abs_z_recent"] >= 2.5)
    out["relaxed_prev_adx_20"] = out["prev_exhaustion_adx"] >= 20.0
    out["relaxed_adx_fade_0"] = out["adx_pct_change"] <= 0.0
    out["relaxed_adx_fade_1"] = out["adx_pct_change"] <= -1.0
    return out


def first_time(frame: pd.DataFrame, mask_col: str) -> str:
    if frame.empty or mask_col not in frame:
        return ""
    hit = frame.loc[frame[mask_col]]
    if hit.empty:
        return ""
    return str(hit.iloc[0]["date"])


def reason_summary(eligible: pd.DataFrame) -> tuple[str, str]:
    if eligible.empty:
        return "No eligible regime rows after MFE >= 1.5%.", "Cannot relax from available data."
    if eligible["strict_exhaustion"].any():
        return "Current strict exhaustion hit.", "Already caught by current no-reentry signal logic."
    if eligible["mean_revert"].any():
        return "Base mean_revert hit, but strict exhaustion did not.", "Relax strict exhaustion toward base mean_revert during profitable trades."
    if eligible["momentum_decay"].any():
        return "Momentum_decay hit, but strict exhaustion requires stretch plus ADX-fade.", "Allow profitable momentum_decay exits or add a separate momentum-fade take-profit path."

    fail_counts = {
        "stretch": int((~eligible["strict_stretch_ok"]).sum()),
        "prev_adx": int((~eligible["strict_prev_adx_ok"]).sum()),
        "adx_fade": int((~eligible["strict_adx_fade_ok"]).sum()),
        "still_momentum": int((~eligible["strict_not_momentum_ok"]).sum()),
    }
    primary = max(fail_counts, key=fail_counts.get)
    if primary == "stretch":
        why = "Strict stretch was usually missing."
        relax = "Test lower stretch gates, e.g. KC>=1.5 or BB>=2.5, but only with profit>=1.5%."
    elif primary == "prev_adx":
        why = "Prior ADX was usually below 30."
        relax = "Test prev ADX floor 20-25 for profitable trades."
    elif primary == "adx_fade":
        why = "ADX was not fading by the strict -2.5% threshold."
        relax = "Test ADX fade <=0% or <=-1% instead of <=-2.5%."
    else:
        why = "Bars were still classified as momentum."
        relax = "Do not relax this lightly; momentum protection is probably doing useful work."
    return why, relax


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prices = load_prices()
    strategy = LazySwingStrategy(PARAMS)
    strategy.prepare(prices)

    rows = []
    event_rows = []
    for idx, trade in enumerate(load_trades(), start=1):
        if trade["entry_time"] < START:
            continue
        stats = trade_path_stats(trade, prices)
        exit_pnl = trade["exit_pnl_pct"]
        if exit_pnl is None:
            exit_pnl = stats["computed_exit_pnl_pct"]
        exit_pnl = float(exit_pnl)
        if stats["mfe_pct"] < MIN_MFE_PCT or exit_pnl >= MAX_EXIT_PNL_PCT:
            continue

        reg = regime_rows(strategy, stats["path"], stats["running_mfe"])
        eligible = reg.loc[reg["running_mfe_pct"] >= MIN_MFE_PCT].copy()
        why, relaxation = reason_summary(eligible)
        if not eligible.empty:
            mode_counts = eligible["base_mode"].value_counts().to_dict()
            maxes = eligible[[
                "kc_abs_z_recent",
                "bb_abs_z_recent",
                "prev_exhaustion_adx",
                "adx_pct_change",
                "adx",
                "efficiency",
                "slow_vol_ratio",
            ]].agg({
                "kc_abs_z_recent": "max",
                "bb_abs_z_recent": "max",
                "prev_exhaustion_adx": "max",
                "adx_pct_change": "min",
                "adx": "max",
                "efficiency": "max",
                "slow_vol_ratio": "min",
            }).to_dict()
            strict_fail_summary = {
                "stretch_fail_bars": int((~eligible["strict_stretch_ok"]).sum()),
                "prev_adx_fail_bars": int((~eligible["strict_prev_adx_ok"]).sum()),
                "adx_fade_fail_bars": int((~eligible["strict_adx_fade_ok"]).sum()),
                "momentum_fail_bars": int((~eligible["strict_not_momentum_ok"]).sum()),
            }
        else:
            mode_counts = {}
            maxes = {}
            strict_fail_summary = {}

        row = {
            "trade_id": idx,
            "direction": trade["direction"],
            "entry_time": trade["entry_time"],
            "exit_time": trade["exit_time"],
            "entry_price": trade["entry_price"],
            "exit_price": trade["exit_price"],
            "entry_reason": trade.get("entry_reason"),
            "exit_reason": trade.get("exit_reason"),
            "mfe_pct": round(stats["mfe_pct"], 4),
            "mae_pct": round(stats["mae_pct"], 4),
            "exit_pnl_pct": round(exit_pnl, 4),
            "giveback_from_mfe_pct": round(stats["mfe_pct"] - exit_pnl, 4),
            "eligible_bars": int(len(eligible)),
            "strict_hit_bars": int(eligible["strict_exhaustion"].sum()) if not eligible.empty else 0,
            "mean_revert_hit_bars": int(eligible["mean_revert"].sum()) if not eligible.empty else 0,
            "momentum_decay_hit_bars": int(eligible["momentum_decay"].sum()) if not eligible.empty else 0,
            "momentum_bars": int(eligible["momentum"].sum()) if not eligible.empty else 0,
            "neutral_bars": int(eligible["neutral"].sum()) if not eligible.empty else 0,
            "first_strict_time": first_time(eligible, "strict_exhaustion"),
            "first_mean_revert_time": first_time(eligible, "mean_revert"),
            "first_momentum_decay_time": first_time(eligible, "momentum_decay"),
            "mode_counts": json.dumps(mode_counts, sort_keys=True),
            "why_not_caught": why,
            "relaxation_candidate": relaxation,
            **{k: round(float(v), 4) for k, v in maxes.items()},
            **strict_fail_summary,
        }
        rows.append(row)

        if not eligible.empty:
            sample = eligible.loc[
                eligible["strict_exhaustion"]
                | eligible["mean_revert"]
                | eligible["momentum_decay"]
                | eligible["relaxed_stretch_1_5_2_5"]
                | eligible["relaxed_prev_adx_20"]
                | eligible["relaxed_adx_fade_0"]
            ].copy()
            sample["trade_id"] = idx
            event_rows.append(sample.head(30))

    out = pd.DataFrame(rows)
    out_path = OUT_DIR / "giveback_trades.csv"
    out.to_csv(out_path, index=False)
    if event_rows:
        events = pd.concat(event_rows, ignore_index=True)
    else:
        events = pd.DataFrame()
    events_path = OUT_DIR / "filter_events.csv"
    events.to_csv(events_path, index=False)

    if out.empty:
        report = "# LazySwing 2026 Giveback Diagnostic\n\nNo matching trades found.\n"
    else:
        report_lines = [
            "# LazySwing 2026 Giveback Diagnostic",
            "",
            f"Window: baseline 2026 trades entered on or after `{START.date()}`.",
            f"Selected trades: MFE >= `{MIN_MFE_PCT}%` and exit PnL < `{MAX_EXIT_PNL_PCT}%`.",
            "",
            "## Summary",
            "",
            f"- Matching trades: `{len(out)}`",
            f"- Total MFE available: `{out['mfe_pct'].sum():.2f}%`",
            f"- Total realized exit PnL: `{out['exit_pnl_pct'].sum():.2f}%`",
            f"- Total giveback from MFE: `{out['giveback_from_mfe_pct'].sum():.2f}%`",
            f"- Trades current strict exhaustion would catch: `{int((out['strict_hit_bars'] > 0).sum())}`",
            f"- Trades broader mean_revert would catch: `{int((out['mean_revert_hit_bars'] > 0).sum())}`",
            f"- Trades momentum_decay would catch: `{int((out['momentum_decay_hit_bars'] > 0).sum())}`",
            "",
            "## Trades",
            "",
            "| ID | Dir | Entry | Exit | MFE | Exit PnL | Giveback | Strict | MR | Fade | Why / Relaxation |",
            "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for _, r in out.iterrows():
            report_lines.append(
                "| "
                f"{int(r['trade_id'])} | {r['direction']} | {r['entry_time']} | {r['exit_time']} | "
                f"{r['mfe_pct']:.2f}% | {r['exit_pnl_pct']:.2f}% | {r['giveback_from_mfe_pct']:.2f}% | "
                f"{int(r['strict_hit_bars'])} | {int(r['mean_revert_hit_bars'])} | {int(r['momentum_decay_hit_bars'])} | "
                f"{r['why_not_caught']} {r['relaxation_candidate']} |"
            )
        report = "\n".join(report_lines) + "\n"

    report_path = OUT_DIR / "report.md"
    report_path.write_text(report)
    print(f"Saved {out_path}")
    print(f"Saved {events_path}")
    print(f"Saved {report_path}")
    if not out.empty:
        print(out[[
            "trade_id",
            "direction",
            "entry_time",
            "exit_time",
            "mfe_pct",
            "exit_pnl_pct",
            "giveback_from_mfe_pct",
            "strict_hit_bars",
            "mean_revert_hit_bars",
            "momentum_decay_hit_bars",
            "why_not_caught",
            "relaxation_candidate",
        ]].to_string(index=False))


if __name__ == "__main__":
    main()
