import math

import pandas as pd

from .base import StrategyBase, Action, ActionType


class BuyAndHoldStrategy(StrategyBase):
    """Simplest strategy: buy on the first bar, sell on the last bar, hold in between."""

    def __init__(self, portfolio, config):
        super().__init__(portfolio, config)
        self._bought = False

    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
    ) -> Action:
        symbol = self.config.get("symbol", "UNKNOWN")
        price = row["close"]

        if not self._bought:
            quantity = math.floor(self.portfolio.cash / price * 1e8) / 1e8
            if quantity > 0:
                self.portfolio.buy(symbol, quantity, price)
                self._bought = True
                return Action(
                    action=ActionType.BUY,
                    quantity=quantity,
                    details={"reason": "Initial buy - buy and hold"},
                )

        if is_last_bar and self._bought and symbol in self.portfolio.positions:
            quantity = self.portfolio.positions[symbol].quantity
            self.portfolio.sell(symbol, quantity, price)
            self._bought = False
            return Action(
                action=ActionType.SELL,
                quantity=quantity,
                details={"reason": "Final bar - liquidate position"},
            )

        return Action(
            action=ActionType.HOLD,
            quantity=0,
            details={"reason": "Holding position"},
        )
