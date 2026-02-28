import math

import pandas as pd
import pytest

from execution.backtest_executor import BacktestExecutor
from portfolio import Portfolio
from strategies.base import ActionType, portfolio_view_from
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


def _run_full(prices, initial_cash=100000):
    """Run BuyAndHold through executor, return (actions, portfolio)."""
    portfolio = Portfolio(initial_cash)
    strategy = BuyAndHoldStrategy({"symbol": "BTC"})
    executor = BacktestExecutor()
    data = _make_price_data(prices)

    actions = []
    for i in range(len(data)):
        is_last = i == len(data) - 1
        pv = portfolio_view_from(portfolio, "BTC")
        action = strategy.on_bar(
            data.index[i], data.iloc[i], data.iloc[:i + 1], is_last_bar=is_last, pv=pv,
        )
        if action.action != ActionType.HOLD:
            executor.execute(action, "BTC", data.iloc[i]["close"], portfolio)
        actions.append(action)
    return actions, portfolio


class TestBuyAndHoldStrategy:
    def test_first_bar_buys(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy({"symbol": "BTC"})
        data = _make_price_data([50000, 51000, 52000])

        pv = portfolio_view_from(portfolio, "BTC")
        action = strategy.on_bar(data.index[0], data.iloc[0], data.iloc[:1], is_last_bar=False, pv=pv)

        assert action.action == ActionType.BUY
        assert action.quantity > 0

    def test_middle_bars_hold(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy({"symbol": "BTC"})
        executor = BacktestExecutor()
        data = _make_price_data([50000, 51000, 52000, 53000])

        pv = portfolio_view_from(portfolio, "BTC")
        action = strategy.on_bar(data.index[0], data.iloc[0], data.iloc[:1], is_last_bar=False, pv=pv)
        executor.execute(action, "BTC", 50000, portfolio)

        for i in range(1, len(data) - 1):
            pv = portfolio_view_from(portfolio, "BTC")
            action = strategy.on_bar(
                data.index[i], data.iloc[i], data.iloc[:i + 1], is_last_bar=False, pv=pv,
            )
            assert action.action == ActionType.HOLD

    def test_last_bar_sells(self):
        portfolio = Portfolio(100000)
        strategy = BuyAndHoldStrategy({"symbol": "BTC"})
        executor = BacktestExecutor()
        data = _make_price_data([50000, 51000, 52000])

        pv = portfolio_view_from(portfolio, "BTC")
        action = strategy.on_bar(data.index[0], data.iloc[0], data.iloc[:1], is_last_bar=False, pv=pv)
        executor.execute(action, "BTC", 50000, portfolio)

        pv = portfolio_view_from(portfolio, "BTC")
        strategy.on_bar(data.index[1], data.iloc[1], data.iloc[:2], is_last_bar=False, pv=pv)

        pv = portfolio_view_from(portfolio, "BTC")
        action = strategy.on_bar(data.index[2], data.iloc[2], data, is_last_bar=True, pv=pv)

        assert action.action == ActionType.SELL
        assert action.quantity > 0

    def test_action_counts(self):
        prices = [50000, 51000, 52000, 53000, 54000]
        actions, _ = _run_full(prices)

        action_types = [a.action for a in actions]
        assert action_types.count(ActionType.BUY) == 1
        assert action_types.count(ActionType.SELL) == 1
        assert action_types.count(ActionType.HOLD) == len(prices) - 2

    def test_final_pnl_matches_price_change(self):
        initial_cash = 100000.0
        start_price = 40000.0
        end_price = 48000.0
        prices = [start_price, 42000, 44000, 46000, end_price]

        _, portfolio = _run_full(prices, initial_cash=initial_cash)

        quantity = math.floor(initial_cash / start_price * 1e8) / 1e8
        expected_pnl = quantity * (end_price - start_price)
        actual_pnl = portfolio.cash - initial_cash

        assert actual_pnl == pytest.approx(expected_pnl, rel=1e-6)

    def test_portfolio_fully_liquidated_at_end(self):
        _, portfolio = _run_full([50000, 55000])
        assert len(portfolio.positions) == 0
        assert portfolio.cash > 0
