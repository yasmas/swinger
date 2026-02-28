import math

import numpy as np
import pandas as pd
import pytest

from execution.backtest_executor import BacktestExecutor
from portfolio import Portfolio
from strategies.base import ActionType, portfolio_view_from
from strategies.ma_crossover_rsi import MaCrossoverRsiStrategy, _compute_rsi


def _make_price_data(prices: list[float], freq: str = "D") -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(prices), freq=freq)
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


def _run_strategy(prices, config_overrides=None, initial_cash=100000):
    """Run the strategy over a price series and return (actions, portfolio)."""
    portfolio = Portfolio(initial_cash)
    cfg = {"symbol": "TEST", "short_window": 5, "long_window": 20, "rsi_period": 14, "rsi_threshold": 50}
    if config_overrides:
        cfg.update(config_overrides)
    strategy = MaCrossoverRsiStrategy(cfg)
    executor = BacktestExecutor()
    data = _make_price_data(prices)

    actions = []
    for i in range(len(data)):
        is_last = i == len(data) - 1
        pv = portfolio_view_from(portfolio, "TEST")
        action = strategy.on_bar(
            data.index[i], data.iloc[i], data.iloc[: i + 1], is_last_bar=is_last, pv=pv,
        )
        if action.action != ActionType.HOLD:
            executor.execute(action, "TEST", data.iloc[i]["close"], portfolio)
        actions.append(action)
    return actions, portfolio


class TestComputeRsi:
    def test_not_enough_data_returns_neutral(self):
        closes = pd.Series([100.0, 101.0, 102.0])
        assert _compute_rsi(closes, period=14) == 50.0

    def test_all_gains_returns_100(self):
        closes = pd.Series([float(i) for i in range(20)])
        assert _compute_rsi(closes, period=14) == 100.0

    def test_all_losses_returns_zero(self):
        closes = pd.Series([float(100 - i) for i in range(20)])
        assert _compute_rsi(closes, period=14) == pytest.approx(0.0)

    def test_mixed_values(self):
        np.random.seed(42)
        closes = pd.Series(np.cumsum(np.random.randn(50)) + 100)
        rsi = _compute_rsi(closes, period=14)
        assert 0 <= rsi <= 100


class TestMaCrossoverRsiStrategy:
    def test_no_signal_during_warmup(self):
        """During the first bars, MAs are close together so no crossover fires."""
        prices = [100.0] * 25
        actions, _ = _run_strategy(prices)
        non_hold = [a for a in actions if a.action != ActionType.HOLD]
        assert len(non_hold) == 0

    def test_bullish_crossover_triggers_buy(self):
        """A rising price series should cause short MA to cross above long MA and trigger a buy."""
        prices = [100.0] * 20 + [float(100 + i * 2) for i in range(1, 31)]
        actions, portfolio = _run_strategy(prices)
        buys = [a for a in actions if a.action == ActionType.BUY]
        assert len(buys) >= 1
        assert buys[0].details.get("crossover") == "bullish"

    def test_bearish_crossover_after_buy_triggers_sell(self):
        """Price rise then drop should produce buy followed by sell."""
        prices = (
            [100.0] * 20
            + [float(100 + i * 3) for i in range(1, 21)]
            + [float(160 - i * 3) for i in range(1, 21)]
        )
        actions, portfolio = _run_strategy(prices)
        buys = [a for a in actions if a.action == ActionType.BUY]
        sells = [a for a in actions if a.action == ActionType.SELL]
        assert len(buys) >= 1
        assert len(sells) >= 1
        first_buy_idx = next(i for i, a in enumerate(actions) if a.action == ActionType.BUY)
        first_sell_idx = next(i for i, a in enumerate(actions) if a.action == ActionType.SELL)
        assert first_sell_idx > first_buy_idx

    def test_sell_details_include_reason(self):
        prices = (
            [100.0] * 20
            + [float(100 + i * 3) for i in range(1, 21)]
            + [float(160 - i * 3) for i in range(1, 21)]
        )
        actions, _ = _run_strategy(prices)
        sells = [a for a in actions if a.action == ActionType.SELL]
        assert len(sells) >= 1
        for sell in sells:
            assert "reason" in sell.details

    def test_last_bar_liquidates_position(self):
        """If holding at the end, last bar forces a sell."""
        prices = [100.0] * 20 + [float(100 + i * 2) for i in range(1, 11)]
        actions, portfolio = _run_strategy(prices)
        if any(a.action == ActionType.BUY for a in actions):
            assert actions[-1].action == ActionType.SELL
            assert "Final bar" in actions[-1].details.get("reason", "")
            assert len(portfolio.positions) == 0

    def test_portfolio_fully_liquidated_at_end(self):
        prices = (
            [100.0] * 20
            + [float(100 + i * 3) for i in range(1, 21)]
            + [float(160 - i * 3) for i in range(1, 21)]
        )
        actions, portfolio = _run_strategy(prices)
        assert len(portfolio.positions) == 0

    def test_details_contain_ma_and_rsi(self):
        prices = [100.0] * 25
        actions, _ = _run_strategy(prices)
        for action in actions:
            assert "short_ma" in action.details
            assert "long_ma" in action.details
            assert "rsi" in action.details

    def test_custom_params_from_config(self):
        cfg = {
            "symbol": "TEST",
            "short_window": 3,
            "long_window": 10,
            "rsi_period": 7,
            "rsi_threshold": 40,
        }
        strategy = MaCrossoverRsiStrategy(cfg)
        assert strategy.short_window == 3
        assert strategy.long_window == 10
        assert strategy.rsi_period == 7
        assert strategy.rsi_threshold == 40

    def test_no_buy_without_rsi_confirmation(self):
        """Even if short MA crosses above long MA, no buy if RSI < threshold."""
        prices = (
            [100.0] * 20
            + [float(100 + i * 2) for i in range(1, 21)]
        )
        actions, _ = _run_strategy(prices, {"rsi_threshold": 101})
        buys = [a for a in actions if a.action == ActionType.BUY]
        assert len(buys) == 0

    def test_registry_contains_strategy(self):
        from strategies.registry import STRATEGY_REGISTRY
        assert "ma_crossover_rsi" in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY["ma_crossover_rsi"] is MaCrossoverRsiStrategy
