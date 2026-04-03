"""Grid search: LazySwing on 15m resampled bars over BIP-20DEC30-CDE data.

Tests combinations of ST ATR period and multiplier on both test and live datasets.
Compares to baseline 1h results.
"""

import itertools
import json
import math
import sys

import pandas as pd

sys.path.insert(0, "src")

from config import Config
from controller import Controller
from reporting.reporter import compute_stats


# ── Baseline results (1h, from prior run) ─────────────────────────────────
BASELINE = {
    "test": {"return": 533.7, "win_rate": 72.0, "trades": 161},
    "live": {"return": 306.0, "win_rate": 66.7, "trades": 81},
}

# ── Grid ──────────────────────────────────────────────────────────────────
ST_ATR_PERIODS = [3, 5, 7, 10, 14]
ST_MULTIPLIERS = [0.75, 1.0, 1.25, 1.5, 2.0]
RESAMPLE_INTERVALS = ["15min", "10min"]

DATASETS = {
    "test": {
        "file": "data/BIP-20DEC30-CDE-5m-2025.csv",
        "start": "2025-07-20",
        "end": "2025-12-31",
    },
    "live": {
        "file": "data/BIP-20DEC30-CDE-5m-2026.csv",
        "start": "2026-01-01",
        "end": "2026-04-02",
    },
}

FIXED_PARAMS = {
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "exit_atr_fraction": 0.25,
    "reentry_atr_fraction": 0.75,
    "cost_per_trade_pct": 0.05,
}


def run_single(dataset_key, atr_period, multiplier, resample_interval):
    ds = DATASETS[dataset_key]
    config_dict = {
        "backtest": {
            "name": f"grid_{dataset_key}_{resample_interval}_atr{atr_period}_m{multiplier}",
            "version": "grid",
            "initial_cash": 100000,
            "start_date": ds["start"],
            "end_date": ds["end"],
        },
        "data_source": {
            "type": "csv_file",
            "parser": "binance_kline",
            "params": {
                "file_path": ds["file"],
                "symbol": "BIP-20DEC30-CDE",
            },
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
    controller = Controller(config, output_dir=f"reports/grid_{resample_interval}")
    results = controller.run()
    result = results[0]

    # Compute stats
    trade_log = pd.read_csv(result.trade_log_path, parse_dates=["date"])
    stats = compute_stats(trade_log, 100000, 0.05)

    # Win rate from exits
    exits = trade_log[trade_log["action"].isin(["SELL", "COVER"])].copy()
    if len(exits) > 0:
        exits["pnl_pct"] = exits["details"].apply(
            lambda d: json.loads(d).get("pnl_pct", 0)
        )
        win_rate = (exits["pnl_pct"] > 0).sum() / len(exits) * 100
    else:
        win_rate = 0

    return {
        "atr_period": atr_period,
        "multiplier": multiplier,
        "resample": resample_interval,
        "dataset": dataset_key,
        "return_pct": round(stats["total_return"], 1),
        "sharpe": round(stats["sharpe_ratio"], 2),
        "max_dd": round(stats["max_drawdown"], 2),
        "trades": len(exits),
        "win_rate": round(win_rate, 1),
    }


def main():
    combos = list(itertools.product(ST_ATR_PERIODS, ST_MULTIPLIERS, RESAMPLE_INTERVALS))
    total = len(combos) * len(DATASETS)
    print(f"Grid search: {len(combos)} param combos × {len(DATASETS)} datasets = {total} runs")
    print(f"Resample: {RESAMPLE_INTERVALS} | ATR periods: {ST_ATR_PERIODS} | Multipliers: {ST_MULTIPLIERS}")
    print("=" * 100)

    all_results = []
    run_num = 0

    for atr_period, multiplier, resample in combos:
        for ds_key in DATASETS:
            run_num += 1
            sys.stdout.write(
                f"\r[{run_num}/{total}] {resample} atr={atr_period} mult={multiplier} {ds_key}...     "
            )
            sys.stdout.flush()
            try:
                r = run_single(ds_key, atr_period, multiplier, resample)
                all_results.append(r)
            except Exception as e:
                print(f"\n  ERROR: {e}")
                all_results.append({
                    "atr_period": atr_period,
                    "multiplier": multiplier,
                    "resample": resample,
                    "dataset": ds_key,
                    "return_pct": 0, "sharpe": 0, "max_dd": 0,
                    "trades": 0, "win_rate": 0, "error": str(e),
                })

    print("\n")

    # ── Results table per resample interval ───────────────────────────
    for resample in RESAMPLE_INTERVALS:
        print(f"\n{'#' * 100}")
        print(f"  RESAMPLE: {resample}")
        print(f"{'#' * 100}")

        for ds_key in DATASETS:
            ds_results = [r for r in all_results if r["dataset"] == ds_key and r["resample"] == resample]
            ds_results.sort(key=lambda r: r["return_pct"], reverse=True)

            bl = BASELINE[ds_key]
            print(f"\n{'=' * 100}")
            print(f"  {resample} | {ds_key.upper()} — Baseline (1h): return={bl['return']}%, "
                  f"WR={bl['win_rate']}%, trades={bl['trades']}")
            print(f"{'=' * 100}")
            print(f"{'ATR':>5s} {'Mult':>6s} {'Return%':>10s} {'Sharpe':>8s} "
                  f"{'MaxDD%':>8s} {'Trades':>7s} {'WinRate%':>9s}")
            print(f"{'-' * 5:>5s} {'-' * 6:>6s} {'-' * 10:>10s} {'-' * 8:>8s} "
                  f"{'-' * 8:>8s} {'-' * 7:>7s} {'-' * 9:>9s}")

            for r in ds_results:
                marker = " ★" if r["return_pct"] > bl["return"] else ""
                print(f"{r['atr_period']:>5d} {r['multiplier']:>6.2f} {r['return_pct']:>+10.1f} "
                      f"{r['sharpe']:>8.2f} {r['max_dd']:>8.2f} {r['trades']:>7d} "
                      f"{r['win_rate']:>9.1f}{marker}")

    # ── Best overall across all resample intervals ────────────────────
    print(f"\n\n{'=' * 100}")
    print("BEST PER DATASET (by return, across all intervals)")
    print(f"{'=' * 100}")
    for ds_key in DATASETS:
        ds_results = [r for r in all_results if r["dataset"] == ds_key]
        best = max(ds_results, key=lambda r: r["return_pct"])
        bl = BASELINE[ds_key]
        print(f"  {ds_key:5s}: {best['resample']} ATR={best['atr_period']}, Mult={best['multiplier']:.2f} → "
              f"return={best['return_pct']:+.1f}% (baseline {bl['return']:+.1f}%), "
              f"sharpe={best['sharpe']}, maxDD={best['max_dd']}%, "
              f"WR={best['win_rate']}%, trades={best['trades']}")

    # ── Best combo consistent across both datasets ────────────────────
    print()
    print("BEST CONSISTENT (sum of returns across both datasets)")
    print("-" * 100)
    combo_scores = {}
    for r in all_results:
        key = (r["resample"], r["atr_period"], r["multiplier"])
        combo_scores.setdefault(key, 0)
        combo_scores[key] += r["return_pct"]

    sorted_combos = sorted(combo_scores.items(), key=lambda x: x[1], reverse=True)
    for (resample, atr, mult), score in sorted_combos[:10]:
        test_r = next(r for r in all_results if r["resample"] == resample and r["atr_period"] == atr and r["multiplier"] == mult and r["dataset"] == "test")
        live_r = next(r for r in all_results if r["resample"] == resample and r["atr_period"] == atr and r["multiplier"] == mult and r["dataset"] == "live")
        print(f"  {resample:5s} ATR={atr:2d} Mult={mult:.2f} → test={test_r['return_pct']:+.1f}% live={live_r['return_pct']:+.1f}% "
              f"| test_WR={test_r['win_rate']}% live_WR={live_r['win_rate']}% "
              f"| test_sharpe={test_r['sharpe']} live_sharpe={live_r['sharpe']} "
              f"| test_DD={test_r['max_dd']}% live_DD={live_r['max_dd']}%")


if __name__ == "__main__":
    main()
