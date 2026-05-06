#!/usr/bin/env python3
"""Robustness checks for LazySwing relaxed take-profit winner."""

from __future__ import annotations

import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config import Config  # noqa: E402
from controller import Controller  # noqa: E402
from reporting.reporter import compute_stats  # noqa: E402
from trade_log import TradeLogReader  # noqa: E402


INITIAL_CASH = 100000.0
SYMBOL = "ETH-PERP-INTX"
OUT_DIR = REPO / "reports" / "lazyswing-takeprofit-robustness"
MAX_WORKERS = 4

PERIODS = [
    {
        "period": "2024h1",
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-01-01",
        "end": "2024-07-01",
    },
    {
        "period": "2024h2",
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv",
        "start": "2024-07-01",
        "end": "2025-01-01",
    },
    {
        "period": "2025",
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv",
        "start": "2025-01-01",
        "end": "2026-01-01",
    },
    {
        "period": "2026",
        "data_file": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
        "start": "2026-01-01",
        "end": "2026-05-06",
    },
]

BASE_PARAMS = {
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
    "cost_per_trade_pct": 0.05,
    "fast_exit_enabled": True,
    "fast_exit_cooldown_bars": 4,
    "fast_exit_rvol_short_period": 24,
    "fast_exit_rvol_long_period": 2016,
    "fast_exit_rvol_low_min": 1.1,
    "fast_exit_rvol_high_min": 1.3,
    "fast_exit_reentry_confirm": True,
    "flat_realign_hourly_closes": 0,
}

REGIME_COMMON = {
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
    "regime_exhaustion_adx_lookback": 2,
    "trail_stop_pct": 0.75,
    "trail_stop_atr_multiple": 0.75,
    "trail_stop_min_gain_pct": 1.5,
    "trail_stop_cooldown_bars": 0,
    "trail_stop_reentry_pct": 0.5,
    "trail_stop_exit_on_signal": True,
    "trail_stop_reentry_enabled": False,
}

CURRENT_STRICT = {
    **REGIME_COMMON,
    "regime_exhaustion_kc_z_min": 2.0,
    "regime_exhaustion_bb_z_min": 3.0,
    "regime_exhaustion_prev_adx_min": 30.0,
    "regime_exhaustion_adx_drop_pct": 2.5,
}

WINNER = {
    **REGIME_COMMON,
    "regime_exhaustion_kc_z_min": 1.5,
    "regime_exhaustion_bb_z_min": 2.5,
    "regime_exhaustion_prev_adx_min": 20.0,
    "regime_exhaustion_adx_drop_pct": 2.5,
}

VARIANTS = [
    ("baseline", {}),
    ("current_strict", CURRENT_STRICT),
    ("winner", WINNER),
    ("stretch_looser_125_225", {**WINNER, "regime_exhaustion_kc_z_min": 1.25, "regime_exhaustion_bb_z_min": 2.25}),
    ("stretch_tighter_175_275", {**WINNER, "regime_exhaustion_kc_z_min": 1.75, "regime_exhaustion_bb_z_min": 2.75}),
    ("prev_adx_25", {**WINNER, "regime_exhaustion_prev_adx_min": 25.0}),
    ("prev_adx_30", {**WINNER, "regime_exhaustion_prev_adx_min": 30.0}),
    ("adx_fade_2_0", {**WINNER, "regime_exhaustion_adx_drop_pct": 2.0}),
    ("adx_fade_3_0", {**WINNER, "regime_exhaustion_adx_drop_pct": 3.0}),
]


def exit_win_rate(trade_log: pd.DataFrame) -> dict:
    wins = losses = 0
    pnl_sum = 0.0
    for _, row in trade_log.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        pnl = details.get("pnl_pct")
        if pnl is None:
            continue
        pnl = float(pnl)
        pnl_sum += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    exits = wins + losses
    return {
        "wr_pct": wins / exits * 100.0 if exits else float("nan"),
        "wins": wins,
        "losses": losses,
        "exits_with_pnl": exits,
        "avg_trade_pnl_pct": pnl_sum / exits if exits else float("nan"),
    }


def trail_stats(trade_log: pd.DataFrame) -> dict:
    exits = 0
    reentries = 0
    pnls = []
    modes = {}
    for _, row in trade_log.iterrows():
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        if details.get("entry_reason") == "regime_trail_reentry":
            reentries += 1
        if details.get("exit_reason") != "regime_trail_stop":
            continue
        exits += 1
        pnl = details.get("pnl_pct")
        if pnl is not None:
            pnls.append(float(pnl))
        regime = ((details.get("indicators") or {}).get("regime_trail") or {})
        mode = regime.get("mode", regime.get("base_mode", "unknown"))
        modes[mode] = modes.get(mode, 0) + 1
    return {
        "trail_exits": exits,
        "trail_reentries": reentries,
        "avg_trail_pnl_pct": sum(pnls) / len(pnls) if pnls else float("nan"),
        "trail_modes": modes,
    }


def build_config(period: dict, tag: str, params_extra: dict) -> Config:
    params = {**BASE_PARAMS, **params_extra}
    return Config(
        {
            "backtest": {
                "name": f"LazySwing_robustness_{period['period']}_{tag}",
                "version": "takeprofit-robustness",
                "initial_cash": INITIAL_CASH,
                "start_date": period["start"],
                "end_date": period["end"],
            },
            "data_source": {
                "type": "csv_file",
                "parser": "coinbase_intx_kline",
                "params": {
                    "file_path": period["data_file"],
                    "symbol": SYMBOL,
                },
            },
            "strategies": [{"type": "lazy_swing", "params": params}],
        }
    )


def run_case(args: tuple[dict, str, dict]) -> dict:
    period, tag, params_extra = args
    run_dir = OUT_DIR / "runs" / period["period"] / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = build_config(period, tag, params_extra)
    t0 = time.time()
    result = Controller(cfg, output_dir=str(run_dir)).run()[0]
    elapsed = time.time() - t0
    trade_log = TradeLogReader().read(result.trade_log_path)
    stats = compute_stats(trade_log, cfg.initial_cash, 0.05)
    return {
        "period": period["period"],
        "tag": tag,
        "total_return_pct": float(stats["total_return"]),
        "final_value": float(result.final_value),
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "num_entries": int(stats["num_buys"]) + int(stats["num_shorts"]),
        "elapsed_sec": round(elapsed, 1),
        "trade_log_path": result.trade_log_path,
        **exit_win_rate(trade_log),
        **trail_stats(trade_log),
        **params_extra,
    }


def read_trade_log(path: str | Path) -> pd.DataFrame:
    return TradeLogReader().read(str(path))


def monthly_returns(summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in summary.iterrows():
        if row["tag"] not in {"baseline", "current_strict", "winner"}:
            continue
        log = read_trade_log(row["trade_log_path"])
        if log.empty:
            continue
        pv = log[["date", "portfolio_value"]].copy()
        pv["date"] = pd.to_datetime(pv["date"])
        pv = pv.sort_values("date")
        pv["month"] = pv["date"].dt.to_period("M").astype(str)
        for month, group in pv.groupby("month"):
            start_value = float(group.iloc[0]["portfolio_value"])
            end_value = float(group.iloc[-1]["portfolio_value"])
            rows.append({
                "period": row["period"],
                "tag": row["tag"],
                "month": month,
                "start_value": start_value,
                "end_value": end_value,
                "monthly_return_pct": (end_value / start_value - 1.0) * 100.0,
            })
    return pd.DataFrame(rows)


def parse_details(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def extract_trades(log: pd.DataFrame) -> list[dict]:
    trades = []
    current = None
    for _, row in log.iterrows():
        action = row["action"]
        details = parse_details(row.get("details", ""))
        if action in {"BUY", "SHORT"} and current is None:
            current = {
                "entry_time": row["date"],
                "direction": "long" if action == "BUY" else "short",
                "entry_price": float(row["price"]),
                "entry_reason": details.get("entry_reason") or details.get("reason"),
            }
            continue
        if action in {"SELL", "COVER"} and current is not None:
            current.update({
                "exit_time": row["date"],
                "exit_price": float(row["price"]),
                "exit_action": action,
                "exit_reason": details.get("exit_reason") or details.get("reason"),
                "exit_pnl_pct": details.get("pnl_pct"),
            })
            trades.append(current)
            current = None
    return trades


def classify_winner_exits(summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for period in sorted(summary["period"].unique()):
        base_row = summary[(summary["period"] == period) & (summary["tag"] == "baseline")]
        winner_row = summary[(summary["period"] == period) & (summary["tag"] == "winner")]
        if base_row.empty or winner_row.empty:
            continue
        base_trades = extract_trades(read_trade_log(base_row.iloc[0]["trade_log_path"]))
        winner_log = read_trade_log(winner_row.iloc[0]["trade_log_path"])
        for _, row in winner_log.iterrows():
            details = parse_details(row.get("details", ""))
            if details.get("exit_reason") != "regime_trail_stop":
                continue
            exit_time = row["date"]
            action = row["action"]
            direction = "long" if action == "SELL" else "short"
            match = None
            for trade in base_trades:
                if (
                    trade["direction"] == direction
                    and trade["entry_time"] <= exit_time <= trade["exit_time"]
                ):
                    match = trade
                    break
            winner_pnl = float(details.get("pnl_pct", math.nan))
            baseline_pnl = (
                float(match["exit_pnl_pct"])
                if match is not None and match.get("exit_pnl_pct") is not None
                else math.nan
            )
            delta = winner_pnl - baseline_pnl if not math.isnan(baseline_pnl) else math.nan
            if math.isnan(delta):
                verdict = "unknown"
            elif delta > 0.25:
                verdict = "correct"
            elif delta < -0.25:
                verdict = "incorrect"
            else:
                verdict = "neutral"
            rows.append({
                "period": period,
                "exit_time": exit_time,
                "action": action,
                "price": float(row["price"]),
                "winner_pnl_pct": winner_pnl,
                "baseline_exit_time": match["exit_time"] if match is not None else pd.NaT,
                "baseline_pnl_pct": baseline_pnl,
                "delta_vs_baseline_pnl_pct": delta,
                "verdict": verdict,
            })
    return pd.DataFrame(rows)


def compound_summary(summary: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for tag, group in summary.groupby("tag"):
        if len(group) != len(PERIODS):
            continue
        multiplier = float((1.0 + group["total_return_pct"] / 100.0).prod())
        exits = int(group["exits_with_pnl"].sum())
        wins = int(group["wins"].sum())
        rows.append({
            "tag": tag,
            "compound_return_pct": (multiplier - 1.0) * 100.0,
            "final_multiple": multiplier,
            "aggregate_wr_pct": wins / exits * 100.0 if exits else float("nan"),
            "avg_pnl_per_exit_pct": group["total_return_pct"].sum() / exits if exits else float("nan"),
            "trail_exits": int(group["trail_exits"].sum()),
        })
    return pd.DataFrame(rows).sort_values("compound_return_pct", ascending=False)


def write_report(
    summary: pd.DataFrame,
    compound: pd.DataFrame,
    monthly: pd.DataFrame,
    attribution: pd.DataFrame,
) -> None:
    lines = [
        "# LazySwing Take-Profit Robustness",
        "",
        "Robustness checks for the relaxed stretch + ADX fade 2.5 no-reentry take-profit rule.",
        "",
        "Winner parameters:",
        "",
        "```text",
        "profit >= 1.5%",
        "AND (KC recent >= 1.5 OR BB recent >= 2.5)",
        "AND previous ADX >= 20",
        "AND ADX faded by at least 2.5%",
        "AND not momentum",
        "AND no same-side trail reentry",
        "```",
        "",
        "## Compound Results",
        "",
        compound.to_markdown(index=False),
        "",
        "## Period Results",
        "",
        summary[[
            "period",
            "tag",
            "total_return_pct",
            "wr_pct",
            "avg_trade_pnl_pct",
            "max_dd_pct",
            "trail_exits",
        ]].to_markdown(index=False),
        "",
        "## Monthly Breadth",
        "",
    ]
    if not monthly.empty:
        pivot = monthly.pivot_table(
            index="month",
            columns="tag",
            values="monthly_return_pct",
            aggfunc="sum",
        ).reset_index()
        lines.append(pivot.to_markdown(index=False))
    else:
        lines.append("No monthly rows.")
    lines.extend(["", "## Exit Attribution", ""])
    if not attribution.empty:
        counts = attribution.groupby(["period", "verdict"]).size().reset_index(name="count")
        lines.append(counts.to_markdown(index=False))
    else:
        lines.append("No attribution rows.")
    (OUT_DIR / "report.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jobs = [(period, tag, params) for period in PERIODS for tag, params in VARIANTS]
    rows = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(run_case, job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(
                f"{row['period']:<8} {row['tag']:<26} "
                f"ret={row['total_return_pct']:+8.2f}% "
                f"WR={row['wr_pct']:5.1f}% "
                f"trail={row['trail_exits']:3d} "
                f"{row['elapsed_sec']}s",
                flush=True,
            )
    summary = pd.DataFrame(rows)
    period_order = {p["period"]: i for i, p in enumerate(PERIODS)}
    variant_order = {tag: i for i, (tag, _) in enumerate(VARIANTS)}
    summary["period_order"] = summary["period"].map(period_order)
    summary["variant_order"] = summary["tag"].map(variant_order)
    summary = summary.sort_values(["period_order", "variant_order"]).drop(
        columns=["period_order", "variant_order"]
    )
    summary.to_csv(OUT_DIR / "summary.csv", index=False)

    compound = compound_summary(summary)
    compound.to_csv(OUT_DIR / "compound.csv", index=False)
    monthly = monthly_returns(summary)
    monthly.to_csv(OUT_DIR / "monthly.csv", index=False)
    attribution = classify_winner_exits(summary)
    attribution.to_csv(OUT_DIR / "exit_attribution.csv", index=False)
    write_report(summary, compound, monthly, attribution)
    print(f"Saved outputs under {OUT_DIR}")
    print(f"Elapsed: {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
