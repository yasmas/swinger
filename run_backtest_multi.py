"""Run a multi-asset SwingParty backtest from a YAML config."""

import sys
from pathlib import Path

import yaml

from multi_asset_controller import MultiAssetController
from reporting.swing_party_reporter import SwingPartyReporter


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_backtest_multi.py <config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    with open(config_path) as f:
        config = yaml.safe_load(f)

    backtest = config["backtest"]
    strategy = config["strategy"]

    print(f"Running multi-asset backtest: {backtest['name']}")
    print(f"  Period: {backtest['start_date']} to {backtest['end_date']}")
    print(f"  Initial cash: ${float(backtest['initial_cash']):,.2f}")
    print(f"  Max positions: {strategy.get('max_positions', 3)}")
    print(f"  Assets: {', '.join(strategy.get('assets', []))}")

    output_dir = "reports"
    controller = MultiAssetController(config, output_dir=output_dir)
    result = controller.run()

    print(f"\n  Final value: ${result.final_value:,.2f}")
    print(f"  Total return: {result.total_return_pct:+.2f}%")
    print(f"  Trade log: {result.trade_log_path}")
    if result.execution_errors:
        print(f"  Execution errors: {len(result.execution_errors)} (see logs)")
        for err in result.execution_errors[:8]:
            print(f"    - {err}")
        if len(result.execution_errors) > 8:
            print(f"    ... and {len(result.execution_errors) - 8} more")

    report_path = SwingPartyReporter(output_dir=output_dir).generate(
        trade_log_path=result.trade_log_path,
        config=config,
        strategy_name=result.strategy_name,
        version=backtest.get("version", ""),
    )
    print(f"  Report: {report_path}")


if __name__ == "__main__":
    main()
