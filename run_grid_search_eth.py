"""Grid search: LazySwing ST params for ETH on 30m resample vs 1h baseline."""

import itertools
import json
import sys

import pandas as pd

sys.path.insert(0, "src")

from config import Config
from controller import Controller
from reporting.reporter import compute_stats


DATASETS = {
    "dev": {
        "start": "2023-08-31",
        "end": "2024-12-31",
    },
    "test": {
        "start": "2025-01-01",
        "end": "2025-12-31",
    },
    "live": {
        "start": "2026-01-01",
        "end": "2026-04-02",
    },
}

DATA_FILE = "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv"
SYMBOL = "ETH-PERP-INTX"

FIXED_PARAMS = {
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "exit_atr_fraction": 0.25,
    "reentry_atr_fraction": 0.75,
    "cost_per_trade_pct": 0.05,
}

# Grid: 30m resample with range of ST params
ST_ATR_PERIODS = [10, 14, 16, 20]
ST_MULTIPLIERS = [1.5, 2.0, 2.5]
RESAMPLE_INTERVALS = ["30min"]

# Also run 1h baseline for comparison
BASELINE_CONFIGS = [
    ("1h", 20, 2.0),  # current v1
    ("1h", 10, 1.5),  # BTC best
]


def run_single(dataset_key, atr_period, multiplier, resample_interval):
    ds = DATASETS[dataset_key]
    config_dict = {
        "backtest": {
            "name": f"eth_grid_{dataset_key}_{resample_interval}_atr{atr_period}_m{multiplier}",
            "version": "grid",
            "initial_cash": 100000,
            "start_date": ds["start"],
            "end_date": ds["end"],
        },
        "data_source": {
            "type": "csv_file",
            "parser": "binance_kline",
            "params": {"file_path": DATA_FILE, "symbol": SYMBOL},
        },
        "strategies": [{
            "type": "lazy_swing",
            "params": {
                "supertrend_atr_period": atr_period,
                "supertrend_multiplier": multiplier,
                "resample_interval": resample_interval,
                **FIXED_PARAMS,
            },
        }],
    }

    config = Config(config_dict)
    controller = Controller(config, output_dir="reports/grid_eth")
    results = controller.run()
    result = results[0]

    trade_log = pd.read_csv(result.trade_log_path, parse_dates=["date"])
    stats = compute_stats(trade_log, 100000, 0.05)

    exits = trade_log[trade_log["action"].isin(["SELL", "COVER"])].copy()
    if len(exits) > 0:
        exits["pnl_pct"] = exits["details"].apply(
            lambda d: json.loads(d).get("pnl_pct", 0)
        )
        win_rate = (exits["pnl_pct"] > 0).sum() / len(exits) * 100
        avg_pnl = exits["pnl_pct"].mean()
    else:
        win_rate = 0
        avg_pnl = 0

    return {
        "resample": resample_interval,
        "atr_period": atr_period,
        "multiplier": multiplier,
        "dataset": dataset_key,
        "return_pct": round(stats["total_return"], 1),
        "sharpe": round(stats["sharpe_ratio"], 2),
        "max_dd": round(stats["max_drawdown"], 2),
        "trades": len(exits),
        "win_rate": round(win_rate, 1),
        "avg_pnl": round(avg_pnl, 3),
    }


def run_grid(datasets, combos):
    total = len(combos) * len(datasets)
    results = []
    run_num = 0

    for resample, atr_period, multiplier in combos:
        for ds_key in datasets:
            run_num += 1
            sys.stdout.write(
                f"\r[{run_num}/{total}] {resample} atr={atr_period} mult={multiplier} {ds_key}...     "
            )
            sys.stdout.flush()
            try:
                r = run_single(ds_key, atr_period, multiplier, resample)
                results.append(r)
            except Exception as e:
                print(f"\n  ERROR: {e}")
                results.append({
                    "resample": resample, "atr_period": atr_period,
                    "multiplier": multiplier, "dataset": ds_key,
                    "return_pct": 0, "sharpe": 0, "max_dd": 0,
                    "trades": 0, "win_rate": 0, "avg_pnl": 0,
                    "error": str(e),
                })
    print()
    return results


def print_table(results, title=""):
    if title:
        print(f"\n{'=' * 110}")
        print(f"  {title}")
        print(f"{'=' * 110}")
    print(f"{'Config':>22s} {'Set':>5s} {'Return%':>10s} {'Sharpe':>8s} "
          f"{'MaxDD%':>8s} {'Trades':>7s} {'WR%':>6s} {'AvgPnL%':>9s}")
    print(f"{'-'*22:>22s} {'-'*5:>5s} {'-'*10:>10s} {'-'*8:>8s} "
          f"{'-'*8:>8s} {'-'*7:>7s} {'-'*6:>6s} {'-'*9:>9s}")
    for r in results:
        label = f"{r['resample']} ATR{r['atr_period']} M{r['multiplier']}"
        print(f"{label:>22s} {r['dataset']:>5s} {r['return_pct']:>+10.1f} "
              f"{r['sharpe']:>8.2f} {r['max_dd']:>8.2f} {r['trades']:>7d} "
              f"{r['win_rate']:>6.1f} {r['avg_pnl']:>+9.3f}")


def main():
    # Phase 1: Dev grid search (30m) + 1h baselines
    print("=" * 110)
    print("PHASE 1: DEV grid search — 30m resample + 1h baselines")
    print("=" * 110)

    grid_combos = [(r, a, m) for r in RESAMPLE_INTERVALS
                   for a, m in itertools.product(ST_ATR_PERIODS, ST_MULTIPLIERS)]
    baseline_combos = list(BASELINE_CONFIGS)

    all_combos = [(r, a, m) for r, a, m in baseline_combos] + grid_combos
    dev_results = run_grid(["dev"], all_combos)

    # Sort by return
    dev_results.sort(key=lambda r: r["return_pct"], reverse=True)
    print_table(dev_results, "DEV Results (sorted by return)")

    # Pick best 3 from 30m by return
    best_30m = [r for r in dev_results if r["resample"] == "30min"][:3]
    # Also include 1h baseline
    baselines = [r for r in dev_results if r["resample"] == "1h"]

    print(f"\n{'=' * 110}")
    print("TOP 3 (30m) + baselines (1h) — will run on test & live")
    print(f"{'=' * 110}")
    print_table(baselines + best_30m)

    # Phase 2: Run best 2 30m + baselines on test and live
    phase2_combos = [(r["resample"], r["atr_period"], r["multiplier"])
                     for r in best_30m[:2]] + baseline_combos
    # Deduplicate
    phase2_combos = list(dict.fromkeys(phase2_combos))

    print(f"\n\n{'=' * 110}")
    print("PHASE 2: TEST + LIVE — best 2 (30m) + baselines (1h)")
    print(f"{'=' * 110}")

    phase2_results = run_grid(["test", "live"], phase2_combos)

    for ds_key in ["test", "live"]:
        ds_results = [r for r in phase2_results if r["dataset"] == ds_key]
        ds_results.sort(key=lambda r: r["return_pct"], reverse=True)
        print_table(ds_results, f"{ds_key.upper()} Results")

    # Final comparison table
    print(f"\n\n{'#' * 110}")
    print("FINAL COMPARISON — all datasets")
    print(f"{'#' * 110}")
    all_results = dev_results + phase2_results
    for combo in phase2_combos:
        resample, atr, mult = combo
        label = f"{resample} ATR{atr} M{mult}"
        combo_results = [r for r in all_results
                         if r["resample"] == resample
                         and r["atr_period"] == atr
                         and r["multiplier"] == mult]
        combo_results.sort(key=lambda r: r["dataset"])
        if combo_results:
            print(f"\n  {label}:")
            for r in combo_results:
                print(f"    {r['dataset']:>5s}: return={r['return_pct']:+.1f}% "
                      f"sharpe={r['sharpe']} maxDD={r['max_dd']}% "
                      f"WR={r['win_rate']}% trades={r['trades']} "
                      f"avgPnL={r['avg_pnl']:+.3f}%")


if __name__ == "__main__":
    main()
