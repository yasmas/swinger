from pathlib import Path
from typing import Optional

import pandas as pd

from config import Config
from execution.backtest_executor import BacktestExecutor
from portfolio import Portfolio
from trade_log import TradeLogger
from data_sources.registry import DATA_SOURCE_REGISTRY, PARSER_REGISTRY
from strategies.registry import STRATEGY_REGISTRY
from strategies.base import ActionType, portfolio_view_from


class BacktestResult:
    """Summary of a single strategy's backtest run."""

    def __init__(
        self,
        strategy_name: str,
        symbol: str,
        start_date: str,
        end_date: str,
        initial_cash: float,
        final_value: float,
        trade_log_path: str,
    ):
        self.strategy_name = strategy_name
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.final_value = final_value
        self.trade_log_path = trade_log_path

    @property
    def total_return_pct(self) -> float:
        return (self.final_value / self.initial_cash - 1) * 100


class Controller:
    """Orchestrates a backtest: loads config, wires data source + strategies, runs simulation."""

    def __init__(self, config: Config, output_dir: str = "reports"):
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _create_data_source(self):
        parser_cls = PARSER_REGISTRY[self.config.parser_type]
        source_cls = DATA_SOURCE_REGISTRY[self.config.data_source_type]
        parser = parser_cls()
        return source_cls(parser, self.config.data_source_params)

    def _load_data(self) -> pd.DataFrame:
        source = self._create_data_source()
        return source.get_data(
            self.config.symbol,
            self.config.start_date,
            self.config.end_date,
        )

    def run(self) -> list[BacktestResult]:
        data = self._load_data()
        if data.empty:
            raise ValueError(
                f"No data found for {self.config.symbol} "
                f"between {self.config.start_date} and {self.config.end_date}"
            )

        results = []
        for strat_config in self.config.strategies:
            result = self._run_strategy(strat_config, data)
            results.append(result)

        return results

    def _run_strategy(self, strat_config: dict, data: pd.DataFrame) -> BacktestResult:
        strat_type = strat_config["type"]
        strat_params = strat_config.get("params", {})
        strat_params["symbol"] = self.config.symbol
        symbol = self.config.symbol

        strat_cls = STRATEGY_REGISTRY[strat_type]
        portfolio = Portfolio(self.config.initial_cash)
        strategy = strat_cls(strat_params)
        executor = BacktestExecutor()
        strategy.prepare(data)

        version = f"_{self.config.version}" if self.config.version else ""
        log_filename = f"{self.config.name}_{strat_type}{version}.csv".replace(" ", "_")
        log_path = self.output_dir / log_filename

        num_bars = len(data)

        with TradeLogger(str(log_path)) as logger:
            for i, (date, row) in enumerate(data.iterrows()):
                is_last_bar = i == num_bars - 1
                data_so_far = data.iloc[: i + 1]
                price = row["close"]

                pv = portfolio_view_from(portfolio, symbol)
                action = strategy.on_bar(date, row, data_so_far, is_last_bar, pv)

                if action.action != ActionType.HOLD:
                    executor.execute(action, symbol, price, portfolio)

                portfolio_value = portfolio.total_value({symbol: price})

                logger.log(
                    date=str(date),
                    action=action.action.value,
                    symbol=symbol,
                    quantity=action.quantity,
                    price=price,
                    cash_balance=portfolio.cash,
                    portfolio_value=portfolio_value,
                    details=action.details,
                )

        final_price = data.iloc[-1]["close"]
        final_value = portfolio.total_value({symbol: final_price})

        return BacktestResult(
            strategy_name=strat_type,
            symbol=self.config.symbol,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            initial_cash=self.config.initial_cash,
            final_value=final_value,
            trade_log_path=str(log_path),
        )
