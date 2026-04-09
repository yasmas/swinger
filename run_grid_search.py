"""Grid search over SwingParty scorer types and parameters.

Runs each scorer variant, collects eviction PnL metrics and total return,
outputs a comparison table.

Usage:
    source .venv/bin/activate
    PYTHONPATH=src python3 run_grid_search.py config/strategies/swing_party/dev.yaml
"""

import copy
import sys
import time

import yaml

from multi_asset_controller import MultiAssetController


# Grid: scorer_type -> list of param dicts to try
SCORER_GRID = {
    "volume_breakout": [
        {"short_window": 3, "long_window": 20},
        {"short_window": 5, "long_window": 50},
        {"short_window": 8, "long_window": 100},
    ],
    "momentum": [
        {"lookback_bars": 10},
        {"lookback_bars": 20},
        {"lookback_bars": 40},
    ],
    "vol_adj_momentum": [
        {"lookback_bars": 10, "atr_period": 14},
        {"lookback_bars": 20, "atr_period": 14},
        {"lookback_bars": 40, "atr_period": 14},
    ],
    "trend_strength": [
        {"st_atr_period": 7, "st_multiplier": 2.0},
        {"st_atr_period": 10, "st_multiplier": 2.0},
        {"st_atr_period": 14, "st_multiplier": 2.5},
    ],
    "relative_strength": [
        {"lookback_bars": 10},
        {"lookback_bars": 20},
        {"lookback_bars": 40},
    ],
}


def run_one(base_config: dict, scorer_type: str, scorer_params: dict) -> dict:
    """Run a single backtest with the given scorer config. Returns result dict."""
    config = copy.deepcopy(base_config)
    config["strategy"]["scorer"] = {
        "type": scorer_type,
        "params": scorer_params,
    }

    controller = MultiAssetController(config, output_dir="reports/grid_search")
    result = controller.run()

    ev = result.eviction_stats
    return {
        "scorer": scorer_type,
        "params": scorer_params,
        "total_return_pct": round(result.total_return_pct, 2),
        "final_value": round(result.final_value, 2),
        "n_evictions": ev.get("n_events", 0),
        "n_resolved": ev.get("n_resolved", 0),
        "n_correct": ev.get("n_correct", 0),
        "accuracy": ev.get("accuracy", 0),
        "entered_compound_pnl": ev.get("entered_compound_pnl", 0),
        "evicted_compound_pnl": ev.get("evicted_compound_pnl", 0),
        "net_compound_pnl": ev.get("net_compound_pnl", 0),
        "events": ev.get("events", []),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_grid_search.py <config.yaml>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        base_config = yaml.safe_load(f)

    print(f"SwingParty Grid Search")
    print(f"  Assets: {', '.join(base_config['strategy']['assets'])}")
    print(f"  Max positions: {base_config['strategy']['max_positions']}")
    print(f"  Period: {base_config['backtest']['start_date']} to {base_config['backtest']['end_date']}")
    print(f"  Scorer variants: {sum(len(v) for v in SCORER_GRID.values())}")
    print()

    results = []
    total = sum(len(v) for v in SCORER_GRID.values())
    idx = 0

    for scorer_type, param_list in SCORER_GRID.items():
        for params in param_list:
            idx += 1
            label = f"{scorer_type}({', '.join(f'{k}={v}' for k, v in params.items())})"
            print(f"[{idx}/{total}] {label} ...", end=" ", flush=True)

            t0 = time.time()
            try:
                result = run_one(base_config, scorer_type, params)
                elapsed = time.time() - t0
                print(f"done ({elapsed:.1f}s) | "
                      f"Return: {result['total_return_pct']:+,.0f}% | "
                      f"Evictions: {result['n_evictions']} | "
                      f"Net eviction PnL: {result['net_compound_pnl']:+.2f}%")
                results.append(result)
            except Exception as e:
                elapsed = time.time() - t0
                print(f"FAILED ({elapsed:.1f}s): {e}")

    # Print summary table
    print("\n" + "=" * 120)
    print(f"{'Scorer':<45} {'Return%':>12} {'Evictions':>10} {'Correct':>9} "
          f"{'Accuracy':>9} {'Entered%':>10} {'Evicted%':>10} {'Net PnL%':>10}")
    print("-" * 120)

    # Sort by net eviction PnL (primary metric)
    results.sort(key=lambda r: r["net_compound_pnl"], reverse=True)

    for r in results:
        label = f"{r['scorer']}({', '.join(f'{k}={v}' for k, v in r['params'].items())})"
        print(f"{label:<45} {r['total_return_pct']:>+12,.0f} {r['n_evictions']:>10} "
              f"{r['n_correct']:>9} {r['accuracy']:>8.1f}% "
              f"{r['entered_compound_pnl']:>+10.2f} {r['evicted_compound_pnl']:>+10.2f} "
              f"{r['net_compound_pnl']:>+10.2f}")

    print("=" * 120)

    # Print detailed eviction events for top scorer
    if results:
        best = results[0]
        print(f"\nBest scorer: {best['scorer']}({', '.join(f'{k}={v}' for k, v in best['params'].items())})")
        print(f"  Net eviction compound PnL: {best['net_compound_pnl']:+.2f}%")
        print(f"  Eviction accuracy: {best['n_correct']}/{best['n_resolved']} ({best['accuracy']:.1f}%)")
        if best["events"]:
            print(f"\n  Eviction details:")
            for ev in best["events"]:
                marker = "+" if ev["diff_pct"] > 0 else "-"
                print(f"    {ev['date']}: evicted {ev['evicted']} ({ev['evicted_ret_pct']:+.2f}%) "
                      f"-> entered {ev['entered']} ({ev['entered_ret_pct']:+.2f}%) "
                      f"[{marker}{abs(ev['diff_pct']):.2f}%]")


if __name__ == "__main__":
    main()
