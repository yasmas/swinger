from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


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


@dataclass(frozen=True)
class PortfolioView:
    """Read-only snapshot of portfolio state passed to strategies."""
    cash: float
    position_qty: float = 0.0
    position_avg_cost: float = 0.0
    short_qty: float = 0.0
    short_avg_cost: float = 0.0


def portfolio_view_from(portfolio, symbol: str) -> PortfolioView:
    """Build a PortfolioView snapshot from a live Portfolio instance."""
    pos = portfolio.positions.get(symbol)
    short = portfolio.short_positions.get(symbol)
    return PortfolioView(
        cash=portfolio.cash,
        position_qty=pos.quantity if pos else 0.0,
        position_avg_cost=pos.avg_cost if pos else 0.0,
        short_qty=short.quantity if short else 0.0,
        short_avg_cost=short.avg_cost if short else 0.0,
    )


class StrategyBase(ABC):
    """Base class for all trading strategies.

    Strategies are pure signal generators: on_bar() reads the portfolio view
    and price data, then returns an Action.  It must NOT mutate any portfolio.
    """

    display_name: str = ""  # Human-readable name, override in subclasses
    min_warmup_hours: int = 0  # Minimum hours of data needed for indicator warmup

    def __init__(self, config: dict):
        self.config = config

    def prepare(self, full_data: pd.DataFrame) -> None:
        """Called once before the backtest loop with the full dataset.

        Override to precompute indicators. Default is no-op.
        """
        pass

    def reset_position(self) -> None:
        """Force-clear position tracking state (e.g. after data gap force-close).

        Override in subclasses that track entry price, bars held, etc. Default is no-op.
        """
        pass

    def save_state(self) -> dict:
        """Snapshot internal state that should be preserved across intra-bar calls.

        Override in subclasses that maintain bar-to-bar state (e.g. _prev_*
        indicators used for crossover detection).  Default is no-op.
        """
        return {}

    def restore_state(self, state: dict) -> None:
        """Restore a previously saved snapshot. Default is no-op."""
        pass

    def export_state(self) -> dict:
        """Serialize full mutable state for crash-recovery persistence.

        Returns a plain dict safe for YAML/JSON serialization.
        The paper trader calls this on every 5m bar and persists the result.
        Override in subclasses. Default returns empty dict.
        """
        return {}

    def import_state(self, state: dict) -> None:
        """Restore full mutable state from a previously exported dict.

        Called on paper trader startup to resume from where we left off.
        Override in subclasses. Default is no-op.
        """
        pass

    @abstractmethod
    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
        pv: PortfolioView,
    ) -> Action:
        """Called for each price bar. Must return an Action.

        Args:
            date: The current bar's timestamp.
            row: The current bar (open, high, low, close, volume).
            data_so_far: All bars up to and including the current one.
            is_last_bar: True if this is the final bar in the dataset.
            pv: Read-only snapshot of current portfolio state.

        Returns:
            Action indicating BUY, SELL, SHORT, COVER, or HOLD with quantity and details.
        """
        pass
