"""Immediate trade executor for backtesting."""

from portfolio import Portfolio
from strategies.base import Action, ActionType


class BacktestExecutor:
    """Executes trade Actions on a portfolio immediately (backtest mode).

    In backtesting, there is no fulfillment delay — trades execute at the
    bar's close price as soon as the strategy signals them.
    """

    def execute(self, action: Action, symbol: str, price: float, portfolio: Portfolio):
        if action.action == ActionType.BUY:
            portfolio.buy(symbol, action.quantity, price)
        elif action.action == ActionType.SELL:
            portfolio.sell(symbol, action.quantity, price)
        elif action.action == ActionType.SHORT:
            portfolio.short_sell(symbol, action.quantity, price)
        elif action.action == ActionType.COVER:
            portfolio.cover(symbol, action.quantity, price)
