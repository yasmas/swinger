from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import pandas as pd

from portfolio import Portfolio


class ActionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SHORT = "SHORT"
    COVER = "COVER"
    HOLD = "HOLD"


@dataclass
class Action:
    action: ActionType
    quantity: float = 0.0
    details: dict = field(default_factory=dict)


class StrategyBase(ABC):
    """Base class for all trading strategies."""

    def __init__(self, portfolio: Portfolio, config: dict):
        self.portfolio = portfolio
        self.config = config

    def prepare(self, full_data: pd.DataFrame) -> None:
        """Called once before the backtest loop with the full dataset.

        Override to precompute indicators. Default is no-op.
        """
        pass

    @abstractmethod
    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
    ) -> Action:
        """Called for each price bar. Must return an Action.

        Args:
            date: The current bar's timestamp.
            row: The current bar (open, high, low, close, volume).
            data_so_far: All bars up to and including the current one.
            is_last_bar: True if this is the final bar in the dataset.

        Returns:
            Action indicating BUY, SELL, or HOLD with quantity and details.
        """
        pass
