"""Grid search trail_stop_cooldown_bars for ETH 2026 trail stop experiment.

Baseline (no trail stop) is included as cooldown=None.
Results saved to data/backtests/eth/trail_stop_grid/summary.csv.

Run:
    source .venv/bin/activate
    PYTHONPATH=src python3 scripts/grid_search_eth_2026_trail_stop.py
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd
from config import Config
from controller import Controller

OUTPUT_DIR = Path("data/backtests/eth/trail_stop_grid")
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

COOLDOWN_VALUES = [None, 0, 1, 2, 3, 4, 6, 8, 12]
TRAIL_STOP_PCT = 1.0
REENTRY_PCT = 0.5
MIN_GAIN_PCT = 2.0


def build_config(cooldown):
    params = {**BASE_PARAMS}
    label = "baseline"
    if cooldown is not None:
        params["trail_stop_pct"] = TRAIL_STOP_PCT
        params["trail_stop_cooldown_bars"] = cooldown
        params["trail_stop_reentry_pct"] = REENTRY_PCT
        params["trail_stop_min_gain_pct"] = MIN_GAIN_PCT
        label = f"trail_cd{cooldown}"
    config_dict = {
        "backtest": {**BACKTEST_BASE, "name": f"ETH_2026_{label}", "version": label},
        "data_source": {
            "type": "csv_file",
            "parser": "coinbase_intx_kline",
            "params": {
                "file_path": "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2026.csv",
                "symbol": "ETH-PERP-INTX",
            },
        },
        "strategies": [{"type": "lazy_swing", "params": params}],
    }
    return Config(config_dict), label


def _trade_stats(trade_log_path: str, initial_cash: float):
    df = pd.read_csv(trade_log_path)
    if df.empty:
        return 0, 0.0, 0, 0

    df["date"] = pd.to_datetime(df["date"])

    # Parse details JSON for exit/entry reasons
    trail_exits = 0
    reentries = 0
    for raw in df["details"]:
        try:
            d = json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            d = {}
        if d.get("exit_reason") == "trail_stop":
            trail_exits += 1
        if d.get("entry_reason") == "trail_stop_reentry":
            reentries += 1

    # Round-trip win rate: pair each BUY→SELL and SHORT→COVER
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
    return total_rt, wr, trail_exits, reentries


def run_one(cooldown):
    config, label = build_config(cooldown)
    try:
        ctrl = Controller(config, output_dir=str(OUTPUT_DIR))
        results = ctrl.run()
        result = results[0]
    except Exception as e:
        print(f"  ERROR {label}: {e}")
        return None

    n_trades, wr, trail_exits, reentries = _trade_stats(result.trade_log_path, config.initial_cash)

    ret = result.total_return_pct
    print(
        f"  {label:<16}  ret={ret:+.1f}%  trades={n_trades}  WR={wr:.0f}%"
        f"  trail_exits={trail_exits}  reentries={reentries}"
        f"  final=${result.final_value:,.0f}"
    )
    return {
        "label": label,
        "cooldown_bars": cooldown if cooldown is not None else "",
        "return_pct": round(ret, 2),
        "final_value": round(result.final_value, 2),
        "n_trades": n_trades,
        "win_rate_pct": round(wr, 1),
        "trail_exits": trail_exits,
        "reentries": reentries,
        "trade_log": result.trade_log_path,
    }


def main():
    print("ETH 2026 Trail Stop Grid Search")
    print(f"trail_stop_pct={TRAIL_STOP_PCT}%  min_gain={MIN_GAIN_PCT}%  reentry_pct={REENTRY_PCT}%")
    print(f"Cooldown values: {COOLDOWN_VALUES}\n")

    rows = []
    for cd in COOLDOWN_VALUES:
        row = run_one(cd)
        if row:
            rows.append(row)

    summary_path = OUTPUT_DIR / "summary.csv"
    if rows:
        keys = list(rows[0].keys())
        with open(summary_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nSummary → {summary_path}")

        df = pd.DataFrame(rows).drop(columns=["trade_log"])
        print("\n" + df.to_string(index=False))


if __name__ == "__main__":
    main()
