import math

import pandas as pd
import pytest

from portfolio import Portfolio
from strategies.base import ActionType
from strategies.buy_and_hold import BuyAndHoldStrategy


def _make_price_data(prices: list[float]) -> pd.DataFrame:
    """Create a simple OHLCV DataFrame from a list of close prices."""
    dates = pd.date_range("2025-01-01", periods=len(prices), freq="D")
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.01 for p in prices],
            "low": [p * 0.99 for p in prices],
            "close": prices,
            "volume": [1000.0] * len(prices),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


class TestBuyAndHoldStrategy:
    def test_first_bar_buys(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy(portfolio, {"symbol": "BTC"})
        data = _make_price_data([50000, 51000, 52000])

        action = strategy.on_bar(data.index[0], data.iloc[0], data.iloc[:1], is_last_bar=False)

        assert action.action == ActionType.BUY
        assert action.quantity > 0

    def test_middle_bars_hold(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy(portfolio, {"symbol": "BTC"})
        data = _make_price_data([50000, 51000, 52000, 53000])

        strategy.on_bar(data.index[0], data.iloc[0], data.iloc[:1], is_last_bar=False)

        for i in range(1, len(data) - 1):
            action = strategy.on_bar(
                data.index[i], data.iloc[i], data.iloc[:i + 1], is_last_bar=False
            )
            assert action.action == ActionType.HOLD

    def test_last_bar_sells(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy(portfolio, {"symbol": "BTC"})
        data = _make_price_data([50000, 51000, 52000])

        strategy.on_bar(data.index[0], data.iloc[0], data.iloc[:1], is_last_bar=False)
        strategy.on_bar(data.index[1], data.iloc[1], data.iloc[:2], is_last_bar=False)
        action = strategy.on_bar(data.index[2], data.iloc[2], data, is_last_bar=True)

        assert action.action == ActionType.SELL
        assert action.quantity > 0

    def test_action_counts(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy(portfolio, {"symbol": "BTC"})
        prices = [50000, 51000, 52000, 53000, 54000]
        data = _make_price_data(prices)

        actions = []
        for i in range(len(data)):
            is_last = i == len(data) - 1
            action = strategy.on_bar(
                data.index[i], data.iloc[i], data.iloc[:i + 1], is_last_bar=is_last
            )
            actions.append(action.action)

        assert actions.count(ActionType.BUY) == 1
        assert actions.count(ActionType.SELL) == 1
        assert actions.count(ActionType.HOLD) == len(prices) - 2

    def test_final_pnl_matches_price_change(self):
        initial_cash = 100000.0
        portfolio = Portfolio(initial_cash)
        strategy = BuyAndHoldStrategy(portfolio, {"symbol": "BTC"})

        start_price = 40000.0
        end_price = 48000.0
        prices = [start_price, 42000, 44000, 46000, end_price]
        data = _make_price_data(prices)

        for i in range(len(data)):
            is_last = i == len(data) - 1
            strategy.on_bar(
                data.index[i], data.iloc[i], data.iloc[:i + 1], is_last_bar=is_last
            )

        quantity = math.floor(initial_cash / start_price * 1e8) / 1e8
        expected_pnl = quantity * (end_price - start_price)
        actual_pnl = portfolio.cash - initial_cash

        assert actual_pnl == pytest.approx(expected_pnl, rel=1e-6)

    def test_portfolio_fully_liquidated_at_end(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy(portfolio, {"symbol": "BTC"})
        data = _make_price_data([50000, 55000])

        strategy.on_bar(data.index[0], data.iloc[0], data.iloc[:1], is_last_bar=False)
        strategy.on_bar(data.index[1], data.iloc[1], data, is_last_bar=True)

        assert len(portfolio.positions) == 0
        assert portfolio.cash > 0
