from pathlib import Path
from typing import Optional

import pandas as pd

from config import Config
from execution.backtest_executor import BacktestExecutor
from portfolio import Portfolio
from trade_log import TradeLogger
from data_sources.registry import DATA_SOURCE_REGISTRY, PARSER_REGISTRY
from strategies.registry import STRATEGY_REGISTRY
from strategies.base import Action, ActionType, portfolio_view_from


def _position_snapshot(portfolio, symbol: str) -> dict:
    """Extract position_qty/avg_cost/short_qty/short_avg_cost from a Portfolio."""
    pos = portfolio.positions.get(symbol)
    short = portfolio.short_positions.get(symbol)
    return {
        "position_qty": pos.quantity if pos else 0.0,
        "position_avg_cost": pos.avg_cost if pos else 0.0,
        "short_qty": short.quantity if short else 0.0,
        "short_avg_cost": short.avg_cost if short else 0.0,
    }


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

    def _strategy_min_warmup_hours(self) -> int:
        max_hours = 0
        for strat_config in self.config.strategies:
            strat_type = strat_config["type"]
            strat_params = {**strat_config.get("params", {}), "symbol": self.config.symbol}
            strat_cls = STRATEGY_REGISTRY[strat_type]
            strat = strat_cls(strat_params)
            max_hours = max(max_hours, int(getattr(strat, "min_warmup_hours", 0)))
        return max_hours

    def _load_start_date(self) -> str:
        configured = float(self.config.backtest.get("data_warmup_hours", 0) or 0)
        warmup_hours = max(configured, float(self._strategy_min_warmup_hours()))
        if warmup_hours <= 0:
            return self.config.start_date

        t0 = pd.Timestamp(self.config.start_date).normalize() - pd.Timedelta(hours=warmup_hours)
        # Date-only sources load from midnight, so include an extra day buffer.
        return (t0 - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    def _load_data(self) -> pd.DataFrame:
        source = self._create_data_source()
        return source.get_data(
            self.config.symbol,
            self._load_start_date(),
            self.config.end_date,
        )

    def _keep_positions_on_data_gap(self) -> bool:
        if "keep_positions_on_data_gap" in self.config.backtest:
            return bool(self.config.backtest.get("keep_positions_on_data_gap", True))
        if "force_close_on_data_gap" in self.config.backtest:
            return not bool(self.config.backtest.get("force_close_on_data_gap", False))
        return True

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
        sim_start = pd.Timestamp(self.config.start_date).normalize()

        prev_date = None
        with TradeLogger(str(log_path)) as logger:
            for i, (date, row) in enumerate(data.iterrows()):
                is_last_bar = i == num_bars - 1
                data_so_far = data.iloc[: i + 1]
                price = row["close"]

                if date < sim_start:
                    strategy.warmup_bar(date, row, data_so_far, is_last_bar)
                    prev_date = date
                    continue

                # Optionally force-close positions across large data gaps (>24h).
                # By default we carry positions through weekend / holiday breaks.
                if not self._keep_positions_on_data_gap() and prev_date is not None:
                    gap = (date - prev_date).total_seconds()
                    if gap > 86400:  # >24 hours
                        prev_price = data.iloc[i - 1]["close"]
                        if symbol in portfolio.positions:
                            qty = portfolio.positions[symbol].quantity
                            sell_action = Action(ActionType.SELL, qty, {"exit_reason": "data_gap"})
                            executor.execute(sell_action, symbol, prev_price, portfolio)
                            logger.log(str(prev_date), "SELL", symbol, qty, prev_price,
                                       portfolio.cash, portfolio.total_value({symbol: prev_price}),
                                       sell_action.details,
                                       **_position_snapshot(portfolio, symbol))
                            strategy.reset_position()
                        if symbol in portfolio.short_positions:
                            qty = portfolio.short_positions[symbol].quantity
                            cover_action = Action(ActionType.COVER, qty, {"exit_reason": "data_gap"})
                            executor.execute(cover_action, symbol, prev_price, portfolio)
                            logger.log(str(prev_date), "COVER", symbol, qty, prev_price,
                                       portfolio.cash, portfolio.total_value({symbol: prev_price}),
                                       cover_action.details,
                                       **_position_snapshot(portfolio, symbol))
                            strategy.reset_position()
                prev_date = date

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
                    **_position_snapshot(portfolio, symbol),
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
