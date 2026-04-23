"""Run a backtest from a YAML config and generate an HTML report."""
import sys
from config import Config
from controller import Controller
from reporting.reporter import Reporter
from reporting.intraday_reporter import IntradayReporter
from reporting.lazy_swing_reporter import LazySwingReporter
from reporting.macd_vortex_adx_reporter import MACDVortexADXReporter
from reporting.swing_trend_reporter import SwingTrendReporter


_INTRADAY_STRATEGIES = {"intraday_trend"}
_LAZY_SWING_STRATEGIES = {"lazy_swing"}
_MACD_VORTEX_ADX_STRATEGIES = {"macd_vortex_adx"}
_SWING_TREND_STRATEGIES = {"swing_trend"}


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

    generic_reporter  = Reporter(output_dir="reports")
    intraday_reporter = IntradayReporter(output_dir="reports")
    lazy_swing_reporter = LazySwingReporter(output_dir="reports")
    macd_vortex_adx_reporter = MACDVortexADXReporter(output_dir="reports")
    swing_trend_reporter = SwingTrendReporter(output_dir="reports")

    for i, result in enumerate(results):
        print(f"\n  Strategy: {result.strategy_name}")
        print(f"  Final value: ${result.final_value:,.2f}")
        print(f"  Total return: {result.total_return_pct:+.2f}%")
        print(f"  Trade log: {result.trade_log_path}")

        # Pick reporter based on strategy type
        strat_cfg    = config.strategies[i]
        strat_type   = strat_cfg["type"]
        strat_params = strat_cfg.get("params", {})

        if strat_type in _LAZY_SWING_STRATEGIES:
            report_path = lazy_swing_reporter.generate(
                trade_log_path=result.trade_log_path,
                price_data=price_data,
                strategy_name=result.strategy_name,
                symbol=config.symbol,
                initial_cash=config.initial_cash,
                version=config.version,
                strategy_params=strat_params,
            )
        elif strat_type in _MACD_VORTEX_ADX_STRATEGIES:
            report_path = macd_vortex_adx_reporter.generate(
                trade_log_path=result.trade_log_path,
                price_data=price_data,
                strategy_name=result.strategy_name,
                symbol=config.symbol,
                initial_cash=config.initial_cash,
                version=config.version,
                strategy_params=strat_params,
            )
        elif strat_type in _SWING_TREND_STRATEGIES:
            report_path = swing_trend_reporter.generate(
                trade_log_path=result.trade_log_path,
                price_data=price_data,
                strategy_name=result.strategy_name,
                symbol=config.symbol,
                initial_cash=config.initial_cash,
                version=config.version,
                strategy_params=strat_params,
            )
        elif strat_type in _INTRADAY_STRATEGIES:
            report_path = intraday_reporter.generate(
                trade_log_path=result.trade_log_path,
                price_data=price_data,
                strategy_name=result.strategy_name,
                symbol=config.symbol,
                initial_cash=config.initial_cash,
                version=config.version,
                strategy_params=strat_params,
            )
        else:
            report_path = generic_reporter.generate(
                trade_log_path=result.trade_log_path,
                price_data=price_data,
                strategy_name=result.strategy_name,
                symbol=config.symbol,
                initial_cash=config.initial_cash,
                version=config.version,
            )

        print(f"  Report: {report_path}")


if __name__ == "__main__":
    main()
