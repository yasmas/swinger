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

    def _create_data_source(self, kind: str = "signal"):
        if kind == "execution" and self.config.has_execution_data_source:
            parser_type = self.config.execution_parser_type
            source_type = self.config.execution_data_source_type
            params = self.config.execution_data_source_params
        else:
            parser_type = self.config.parser_type
            source_type = self.config.data_source_type
            params = self.config.data_source_params

        parser_cls = PARSER_REGISTRY[parser_type]
        source_cls = DATA_SOURCE_REGISTRY[source_type]
        parser = parser_cls()
        return source_cls(parser, params)

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

    def _load_data(self, kind: str = "signal") -> pd.DataFrame:
        source = self._create_data_source(kind)
        symbol = self.config.execution_symbol if kind == "execution" else self.config.symbol
        return source.get_data(
            symbol,
            self._load_start_date(),
            self.config.end_date,
        )

    def _resolve_execution_row(
        self,
        execution_data: pd.DataFrame,
        signal_date: pd.Timestamp,
    ) -> tuple[pd.Timestamp | None, pd.Series | None, bool]:
        idx = execution_data.index.searchsorted(signal_date, side="left")
        if idx >= len(execution_data):
            return None, None, False
        execution_date = execution_data.index[idx]
        return execution_date, execution_data.iloc[idx], execution_date != signal_date

    def _resolve_mark_price(
        self,
        execution_data: pd.DataFrame,
        signal_date: pd.Timestamp,
    ) -> float:
        idx = execution_data.index.searchsorted(signal_date, side="right") - 1
        if idx >= 0:
            return float(execution_data.iloc[idx]["close"])
        idx = execution_data.index.searchsorted(signal_date, side="left")
        if idx < len(execution_data):
            return float(execution_data.iloc[idx]["close"])
        raise ValueError("Execution dataset is empty after loading")

    @staticmethod
    def _execution_quantity(
        action: Action,
        signal_price: float,
        execution_price: float,
    ) -> float:
        if (
            action.action not in (ActionType.BUY, ActionType.SHORT)
            or signal_price <= 0
            or execution_price <= 0
        ):
            return action.quantity
        return action.quantity * signal_price / execution_price

    def _keep_positions_on_data_gap(self) -> bool:
        if "keep_positions_on_data_gap" in self.config.backtest:
            return bool(self.config.backtest.get("keep_positions_on_data_gap", True))
        if "force_close_on_data_gap" in self.config.backtest:
            return not bool(self.config.backtest.get("force_close_on_data_gap", False))
        return True

    def run(self) -> list[BacktestResult]:
        signal_data = self._load_data("signal")
        if signal_data.empty:
            raise ValueError(
                f"No data found for {self.config.symbol} "
                f"between {self.config.start_date} and {self.config.end_date}"
            )
        if self.config.has_execution_data_source:
            execution_data = self._load_data("execution")
            if execution_data.empty:
                raise ValueError(
                    f"No execution data found for {self.config.execution_symbol} "
                    f"between {self.config.start_date} and {self.config.end_date}"
                )
        else:
            execution_data = signal_data

        results = []
        for strat_config in self.config.strategies:
            result = self._run_strategy(strat_config, signal_data, execution_data)
            results.append(result)

        return results

    def _run_strategy(
        self,
        strat_config: dict,
        signal_data: pd.DataFrame,
        execution_data: pd.DataFrame,
    ) -> BacktestResult:
        strat_type = strat_config["type"]
        strat_params = strat_config.get("params", {})
        strat_params["symbol"] = self.config.symbol
        signal_symbol = self.config.symbol
        execution_symbol = self.config.execution_symbol

        strat_cls = STRATEGY_REGISTRY[strat_type]
        portfolio = Portfolio(self.config.initial_cash)
        strategy = strat_cls(strat_params)
        executor = BacktestExecutor()
        strategy.prepare(signal_data)

        version = f"_{self.config.version}" if self.config.version else ""
        log_filename = f"{self.config.name}_{strat_type}{version}.csv".replace(" ", "_")
        log_path = self.output_dir / log_filename

        num_bars = len(signal_data)
        sim_start = pd.Timestamp(self.config.start_date).normalize()

        prev_date = None
        with TradeLogger(str(log_path)) as logger:
            for i, (date, row) in enumerate(signal_data.iterrows()):
                is_last_bar = i == num_bars - 1
                data_so_far = signal_data.iloc[: i + 1]
                signal_price = float(row["close"])
                execution_mark_price = self._resolve_mark_price(execution_data, date)

                if date < sim_start:
                    strategy.warmup_bar(date, row, data_so_far, is_last_bar)
                    prev_date = date
                    continue

                # Optionally force-close positions across large data gaps (>24h).
                # By default we carry positions through weekend / holiday breaks.
                if not self._keep_positions_on_data_gap() and prev_date is not None:
                    gap = (date - prev_date).total_seconds()
                    if gap > 86400:  # >24 hours
                        prev_price = self._resolve_mark_price(execution_data, prev_date)
                        if execution_symbol in portfolio.positions:
                            qty = portfolio.positions[execution_symbol].quantity
                            sell_action = Action(ActionType.SELL, qty, {"exit_reason": "data_gap"})
                            executor.execute(sell_action, execution_symbol, prev_price, portfolio)
                            logger.log(str(prev_date), "SELL", execution_symbol, qty, prev_price,
                                       portfolio.cash, portfolio.total_value({execution_symbol: prev_price}),
                                       sell_action.details,
                                       **_position_snapshot(portfolio, execution_symbol))
                            strategy.reset_position()
                        if execution_symbol in portfolio.short_positions:
                            qty = portfolio.short_positions[execution_symbol].quantity
                            cover_action = Action(ActionType.COVER, qty, {"exit_reason": "data_gap"})
                            executor.execute(cover_action, execution_symbol, prev_price, portfolio)
                            logger.log(str(prev_date), "COVER", execution_symbol, qty, prev_price,
                                       portfolio.cash, portfolio.total_value({execution_symbol: prev_price}),
                                       cover_action.details,
                                       **_position_snapshot(portfolio, execution_symbol))
                            strategy.reset_position()
                prev_date = date

                pv = portfolio_view_from(portfolio, execution_symbol)
                action = strategy.on_bar(date, row, data_so_far, is_last_bar, pv)
                details = dict(action.details or {})
                details.setdefault("signal_symbol", signal_symbol)
                details.setdefault("signal_price", signal_price)
                details.setdefault("execution_symbol", execution_symbol)
                details.setdefault("execution_price", execution_mark_price)
                details.setdefault("execution_timestamp", str(date))
                trade_price = execution_mark_price

                if action.action != ActionType.HOLD:
                    execution_date, execution_row, delayed = self._resolve_execution_row(execution_data, date)
                    if execution_row is None:
                        raise ValueError(
                            f"No execution bar available for {execution_symbol} at or after {date}"
                        )
                    trade_price = float(execution_row["close"])
                    details["execution_price"] = trade_price
                    details["execution_timestamp"] = str(execution_date)
                    if delayed:
                        details["execution_price_delayed"] = True
                    scaled_action = Action(
                        action.action,
                        self._execution_quantity(action, signal_price, trade_price),
                        details,
                    )
                    executor.execute(scaled_action, execution_symbol, trade_price, portfolio)

                portfolio_value = portfolio.total_value({execution_symbol: execution_mark_price})

                logger.log(
                    date=str(date),
                    action=action.action.value,
                    symbol=execution_symbol,
                    quantity=(
                        self._execution_quantity(action, signal_price, trade_price)
                        if action.action != ActionType.HOLD
                        else action.quantity
                    ),
                    price=trade_price,
                    cash_balance=portfolio.cash,
                    portfolio_value=portfolio_value,
                    details=details,
                    **_position_snapshot(portfolio, execution_symbol),
                )

        final_price = self._resolve_mark_price(execution_data, signal_data.index[-1])
        final_value = portfolio.total_value({execution_symbol: final_price})

        return BacktestResult(
            strategy_name=strat_type,
            symbol=execution_symbol,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            initial_cash=self.config.initial_cash,
            final_value=final_value,
            trade_log_path=str(log_path),
        )
