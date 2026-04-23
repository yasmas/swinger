#!/usr/bin/env python3
"""Broad 2025 QQQ LazySwing sweep around QQQ HOF ST and ETH-style RVOL regime gating."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

INITIAL_CASH = 100000.0
DATA_FILE = "data/QQQ-5m-2024-2025.csv"
SYMBOL = "QQQ"

BASE_PARAMS = {
    "resample_interval": "30min",
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
}

ST_GRID = [
    (12, 1.00),
    (12, 1.25),
    (12, 1.50),
    (20, 1.00),
    (20, 1.25),
    (20, 1.50),
    (28, 1.00),
    (28, 1.25),
    (28, 1.50),
]

REGIME_PRESETS = [
    {
        "regime_tag": "vp12_e090_x080_d24",
        "adaptive_st_vol_period": 12,
        "adaptive_st_vol_long_period": 336,
        "adaptive_st_enter_ratio_threshold": 0.90,
        "adaptive_st_exit_ratio_threshold": 0.80,
        "adaptive_st_min_high_bars": 24,
    },
    {
        "regime_tag": "vp24_e100_x085_d48",
        "adaptive_st_vol_period": 24,
        "adaptive_st_vol_long_period": 336,
        "adaptive_st_enter_ratio_threshold": 1.00,
        "adaptive_st_exit_ratio_threshold": 0.85,
        "adaptive_st_min_high_bars": 48,
    },
    {
        "regime_tag": "vp48_e110_x090_d48",
        "adaptive_st_vol_period": 48,
        "adaptive_st_vol_long_period": 336,
        "adaptive_st_enter_ratio_threshold": 1.10,
        "adaptive_st_exit_ratio_threshold": 0.90,
        "adaptive_st_min_high_bars": 48,
    },
]

GATE_PRESETS = [
    {
        "gate_tag": "baseline",
        "params": {},
        "meta": {"gate_family": "baseline", "mode": "-", "low_ratio": None, "high_ratio": None, "low_stop": None, "high_stop": None, "power": None},
        "uses_regime": False,
    },
    {
        "gate_tag": "fixed_r070_s100",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_min": 0.70,
            "flip_vol_ratio_safety_stop_pct": 1.0,
        },
        "meta": {"gate_family": "fixed", "mode": "fixed", "low_ratio": 0.70, "high_ratio": 0.70, "low_stop": 1.0, "high_stop": 1.0, "power": None},
        "uses_regime": False,
    },
    {
        "gate_tag": "fixed_r085_s150",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_min": 0.85,
            "flip_vol_ratio_safety_stop_pct": 1.5,
        },
        "meta": {"gate_family": "fixed", "mode": "fixed", "low_ratio": 0.85, "high_ratio": 0.85, "low_stop": 1.5, "high_stop": 1.5, "power": None},
        "uses_regime": False,
    },
    {
        "gate_tag": "linear_060_090_s05_15",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_regime_mode": "linear",
            "flip_vol_ratio_regime_low_min": 0.60,
            "flip_vol_ratio_regime_high_min": 0.90,
            "flip_vol_ratio_regime_low_stop_pct": 0.5,
            "flip_vol_ratio_regime_high_stop_pct": 1.5,
        },
        "meta": {"gate_family": "dynamic", "mode": "linear", "low_ratio": 0.60, "high_ratio": 0.90, "low_stop": 0.5, "high_stop": 1.5, "power": None},
        "uses_regime": True,
    },
    {
        "gate_tag": "linear_070_100_s10_25",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_regime_mode": "linear",
            "flip_vol_ratio_regime_low_min": 0.70,
            "flip_vol_ratio_regime_high_min": 1.00,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 2.5,
        },
        "meta": {"gate_family": "dynamic", "mode": "linear", "low_ratio": 0.70, "high_ratio": 1.00, "low_stop": 1.0, "high_stop": 2.5, "power": None},
        "uses_regime": True,
    },
    {
        "gate_tag": "linear_080_110_s10_35",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_regime_mode": "linear",
            "flip_vol_ratio_regime_low_min": 0.80,
            "flip_vol_ratio_regime_high_min": 1.10,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 3.5,
        },
        "meta": {"gate_family": "dynamic", "mode": "linear", "low_ratio": 0.80, "high_ratio": 1.10, "low_stop": 1.0, "high_stop": 3.5, "power": None},
        "uses_regime": True,
    },
    {
        "gate_tag": "squared_060_090_s05_15_p15",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_regime_mode": "squared",
            "flip_vol_ratio_regime_low_min": 0.60,
            "flip_vol_ratio_regime_high_min": 0.90,
            "flip_vol_ratio_regime_low_stop_pct": 0.5,
            "flip_vol_ratio_regime_high_stop_pct": 1.5,
            "flip_vol_ratio_regime_power": 1.5,
        },
        "meta": {"gate_family": "dynamic", "mode": "squared", "low_ratio": 0.60, "high_ratio": 0.90, "low_stop": 0.5, "high_stop": 1.5, "power": 1.5},
        "uses_regime": True,
    },
    {
        "gate_tag": "squared_070_100_s10_25_p15",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_regime_mode": "squared",
            "flip_vol_ratio_regime_low_min": 0.70,
            "flip_vol_ratio_regime_high_min": 1.00,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 2.5,
            "flip_vol_ratio_regime_power": 1.5,
        },
        "meta": {"gate_family": "dynamic", "mode": "squared", "low_ratio": 0.70, "high_ratio": 1.00, "low_stop": 1.0, "high_stop": 2.5, "power": 1.5},
        "uses_regime": True,
    },
    {
        "gate_tag": "squared_080_110_s10_30_p15",
        "params": {
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_short_period": 4,
            "flip_vol_ratio_long_period": 336,
            "flip_vol_ratio_regime_mode": "squared",
            "flip_vol_ratio_regime_low_min": 0.80,
            "flip_vol_ratio_regime_high_min": 1.10,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 3.0,
            "flip_vol_ratio_regime_power": 1.5,
        },
        "meta": {"gate_family": "dynamic", "mode": "squared", "low_ratio": 0.80, "high_ratio": 1.10, "low_stop": 1.0, "high_stop": 3.0, "power": 1.5},
        "uses_regime": True,
    },
]


def exit_win_rate(trade_log: pd.DataFrame) -> tuple[float, int, int, int]:
    wins = losses = 0
    for _, row in trade_log.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        pnl = details.get("pnl_pct")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    exited = wins + losses
    wr = (wins / exited * 100.0) if exited else float("nan")
    return wr, wins, losses, exited


def build_runs(atr_filter: set[int] | None = None) -> list[tuple[str, dict, dict]]:
    runs: list[tuple[str, dict, dict]] = []
    for atr, mult in ST_GRID:
        if atr_filter is not None and atr not in atr_filter:
            continue
        st_params = {
            **BASE_PARAMS,
            "supertrend_atr_period": atr,
            "supertrend_multiplier": mult,
        }

        for gate in GATE_PRESETS:
            if gate["uses_regime"]:
                for regime in REGIME_PRESETS:
                    tag = f"st{atr}_m{str(mult).replace('.', 'p')}_{regime['regime_tag']}_{gate['gate_tag']}"
                    params = {**st_params, **regime, **gate["params"]}
                    meta = {
                        "atr": atr,
                        "mult": mult,
                        **regime,
                        **gate["meta"],
                    }
                    runs.append((tag, params, meta))
            else:
                tag = f"st{atr}_m{str(mult).replace('.', 'p')}_{gate['gate_tag']}"
                params = {**st_params, **gate["params"]}
                meta = {
                    "atr": atr,
                    "mult": mult,
                    "adaptive_st_vol_period": None,
                    "adaptive_st_enter_ratio_threshold": None,
                    "adaptive_st_exit_ratio_threshold": None,
                    "adaptive_st_min_high_bars": None,
                    **gate["meta"],
                }
                runs.append((tag, params, meta))
    return runs


def fmt_row(row: dict) -> str:
    return (
        f"{row['tag']:<58} "
        f"ret={row['total_return_pct']:+9.2f}%  "
        f"WR={row['wr_pct']:5.1f}% ({row['wins']}/{row['exits_with_pnl']})  "
        f"Sh={row['sharpe']:+5.2f}  "
        f"DD={row['max_dd_pct']:+6.2f}%  "
        f"trades={row['num_trades']:>4}  "
        f"{row['elapsed_sec']:>5}s"
    )


def main() -> None:
    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="qqq_2025_rvol_broad")
    ap.add_argument("--atrs", default="", help="Comma-separated ATR values to run, e.g. 12,20")
    args = ap.parse_args()

    label = args.label
    output_root = REPO / "reports" / label
    output_root.mkdir(parents=True, exist_ok=True)
    atr_filter = None
    if args.atrs.strip():
        atr_filter = {int(x.strip()) for x in args.atrs.split(",") if x.strip()}
    runs = build_runs(atr_filter)
    print(f"QQQ 2025 broad LazySwing sweep ({len(runs)} runs)\n", flush=True)

    results: list[dict] = []
    for i, (tag, params, meta) in enumerate(runs, start=1):
        run_dir = output_root / tag
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = Config(
            {
                "backtest": {
                    "name": f"LazySwing_QQQ_2025_{tag}",
                    "version": "qqq-broad-rvol",
                    "initial_cash": INITIAL_CASH,
                    "start_date": "2025-01-01",
                    "end_date": "2025-12-31",
                },
                "data_source": {
                    "type": "csv_file",
                    "parser": "binance_kline",
                    "params": {
                        "file_path": DATA_FILE,
                        "symbol": SYMBOL,
                    },
                },
                "strategies": [{"type": "lazy_swing", "params": params}],
            }
        )

        t0 = time.time()
        result = Controller(cfg, output_dir=str(run_dir)).run()[0]
        elapsed = time.time() - t0
        trade_log = TradeLogReader().read(result.trade_log_path)
        stats = compute_stats(trade_log, cfg.initial_cash, 0.05)
        wr, wins, losses, exited = exit_win_rate(trade_log)
        num_trades = int(stats["num_buys"]) + int(stats["num_shorts"])

        row = {
            "tag": tag,
            "total_return_pct": float(stats["total_return"]),
            "final_value": float(result.final_value),
            "sharpe": float(stats["sharpe_ratio"]),
            "max_dd_pct": float(stats["max_drawdown"]),
            "wr_pct": wr,
            "wins": wins,
            "losses": losses,
            "exits_with_pnl": exited,
            "num_trades": num_trades,
            "elapsed_sec": round(elapsed, 1),
            "trade_log_path": result.trade_log_path,
            **meta,
        }
        results.append(row)
        print(f"[{i:>3}/{len(runs)}] {fmt_row(row)}", flush=True)

    df = pd.DataFrame(results).sort_values("total_return_pct", ascending=False)
    df.to_csv(output_root / "summary.csv", index=False)

    top = df.head(30).copy()
    top.to_csv(output_root / "top30.csv", index=False)

    summary_rows = []
    for colset_name, cols in [
        ("st_area", ["atr", "mult"]),
        ("regime_area", ["adaptive_st_vol_period", "adaptive_st_enter_ratio_threshold", "adaptive_st_exit_ratio_threshold", "adaptive_st_min_high_bars"]),
        ("gate_area", ["mode", "low_ratio", "high_ratio", "low_stop", "high_stop", "power"]),
    ]:
        agg = (
            df.groupby(cols, dropna=False)
            .agg(
                runs=("tag", "count"),
                avg_return=("total_return_pct", "mean"),
                best_return=("total_return_pct", "max"),
                avg_sharpe=("sharpe", "mean"),
                avg_wr=("wr_pct", "mean"),
                avg_dd=("max_dd_pct", "mean"),
            )
            .reset_index()
            .sort_values("best_return", ascending=False)
        )
        agg["group"] = colset_name
        summary_rows.append(agg.head(20))
    pd.concat(summary_rows, ignore_index=True).to_csv(output_root / "area_summary.csv", index=False)

    print(f"\nSaved summary to {output_root/'summary.csv'}", flush=True)


if __name__ == "__main__":
    main()
