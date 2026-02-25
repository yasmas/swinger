"""Run a backtest from a YAML config and generate an HTML report."""
import sys
from config import Config
from controller import Controller
from reporting.reporter import Reporter


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_backtest.py <config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    config = Config.from_yaml(config_path)

    print(f"Running backtest: {config.name}")
    print(f"  Symbol: {config.symbol}")
    print(f"  Period: {config.start_date} to {config.end_date}")
    print(f"  Initial cash: ${config.initial_cash:,.2f}")

    controller = Controller(config, output_dir="reports")
    results = controller.run()

    # Re-load the price data for the chart
    source = controller._create_data_source()
    price_data = source.get_data(config.symbol, config.start_date, config.end_date)

    reporter = Reporter(output_dir="reports")

    for result in results:
        print(f"\n  Strategy: {result.strategy_name}")
        print(f"  Final value: ${result.final_value:,.2f}")
        print(f"  Total return: {result.total_return_pct:+.2f}%")
        print(f"  Trade log: {result.trade_log_path}")

        report_path = reporter.generate(
            trade_log_path=result.trade_log_path,
            price_data=price_data,
            strategy_name=result.strategy_name,
            symbol=config.symbol,
            initial_cash=config.initial_cash,
        )
        print(f"  Report: {report_path}")


if __name__ == "__main__":
    main()
