"""Grid search regime-adaptive RVOL fast-exit threshold for ETH 2026.

Tests pairs (low_min, high_min) × cooldown alongside baseline and trail_cd8.
Results saved to data/backtests/eth/fast_exit_grid/summary_2026_regime_rvol.csv.

Run:
    source .venv/bin/activate
    PYTHONPATH=src python3 scripts/grid_search_eth_2026_regime_rvol.py
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd
from config import Config
from controller import Controller

OUTPUT_DIR = Path("data/backtests/eth/fast_exit_grid")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_PARAMS = dict(
    resample_interval="30min",
    supertrend_atr_period=25,
    supertrend_multiplier=1.75,
    adaptive_st_vol_period=24,
    adaptive_st_vol_long_period=336,
    adaptive_st_enter_ratio_threshold=1.0,
    adaptive_st_exit_ratio_threshold=0.85,
    adaptive_st_min_high_bars=48,
    flip_vol_ratio_enabled=True,
    flip_vol_ratio_short_period=4,
    flip_vol_ratio_long_period=336,
    flip_vol_ratio_regime_mode="squared",
    flip_vol_ratio_regime_low_min=0.7,
    flip_vol_ratio_regime_high_min=1.0,
    flip_vol_ratio_regime_low_stop_pct=1.0,
    flip_vol_ratio_regime_high_stop_pct=2.5,
    flip_vol_ratio_regime_power=1.5,
    hmacd_fast=24,
    hmacd_slow=51,
    hmacd_signal=12,
    cost_per_trade_pct=0.05,
)

BACKTEST_BASE = dict(
    initial_cash=100_000,
    start_date="2026-01-01",
    end_date="2026-04-27",
)

DATA_FILE = "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv"

# Regime-adaptive RVOL pairs: (low_min, high_min)
REGIME_PAIRS = [
    (0.7, 1.0),
    (0.8, 1.0),
    (0.8, 1.1),
    (0.9, 1.1),
    (0.9, 1.2),
    (1.0, 1.2),
]
COOLDOWNS = [2, 4, 8]


def build_config(label, extra_params):
    params = {**BASE_PARAMS, **extra_params}
    config_dict = {
        "backtest": {**BACKTEST_BASE, "name": f"ETH_2026_{label}", "version": label},
        "data_source": {
            "type": "csv_file",
            "parser": "coinbase_intx_kline",
            "params": {"file_path": DATA_FILE, "symbol": "ETH-PERP-INTX"},
        },
        "strategies": [{"type": "lazy_swing", "params": params}],
    }
    return Config(config_dict)


def _trade_stats(trade_log_path):
    df = pd.read_csv(trade_log_path)
    if df.empty:
        return 0, 0.0, 0, 0

    fast_exits = 0
    reentries = 0
    for raw in df["details"]:
        try:
            d = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            d = {}
        if d.get("exit_reason") == "fast_exit":
            fast_exits += 1
        if d.get("entry_reason") == "fast_exit_reentry":
            reentries += 1

    wins = 0
    total_rt = 0
    entries = []
    for _, row in df.iterrows():
        act = row["action"]
        if act in ("BUY", "SHORT"):
            entries.append((act, float(row["price"])))
        elif act == "SELL" and entries:
            entry_act, entry_price = entries.pop()
            if entry_act == "BUY":
                total_rt += 1
                if float(row["price"]) > entry_price:
                    wins += 1
        elif act == "COVER" and entries:
            entry_act, entry_price = entries.pop()
            if entry_act == "SHORT":
                total_rt += 1
                if float(row["price"]) < entry_price:
                    wins += 1

    wr = wins / total_rt * 100 if total_rt > 0 else 0.0
    return total_rt, wr, fast_exits, reentries


def run_one(label, extra_params):
    config = build_config(label, extra_params)
    try:
        ctrl = Controller(config, output_dir=str(OUTPUT_DIR))
        results = ctrl.run()
        result = results[0]
    except Exception as e:
        print(f"  ERROR {label}: {e}", flush=True)
        return None

    n_trades, wr, fast_exits, reentries = _trade_stats(result.trade_log_path)
    ret = result.total_return_pct
    print(
        f"  {label:<20}  ret={ret:+.1f}%  trades={n_trades}  WR={wr:.0f}%"
        f"  fe={fast_exits}  re={reentries}  final=${result.final_value:,.0f}",
        flush=True,
    )
    return {
        "label": label,
        "return_pct": round(ret, 2),
        "final_value": round(result.final_value, 2),
        "n_trades": n_trades,
        "win_rate_pct": round(wr, 1),
        "fast_exits": fast_exits,
        "reentries": reentries,
    }


def main():
    print("ETH 2026 Regime-Adaptive RVOL Fast-Exit Grid Search", flush=True)
    print(f"Pairs: {REGIME_PAIRS}  Cooldowns: {COOLDOWNS}\n", flush=True)

    rows = []

    # baseline
    row = run_one("baseline", {})
    if row:
        rows.append(row)

    # trail_cd8 benchmark
    row = run_one("trail_cd8", {
        "trail_stop_pct": 1.0,
        "trail_stop_cooldown_bars": 8,
        "trail_stop_reentry_pct": 0.5,
        "trail_stop_min_gain_pct": 2.0,
    })
    if row:
        rows.append(row)

    # regime-adaptive RVOL grid
    for low_min, high_min in REGIME_PAIRS:
        for cd in COOLDOWNS:
            label = f"rg{low_min:.1f}_{high_min:.1f}_cd{cd}"
            extra = {
                "fast_exit_enabled": True,
                "fast_exit_cooldown_bars": cd,
                "fast_exit_rvol_short_period": 24,
                "fast_exit_rvol_long_period": 2016,
                "fast_exit_rvol_low_min": low_min,
                "fast_exit_rvol_high_min": high_min,
            }
            row = run_one(label, extra)
            if row:
                rows.append(row)

    summary_path = OUTPUT_DIR / "summary_2026_regime_rvol.csv"
    if rows:
        keys = list(rows[0].keys())
        with open(summary_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n  → {summary_path}", flush=True)
        df = pd.DataFrame(rows)
        print("\n" + df.to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
