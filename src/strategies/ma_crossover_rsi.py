import math

import pandas as pd

from .base import StrategyBase, Action, ActionType


def _compute_rsi(closes: pd.Series, period: int) -> float:
    """Compute RSI from a series of close prices."""
    if len(closes) < period + 1:
        return 50.0  # neutral when not enough data

    deltas = closes.diff().iloc[1:]
    recent = deltas.iloc[-period:]

    gains = recent.where(recent > 0, 0.0).mean()
    losses = (-recent.where(recent < 0, 0.0)).mean()

    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


class MaCrossoverRsiStrategy(StrategyBase):
    """Buy when short MA crosses above long MA and RSI confirms momentum.

    Params (via config):
        short_window: short moving average period (default 10)
        long_window:  long moving average period (default 50)
        rsi_period:   RSI lookback period (default 14)
        rsi_threshold: RSI threshold for buy confirmation (default 50)
    """

    def __init__(self, portfolio, config):
        super().__init__(portfolio, config)
        self.short_window = config.get("short_window", 10)
        self.long_window = config.get("long_window", 50)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_threshold = config.get("rsi_threshold", 50)
        self._prev_short_ma = None
        self._prev_long_ma = None

    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
    ) -> Action:
        symbol = self.config.get("symbol", "UNKNOWN")
        price = row["close"]
        closes = data_so_far["close"]

        short_ma = closes.iloc[-self.short_window:].mean() if len(closes) >= self.short_window else closes.mean()
        long_ma = closes.iloc[-self.long_window:].mean() if len(closes) >= self.long_window else closes.mean()
        rsi = _compute_rsi(closes, self.rsi_period)

        details = {
            "short_ma": round(short_ma, 2),
            "long_ma": round(long_ma, 2),
            "rsi": round(rsi, 2),
        }

        if is_last_bar and symbol in self.portfolio.positions:
            quantity = self.portfolio.positions[symbol].quantity
            self.portfolio.sell(symbol, quantity, price)
            details["reason"] = "Final bar - liquidate position"
            self._prev_short_ma = short_ma
            self._prev_long_ma = long_ma
            return Action(action=ActionType.SELL, quantity=quantity, details=details)

        action = self._evaluate_signal(symbol, price, short_ma, long_ma, rsi, details)

        self._prev_short_ma = short_ma
        self._prev_long_ma = long_ma

        return action

    def _evaluate_signal(
        self, symbol: str, price: float,
        short_ma: float, long_ma: float, rsi: float,
        details: dict,
    ) -> Action:
        has_position = symbol in self.portfolio.positions
        crossed_above = (
            self._prev_short_ma is not None
            and self._prev_short_ma <= self._prev_long_ma
            and short_ma > long_ma
        )
        crossed_below = (
            self._prev_short_ma is not None
            and self._prev_short_ma >= self._prev_long_ma
            and short_ma < long_ma
        )

        if not has_position and crossed_above and rsi > self.rsi_threshold:
            quantity = math.floor(self.portfolio.cash / price * 1e8) / 1e8
            if quantity > 0:
                self.portfolio.buy(symbol, quantity, price)
                details["reason"] = "Short MA crossed above long MA with RSI confirmation"
                details["crossover"] = "bullish"
                return Action(action=ActionType.BUY, quantity=quantity, details=details)

        if has_position and (crossed_below or rsi < (100 - self.rsi_threshold)):
            quantity = self.portfolio.positions[symbol].quantity
            self.portfolio.sell(symbol, quantity, price)
            reason_parts = []
            if crossed_below:
                reason_parts.append("Short MA crossed below long MA")
                details["crossover"] = "bearish"
            if rsi < (100 - self.rsi_threshold):
                reason_parts.append(f"RSI below {100 - self.rsi_threshold}")
            details["reason"] = " and ".join(reason_parts)
            return Action(action=ActionType.SELL, quantity=quantity, details=details)

        details["reason"] = "No signal"
        return Action(action=ActionType.HOLD, quantity=0, details=details)
