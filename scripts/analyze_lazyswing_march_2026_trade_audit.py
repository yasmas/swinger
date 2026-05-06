#!/usr/bin/env python3
"""Audit LazySwing ETH trades from 2026-03-01 through refreshed 2026 data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from reporting.reporter import compute_stats  # noqa: E402
from strategies.lazy_swing import LazySwingStrategy  # noqa: E402
from trade_log import TradeLogReader  # noqa: E402


DATA_FILE = REPO / "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv"
HOF_LOG = (
    REPO
    / "reports/lazyswing-takeprofit-robustness/runs/2026_0301_now/stretch_tighter_175_275"
    / "LazySwing_robustness_2026_0301_now_stretch_tighter_175_275_lazy_swing_takeprofit-robustness.csv"
)
RELAXED_LOG = (
    REPO
    / "reports/lazyswing-takeprofit-robustness/runs/2026_0301_now/relaxed_kc15_bb25"
    / "LazySwing_robustness_2026_0301_now_relaxed_kc15_bb25_lazy_swing_takeprofit-robustness.csv"
)
OUT_DIR = REPO / "reports" / "lazyswing-2026-march-trade-audit"
START = pd.Timestamp("2026-03-01")
END = pd.Timestamp("2026-05-06")
COST_PCT = 0.05
MIN_GAIN_PCT = 1.5


HOF_PARAMS = {
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
    "regime_exhaustion_kc_z_min": 1.75,
    "regime_exhaustion_bb_z_min": 2.75,
    "regime_exhaustion_adx_lookback": 2,
    "regime_exhaustion_prev_adx_min": 20.0,
    "regime_exhaustion_adx_drop_pct": 2.5,
    "trail_stop_pct": 0.75,
    "trail_stop_atr_multiple": 0.75,
    "trail_stop_min_gain_pct": MIN_GAIN_PCT,
    "trail_stop_cooldown_bars": 0,
    "trail_stop_reentry_pct": 0.5,
    "trail_stop_exit_on_signal": True,
    "trail_stop_reentry_enabled": False,
}


def parse_details(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def load_prices() -> pd.DataFrame:
    df = pd.read_csv(DATA_FILE)
    df["date"] = pd.to_datetime(df["open_time"].astype(float), unit="ms", utc=True)
    df["date"] = df["date"].dt.tz_localize(None)
    df = df.set_index("date").sort_index()
    df = df.loc[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def load_log(path: Path) -> pd.DataFrame:
    return TradeLogReader().read(str(path))


def extract_trades(log: pd.DataFrame) -> list[dict]:
    trades = []
    current = None
    for _, row in log.iterrows():
        action = row["action"]
        details = parse_details(row.get("details", {}))
        if action in {"BUY", "SHORT"}:
            if current is not None:
                current["forced_unclosed"] = True
                trades.append(current)
            current = {
                "entry_time": pd.Timestamp(row["date"]),
                "direction": "long" if action == "BUY" else "short",
                "entry_action": action,
                "entry_price": float(row["price"]),
                "entry_reason": details.get("entry_reason") or details.get("reason"),
                "entry_immediate_flip": bool(details.get("immediate_flip", False)),
                "entry_gap": (details.get("gap_info") or {}).get("gap"),
            }
            continue
        if action in {"SELL", "COVER"} and current is not None:
            current.update(
                {
                    "exit_time": pd.Timestamp(row["date"]),
                    "exit_action": action,
                    "exit_price": float(row["price"]),
                    "exit_reason": details.get("exit_reason") or details.get("reason"),
                    "bars_held": details.get("bars_held"),
                    "pnl_pct": details.get("pnl_pct"),
                }
            )
            trail = ((details.get("indicators") or {}).get("regime_trail") or {})
            current["trail_base_mode_at_exit"] = trail.get("base_mode")
            current["trail_mode_at_exit"] = trail.get("mode")
            trades.append(current)
            current = None
    return trades


def trade_path_stats(trade: dict, prices: pd.DataFrame) -> dict:
    path = prices.loc[trade["entry_time"] : trade["exit_time"]].copy()
    entry = trade["entry_price"]
    exit_price = trade["exit_price"]
    if trade["direction"] == "long":
        favorable = path["high"] / entry - 1.0
        adverse = path["low"] / entry - 1.0
        close_favorable = path["close"] / entry - 1.0
        running_mfe = favorable.cummax() * 100.0
        running_close_mfe = close_favorable.cummax() * 100.0
        running_mae = adverse.cummin() * 100.0
        computed_pnl = (exit_price / entry - 1.0) * 100.0 - COST_PCT
    else:
        favorable = entry / path["low"] - 1.0
        adverse = entry / path["high"] - 1.0
        close_favorable = entry / path["close"] - 1.0
        running_mfe = favorable.cummax() * 100.0
        running_close_mfe = close_favorable.cummax() * 100.0
        running_mae = adverse.cummin() * 100.0
        computed_pnl = (entry / exit_price - 1.0) * 100.0 - COST_PCT
    return {
        "path": path,
        "mfe_pct": float(favorable.max() * 100.0) if not favorable.empty else 0.0,
        "close_mfe_pct": float(close_favorable.max() * 100.0) if not close_favorable.empty else 0.0,
        "mae_pct": float(adverse.min() * 100.0) if not adverse.empty else 0.0,
        "running_mfe": running_mfe,
        "running_close_mfe": running_close_mfe,
        "running_mae": running_mae,
        "computed_pnl_pct": computed_pnl,
    }


def regime_frame(strategy: LazySwingStrategy, path: pd.DataFrame, running_mfe: pd.Series) -> pd.DataFrame:
    rows = []
    for ts in path.index:
        hourly_idx = strategy._5m_to_hourly.get(ts)
        if hourly_idx is None:
            continue
        info = strategy._regime_trail_info(hourly_idx)
        if not info.get("ready"):
            continue
        momentum = info.get("base_mode") == "momentum"
        stretch_ok = (
            float(info.get("kc_abs_z_recent", 0.0)) >= HOF_PARAMS["regime_exhaustion_kc_z_min"]
            or float(info.get("bb_abs_z_recent", 0.0)) >= HOF_PARAMS["regime_exhaustion_bb_z_min"]
        )
        prev_adx_ok = float(info.get("prev_exhaustion_adx", 0.0)) >= HOF_PARAMS["regime_exhaustion_prev_adx_min"]
        adx_fade_ok = float(info.get("adx_pct_change", 999.0)) <= -HOF_PARAMS["regime_exhaustion_adx_drop_pct"]
        not_momentum_ok = not momentum
        relaxed_stretch_ok = (
            float(info.get("kc_abs_z_recent", 0.0)) >= 1.5
            or float(info.get("bb_abs_z_recent", 0.0)) >= 2.5
        )
        relaxed_hit = (
            float(running_mfe.loc[ts]) >= MIN_GAIN_PCT
            and relaxed_stretch_ok
            and prev_adx_ok
            and adx_fade_ok
            and not_momentum_ok
        )
        rows.append(
            {
                "date": ts,
                "running_mfe_pct": float(running_mfe.loc[ts]),
                "base_mode": info.get("base_mode"),
                "momentum": momentum,
                "kc_recent": float(info.get("kc_abs_z_recent", 0.0)),
                "bb_recent": float(info.get("bb_abs_z_recent", 0.0)),
                "prev_adx": float(info.get("prev_exhaustion_adx", 0.0)),
                "adx_pct_change": float(info.get("adx_pct_change", 999.0)),
                "stretch_ok": stretch_ok,
                "prev_adx_ok": prev_adx_ok,
                "adx_fade_ok": adx_fade_ok,
                "not_momentum_ok": not_momentum_ok,
                "hof_hit": bool(info.get("strict_exhaustion")) and float(running_mfe.loc[ts]) >= MIN_GAIN_PCT,
                "relaxed_hit": relaxed_hit,
            }
        )
    return pd.DataFrame(rows)


def miss_reason(reg: pd.DataFrame, mfe_pct: float, close_mfe_pct: float, exit_reason: str) -> dict:
    if exit_reason == "regime_trail_stop":
        return {
            "takeprofit_status": "caught",
            "why_takeprofit_missed": "Caught by HOF strict-exhaustion take-profit.",
            "relaxed_would_catch": False,
        }
    if close_mfe_pct < MIN_GAIN_PCT and mfe_pct >= MIN_GAIN_PCT:
        return {
            "takeprofit_status": "intrabar_only",
            "why_takeprofit_missed": "High/low MFE reached +1.5%, but close-based MFE did not; the strategy trails closes.",
            "relaxed_would_catch": False,
        }
    if close_mfe_pct < MIN_GAIN_PCT:
        return {
            "takeprofit_status": "not_eligible",
            "why_takeprofit_missed": "Never reached +1.5% open profit on a 5m close.",
            "relaxed_would_catch": False,
        }
    eligible = reg.loc[reg["running_mfe_pct"] >= MIN_GAIN_PCT].copy()
    if eligible.empty:
        return {
            "takeprofit_status": "not_eligible",
            "why_takeprofit_missed": "No ready regime rows after +1.5% open profit.",
            "relaxed_would_catch": False,
        }
    if eligible["hof_hit"].any():
        if exit_reason in {"st_flip", "st_flip_ratio_safety"}:
            return {
                "takeprofit_status": "post_flip_unchecked",
                "why_takeprofit_missed": "HOF gates lined up after ST was already opposite; code only checks the trail while ST still agrees with the position.",
                "relaxed_would_catch": bool(eligible["relaxed_hit"].any()),
            }
        return {
            "takeprofit_status": "priority_mismatch",
            "why_takeprofit_missed": "HOF gates lined up, but another exit path had priority first.",
            "relaxed_would_catch": bool(eligible["relaxed_hit"].any()),
        }

    gate_cols = ["stretch_ok", "prev_adx_ok", "adx_fade_ok", "not_momentum_ok"]
    eligible["gate_count"] = eligible[gate_cols].sum(axis=1)
    closest = eligible.loc[eligible["gate_count"] == eligible["gate_count"].max()]
    missing_counts = {col: int((~closest[col]).sum()) for col in gate_cols}
    primary = max(missing_counts, key=missing_counts.get)
    max_kc = eligible["kc_recent"].max()
    max_bb = eligible["bb_recent"].max()
    max_prev_adx = eligible["prev_adx"].max()
    min_adx_change = eligible["adx_pct_change"].min()

    if primary == "stretch_ok":
        why = f"Stretch did not reach HOF gate on the closest bars; max KC {max_kc:.2f}, max BB {max_bb:.2f}."
    elif primary == "prev_adx_ok":
        why = f"Prior ADX was too low on the closest bars; max prior ADX {max_prev_adx:.2f}."
    elif primary == "adx_fade_ok":
        why = f"ADX did not fade enough on the closest bars; best ADX change {min_adx_change:.2f}%."
    else:
        why = "Momentum-on protection blocked the take-profit on the closest bars."

    return {
        "takeprofit_status": "missed",
        "why_takeprofit_missed": why,
        "relaxed_would_catch": bool(eligible["relaxed_hit"].any()),
    }


def pct_fmt(value: float) -> str:
    return f"{value:+.2f}%"


def reason_table(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    grouped = df.groupby(group_col, dropna=False)
    rows = []
    for key, g in grouped:
        wins = int((g["pnl_pct"] > 0).sum())
        losses = int((g["pnl_pct"] < 0).sum())
        rows.append(
            {
                group_col: key,
                "trades": len(g),
                "wr_pct": wins / (wins + losses) * 100.0 if wins + losses else 0.0,
                "avg_pnl_pct": g["pnl_pct"].mean(),
                "sum_pnl_pct": g["pnl_pct"].sum(),
                "avg_mfe_pct": g["mfe_pct"].mean(),
                "avg_mae_pct": g["mae_pct"].mean(),
            }
        )
    return pd.DataFrame(rows).sort_values(["sum_pnl_pct"], ascending=True)


def markdown_table(df: pd.DataFrame, cols: list[str], max_rows: int | None = None) -> list[str]:
    show = df[cols].copy()
    if max_rows is not None:
        show = show.head(max_rows)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in show.iterrows():
        values = []
        for col in cols:
            val = row[col]
            if isinstance(val, float):
                values.append(f"{val:.2f}")
            else:
                values.append(str(val))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    prices = load_prices()
    strategy = LazySwingStrategy(HOF_PARAMS)
    strategy.prepare(prices)
    log = load_log(HOF_LOG)
    stats = compute_stats(log, 100000.0, COST_PCT)
    relaxed_log = load_log(RELAXED_LOG) if RELAXED_LOG.exists() else pd.DataFrame()

    rows = []
    for trade_id, trade in enumerate(extract_trades(log), start=1):
        if trade.get("exit_time") is None or trade["entry_time"] < START:
            continue
        path_stats = trade_path_stats(trade, prices)
        reg = regime_frame(strategy, path_stats["path"], path_stats["running_close_mfe"])
        pnl = trade.get("pnl_pct")
        pnl = float(pnl) if pnl is not None else float(path_stats["computed_pnl_pct"])
        miss = miss_reason(
            reg,
            path_stats["mfe_pct"],
            path_stats["close_mfe_pct"],
            trade.get("exit_reason", ""),
        )
        eligible = reg.loc[reg["running_mfe_pct"] >= MIN_GAIN_PCT]
        mode_counts = eligible["base_mode"].value_counts().to_dict() if not eligible.empty else {}
        rows.append(
            {
                "trade_id": trade_id,
                "side": trade["direction"],
                "entry_time": trade["entry_time"],
                "entry_reason": trade.get("entry_reason"),
                "immediate_flip_entry": trade.get("entry_immediate_flip"),
                "exit_time": trade["exit_time"],
                "exit_reason": trade.get("exit_reason"),
                "bars_held": trade.get("bars_held"),
                "entry_price": trade["entry_price"],
                "exit_price": trade["exit_price"],
                "pnl_pct": round(pnl, 4),
                "mfe_pct": round(path_stats["mfe_pct"], 4),
                "close_mfe_pct": round(path_stats["close_mfe_pct"], 4),
                "mae_pct": round(path_stats["mae_pct"], 4),
                "giveback_pct": round(path_stats["mfe_pct"] - pnl, 4),
                "takeprofit_status": miss["takeprofit_status"],
                "why_takeprofit_missed": miss["why_takeprofit_missed"],
                "relaxed_would_catch": miss["relaxed_would_catch"],
                "eligible_bars_after_1p5_mfe": int(len(eligible)),
                "mode_counts_after_1p5_mfe": json.dumps(mode_counts, sort_keys=True),
            }
        )

    out = pd.DataFrame(rows)
    out["exit_date"] = pd.to_datetime(out["exit_time"]).dt.date.astype(str)
    all_path = OUT_DIR / "all_trades.csv"
    out.to_csv(all_path, index=False)

    bad = out.loc[out["pnl_pct"] <= 0].copy()
    gave_back = out.loc[(out["mfe_pct"] >= MIN_GAIN_PCT) & (out["pnl_pct"] < 0.5)].copy()
    by_exit = reason_table(out, "exit_reason")
    by_entry = reason_table(out, "entry_reason")
    by_side = reason_table(out, "side")
    by_status = reason_table(out, "takeprofit_status")
    daily = (
        out.groupby("exit_date")
        .agg(
            trades=("trade_id", "count"),
            avg_pnl_pct=("pnl_pct", "mean"),
            sum_pnl_pct=("pnl_pct", "sum"),
            wins=("pnl_pct", lambda s: int((s > 0).sum())),
            losses=("pnl_pct", lambda s: int((s < 0).sum())),
        )
        .reset_index()
        .sort_values("sum_pnl_pct")
    )

    for name, frame in [
        ("by_exit_reason.csv", by_exit),
        ("by_entry_reason.csv", by_entry),
        ("by_side.csv", by_side),
        ("by_takeprofit_status.csv", by_status),
        ("bad_trades.csv", bad),
        ("giveback_trades.csv", gave_back),
        ("daily.csv", daily),
    ]:
        frame.to_csv(OUT_DIR / name, index=False)

    wins = int((out["pnl_pct"] > 0).sum())
    losses = int((out["pnl_pct"] < 0).sum())
    small = int((out["pnl_pct"].abs() <= 0.5).sum())
    never_eligible = int((out["close_mfe_pct"] < MIN_GAIN_PCT).sum())
    intrabar_only = int((out["takeprofit_status"] == "intrabar_only").sum())
    caught = int((out["exit_reason"] == "regime_trail_stop").sum())
    relaxed_extra = int(out["relaxed_would_catch"].sum())

    report_lines = [
        "# LazySwing ETH March 2026 Trade Audit",
        "",
        f"Window: `{START.date()}` to `{END.date()}`.",
        f"Trade log: `{HOF_LOG}`.",
        "",
        "## Summary",
        "",
        f"- HOF return: `{float(stats['total_return']):.2f}%`.",
        f"- Closed trades with PnL: `{len(out)}`.",
        f"- Win rate: `{wins / (wins + losses) * 100.0:.2f}%` (`{wins}` wins / `{losses}` losses).",
        f"- Avg PnL per exit: `{out['pnl_pct'].mean():.3f}%`.",
        f"- Sum of per-trade PnL: `{out['pnl_pct'].sum():.2f}%`.",
        f"- Median PnL per exit: `{out['pnl_pct'].median():.3f}%`.",
        f"- Small churn trades (`abs(PnL) <= 0.5%`): `{small}`.",
        f"- Trades that never reached +1.5% open profit on a 5m close: `{never_eligible}`.",
        f"- Of those, high/low MFE did touch +1.5% intrabar only: `{intrabar_only}`.",
        f"- HOF take-profit exits: `{caught}`.",
        f"- Trades the looser KC1.5 / BB2.5 stretch gate would additionally catch in this audit: `{relaxed_extra}`.",
        "",
        "## By Exit Reason",
        "",
        *markdown_table(by_exit, ["exit_reason", "trades", "wr_pct", "avg_pnl_pct", "sum_pnl_pct", "avg_mfe_pct", "avg_mae_pct"]),
        "",
        "## By Entry Reason",
        "",
        *markdown_table(by_entry, ["entry_reason", "trades", "wr_pct", "avg_pnl_pct", "sum_pnl_pct", "avg_mfe_pct", "avg_mae_pct"]),
        "",
        "## Worst Days By Sum PnL",
        "",
        *markdown_table(daily, ["exit_date", "trades", "avg_pnl_pct", "sum_pnl_pct", "wins", "losses"], max_rows=10),
        "",
        "## Giveback Trades",
        "",
        "Trades with MFE >= 1.5% but final PnL < 0.5%. These are the exact trades the take-profit project is trying to reduce.",
        "",
        *markdown_table(
            gave_back,
            [
                "trade_id",
                "side",
                "entry_time",
                "exit_time",
                "entry_reason",
                "exit_reason",
                "pnl_pct",
                "mfe_pct",
                "close_mfe_pct",
                "giveback_pct",
                "takeprofit_status",
                "why_takeprofit_missed",
                "relaxed_would_catch",
            ],
        ),
        "",
        "## All Trades",
        "",
        *markdown_table(
            out,
            [
                "trade_id",
                "side",
                "entry_time",
                "exit_time",
                "entry_reason",
                "exit_reason",
                "pnl_pct",
                "mfe_pct",
                "close_mfe_pct",
                "mae_pct",
                "giveback_pct",
                "takeprofit_status",
                "why_takeprofit_missed",
            ],
        ),
        "",
    ]
    (OUT_DIR / "report.md").write_text("\n".join(report_lines))

    print(f"Saved {all_path}")
    print(f"Saved {OUT_DIR / 'report.md'}")
    print(f"return_pct={float(stats['total_return']):.4f}")
    print(f"trades={len(out)} wins={wins} losses={losses} avg_pnl={out['pnl_pct'].mean():.4f}")
    print(f"never_eligible={never_eligible} caught={caught} relaxed_extra={relaxed_extra}")
    print("by_exit_reason")
    print(by_exit.to_string(index=False))


if __name__ == "__main__":
    main()
