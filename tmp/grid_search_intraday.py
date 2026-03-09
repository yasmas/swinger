"""Grid search for intraday trend strategy parameters."""
import sys
import itertools
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import Config
from controller import Controller


def run_one(base_config_path: str, param_overrides: dict) -> dict:
    """Run a single backtest with parameter overrides, return summary stats."""
    config = Config.from_yaml(base_config_path)

    # Override params
    for key, val in param_overrides.items():
        config.strategies[0]["params"][key] = val

    controller = Controller(config, output_dir="/tmp/grid_search_out")

    try:
        results = controller.run()
    except Exception as e:
        return {"error": str(e), **param_overrides}

    result = results[0]

    # Load trade log for stats
    trade_log = pd.read_csv(result.trade_log_path)
    actions = trade_log[trade_log["action"].isin(["BUY", "SELL", "SHORT", "COVER"])]

    buys = (actions["action"] == "BUY").sum()
    shorts = (actions["action"] == "SHORT").sum()
    total_trades = buys + shorts

    # Gross return
    final_value = result.final_value
    total_return = result.total_return_pct

    # Transaction costs
    total_costs = (actions["price"] * actions["quantity"] * 0.05 / 100).sum()
    after_cost_value = final_value - total_costs
    after_cost_return = (after_cost_value / config.initial_cash - 1) * 100

    # Sharpe
    pv = trade_log.set_index(pd.to_datetime(trade_log["date"]))["portfolio_value"]
    daily = pv.resample("D").last().dropna()
    daily_ret = daily.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0

    # Max drawdown
    cummax = daily.cummax()
    dd = ((daily - cummax) / cummax * 100).min()

    # Win rate from trade pairs
    import json
    entries_idx = []
    exits_idx = []
    for i, row in trade_log.iterrows():
        if row["action"] in ("BUY", "SHORT"):
            entries_idx.append(i)
        elif row["action"] in ("SELL", "COVER") and entries_idx:
            entry_i = entries_idx.pop(0)
            exits_idx.append((entry_i, i))

    wins = 0
    for ei, xi in exits_idx:
        entry_row = trade_log.loc[ei]
        exit_row = trade_log.loc[xi]
        if entry_row["action"] == "BUY":
            if exit_row["price"] > entry_row["price"]:
                wins += 1
        else:
            if exit_row["price"] < entry_row["price"]:
                wins += 1
    win_rate = wins / len(exits_idx) * 100 if exits_idx else 0

    days = (pd.to_datetime(trade_log["date"].iloc[-1]) - pd.to_datetime(trade_log["date"].iloc[0])).days

    return {
        **param_overrides,
        "total_return": round(total_return, 2),
        "after_cost_return": round(after_cost_return, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(dd, 2),
        "total_trades": total_trades,
        "trades_per_day": round(total_trades / days, 2) if days > 0 else 0,
        "win_rate": round(win_rate, 1),
        "total_costs": round(total_costs, 0),
    }


def main():
    base_config = "config/intraday_trend_dev.yaml"

    # Focused grid on key parameters
    grid = {
        "supertrend_multiplier": [2.0, 3.0, 4.0],
        "keltner_atr_multiplier": [1.5, 2.0, 2.5],
        "adx_threshold": [20, 25, 30],
        "volume_confirm_multiplier": [1.0, 1.5, 2.0],
    }

    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    print(f"Running {len(combos)} parameter combinations...")

    results = []
    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        print(f"  [{i+1}/{len(combos)}] {params}", end="", flush=True)
        r = run_one(base_config, params)
        print(f"  → return={r.get('after_cost_return', 'ERR')}%  sharpe={r.get('sharpe', 'ERR')}")
        results.append(r)

    df = pd.DataFrame(results)
    df = df.sort_values("after_cost_return", ascending=False)

    print("\n" + "=" * 100)
    print("TOP 10 BY AFTER-COST RETURN:")
    print("=" * 100)
    cols = keys + ["total_return", "after_cost_return", "sharpe", "max_drawdown", "total_trades", "trades_per_day", "win_rate"]
    print(df[cols].head(10).to_string(index=False))

    print("\n\nTOP 10 BY SHARPE:")
    print("=" * 100)
    print(df.sort_values("sharpe", ascending=False)[cols].head(10).to_string(index=False))

    # Save full results
    df.to_csv("reports/grid_search_intraday_v1.csv", index=False)
    print(f"\nFull results saved to reports/grid_search_intraday_v1.csv")


if __name__ == "__main__":
    main()
