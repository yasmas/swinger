"""Grid search: ATR(S)/ATR(L) > threshold volatility ratio entry filter.

Tests whether requiring short-term ATR to exceed long-term ATR by a threshold
before entering a position improves win rate and/or returns on LazySwing v5.

Logic:
  - Exit: unchanged (ST flip → exit immediately)
  - Entry: only enter when ATR(S)/ATR(L) > threshold. If filter fails at flip,
    wait on subsequent hourly closes until it passes or ST flips back.

Grid (based on ST ATR period N=10 for v5):
  short_period: [2, 3, 4]         (N/5, N/3, N/2.5)
  long_period:  [30, 40, 50]      (N*3, N*4, N*5)
  threshold:    [1.0, 1.05, 1.1, 1.15, 1.2, 1.3]

Runs on BTC dev set (2022-2024) and test set. Saves CSV + prints summary table.
"""

import sys
import json
import itertools
import pandas as pd

sys.path.insert(0, "src")

from config import Config
from controller import Controller

# ── Config ────────────────────────────────────────────────────────────

BASE_PARAMS = {
    "supertrend_atr_period": 10,
    "supertrend_multiplier": 1.5,
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
    # no resample_interval → default 1h
}

DATASETS = {
    "dev": {
        "file": "data/BTCUSDT-5m-2022-2024-combined.csv",
        "start": "2022-01-01",
        "end":   "2024-12-31",
    },
    "test": {
        "file": "data/BTCUSDT-5m-test-combined.csv",
        "start": "2020-01-01",
        "end":   "2026-01-31",
    },
}

SHORT_PERIODS = [2, 3, 4]
LONG_PERIODS  = [30, 40, 50]
THRESHOLDS    = [1.0, 1.05, 1.1, 1.15, 1.2, 1.3]

OUTPUT_CSV = "tmp/vol_filter_grid_results.csv"


# ── Helpers ───────────────────────────────────────────────────────────

def make_config(name, dataset_key, extra_params):
    ds = DATASETS[dataset_key]
    return Config({
        "backtest": {
            "name": name,
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
                "symbol": "BTCUSDT",
            },
        },
        "strategies": [{
            "type": "lazy_swing",
            "params": {**BASE_PARAMS, **extra_params},
        }],
    })


def run_one(config):
    controller = Controller(config, output_dir="tmp/vol_filter_grid")
    results = controller.run()
    return results[0]


def compute_stats(result):
    """Return (trades, win_rate_pct, avg_pnl_pct) from trade log."""
    df = pd.read_csv(result.trade_log_path)
    closes = df[df["action"].isin(["SELL", "COVER"])]
    total = len(closes)
    if total == 0:
        return 0, 0.0, 0.0
    pnls = closes["details"].apply(lambda x: json.loads(x).get("pnl_pct", 0))
    win_rate = (pnls > 0).sum() / total * 100
    avg_pnl = pnls.mean()
    return total, round(win_rate, 1), round(avg_pnl, 3)


# ── Grid search ───────────────────────────────────────────────────────

def main():
    import os
    os.makedirs("tmp/vol_filter_grid", exist_ok=True)

    rows = []

    # Baseline: no vol filter
    for ds_key in DATASETS:
        print(f"[baseline] {ds_key} ...", flush=True)
        cfg = make_config(f"baseline_{ds_key}", ds_key, {})
        result = run_one(cfg)
        trades, wr, avg_pnl = compute_stats(result)
        rows.append({
            "dataset": ds_key,
            "short": 0,
            "long": 0,
            "threshold": 0.0,
            "return_pct": round(result.total_return_pct, 2),
            "trades": trades,
            "win_rate": wr,
            "avg_pnl": avg_pnl,
        })
        print(f"  → return={result.total_return_pct:+.0f}%  trades={trades}  WR={wr:.1f}%")

    # Grid
    combos = list(itertools.product(SHORT_PERIODS, LONG_PERIODS, THRESHOLDS, DATASETS.keys()))
    total = len(combos)
    for i, (s, l, thresh, ds_key) in enumerate(combos, 1):
        label = f"S{s}_L{l}_T{thresh}_{ds_key}"
        print(f"[{i}/{total}] {label} ...", flush=True)
        extra = {
            "vol_filter_short": s,
            "vol_filter_long": l,
            "vol_filter_threshold": thresh,
        }
        cfg = make_config(label, ds_key, extra)
        result = run_one(cfg)
        trades, wr, avg_pnl = compute_stats(result)
        rows.append({
            "dataset": ds_key,
            "short": s,
            "long": l,
            "threshold": thresh,
            "return_pct": round(result.total_return_pct, 2),
            "trades": trades,
            "win_rate": wr,
            "avg_pnl": avg_pnl,
        })
        print(f"  → return={result.total_return_pct:+.0f}%  trades={trades}  WR={wr:.1f}%")

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nResults saved to {OUTPUT_CSV}")

    # Summary table
    print("\n=== DEV RESULTS (sorted by return) ===")
    dev = df[df["dataset"] == "dev"].sort_values("return_pct", ascending=False)
    print(dev.to_string(index=False))

    print("\n=== TEST RESULTS (sorted by return) ===")
    test = df[df["dataset"] == "test"].sort_values("return_pct", ascending=False)
    print(test.to_string(index=False))

    # Also show top 10 by WR on dev
    print("\n=== DEV TOP 10 BY WIN RATE ===")
    print(dev.sort_values("win_rate", ascending=False).head(10).to_string(index=False))


if __name__ == "__main__":
    main()
