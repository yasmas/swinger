import math

import numpy as np
import pandas as pd
import pytest

from execution.backtest_executor import BacktestExecutor
from portfolio import Portfolio
from strategies.base import ActionType, PortfolioView, portfolio_view_from
from strategies.macd_rsi_advanced import (
    MACDRSIAdvancedStrategy,
    compute_ema,
    compute_macd,
    compute_rsi,
    compute_adx,
    compute_atr,
    resample_ohlcv,
)


def _make_5min_data(prices: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(prices), freq="5min")
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p * 1.005 for p in prices],
            "low": [p * 0.995 for p in prices],
            "close": prices,
            "volume": [100.0] * len(prices),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def _trending_up(n: int, start: float = 40000, slope: float = 5.0, noise: float = 50.0) -> list[float]:
    """Generate an uptrending price series with noise."""
    np.random.seed(42)
    return [start + slope * i + np.random.randn() * noise for i in range(n)]


def _trending_down(n: int, start: float = 60000, slope: float = 5.0, noise: float = 50.0) -> list[float]:
    np.random.seed(42)
    return [start - slope * i + np.random.randn() * noise for i in range(n)]


def _sideways(n: int, center: float = 50000, noise: float = 100.0) -> list[float]:
    np.random.seed(42)
    return [center + np.random.randn() * noise for i in range(n)]


DEFAULT_CFG = {
    "symbol": "TEST",
    "resample_interval": "1h",
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "rsi_period": 14,
    "rsi_entry_low": 40,
    "rsi_overbought": 70,
    "adx_period": 14,
    "adx_threshold": 20,
    "atr_period": 14,
    "atr_stop_multiplier": 2.0,
    "atr_trailing_multiplier": 1.5,
    "ema_trend_period": 200,
    "cooldown_bars": 12,
}


def _run_strategy(prices, config_overrides=None, initial_cash=100000):
    portfolio = Portfolio(initial_cash)
    cfg = {**DEFAULT_CFG}
    if config_overrides:
        cfg.update(config_overrides)
    strategy = MACDRSIAdvancedStrategy(cfg)
    executor = BacktestExecutor()
    data = _make_5min_data(prices)
    strategy.prepare(data)

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


# --- Unit tests for indicator functions ---

class TestComputeEma:
    def test_ema_length_matches_input(self):
        s = pd.Series(range(50), dtype=float)
        assert len(compute_ema(s, 10)) == 50

    def test_ema_smooths(self):
        np.random.seed(0)
        noisy = pd.Series(np.cumsum(np.random.randn(100)) + 100)
        ema = compute_ema(noisy, 20)
        assert ema.std() < noisy.std()


class TestComputeMacd:
    def test_macd_shapes(self):
        closes = pd.Series(range(50), dtype=float)
        macd_line, signal_line, histogram = compute_macd(closes, 12, 26, 9)
        assert len(macd_line) == 50
        assert len(signal_line) == 50
        assert len(histogram) == 50

    def test_macd_positive_in_uptrend(self):
        closes = pd.Series(range(60), dtype=float)
        macd_line, _, _ = compute_macd(closes, 12, 26, 9)
        assert macd_line.iloc[-1] > 0


class TestComputeRsi:
    def test_all_up_returns_near_100(self):
        closes = pd.Series(range(1, 30), dtype=float)
        rsi = compute_rsi(closes, 14)
        assert rsi.iloc[-1] > 95

    def test_all_down_returns_near_0(self):
        closes = pd.Series(range(30, 0, -1), dtype=float)
        rsi = compute_rsi(closes, 14)
        assert rsi.iloc[-1] < 5

    def test_range_0_100(self):
        np.random.seed(42)
        closes = pd.Series(np.cumsum(np.random.randn(100)) + 100)
        rsi = compute_rsi(closes, 14)
        valid = rsi.dropna()
        assert valid.min() >= 0
        assert valid.max() <= 100


class TestComputeAdx:
    def test_strong_trend_has_high_adx(self):
        n = 100
        closes = pd.Series(range(100, 100 + n), dtype=float)
        highs = closes + 1
        lows = closes - 1
        adx = compute_adx(highs, lows, closes, 14)
        assert adx.iloc[-1] > 20

    def test_sideways_has_low_adx(self):
        np.random.seed(0)
        n = 200
        closes = pd.Series(50000 + np.random.randn(n) * 5, dtype=float)
        highs = closes + 2
        lows = closes - 2
        adx = compute_adx(highs, lows, closes, 14)
        assert adx.iloc[-1] < 30


class TestResampleOhlcv:
    def test_resample_reduces_bars(self):
        prices = [100.0] * (12 * 24)  # 24 hours of 5-min bars
        data = _make_5min_data(prices)
        resampled = resample_ohlcv(data, "1h")
        assert len(resampled) == 24

    def test_resample_preserves_ohlcv(self):
        prices = list(range(100, 112))
        data = _make_5min_data(prices)
        resampled = resample_ohlcv(data, "1h")
        assert len(resampled) == 1
        assert resampled.iloc[0]["open"] == data.iloc[0]["open"]
        assert resampled.iloc[0]["close"] == data.iloc[-1]["close"]


# --- Strategy integration tests ---

class TestMACDRSIAdvancedStrategy:
    def test_warmup_holds(self):
        """Strategy should HOLD during indicator warmup period."""
        prices = [50000.0] * 100
        actions, _ = _run_strategy(prices)
        assert all(a.action == ActionType.HOLD for a in actions)

    def test_warmup_details_say_warming_up(self):
        prices = [50000.0] * 50
        actions, _ = _run_strategy(prices)
        warming = [a for a in actions if "Warming up" in a.details.get("reason", "")]
        assert len(warming) > 0

    def test_strong_uptrend_enters(self):
        """After a flat period, a strong uptrend should produce a MACD crossover and trigger entry."""
        flat = _sideways(2400, center=50000, noise=20.0)
        up = _trending_up(2400, start=50000, slope=5.0, noise=30.0)
        prices = flat + up
        actions, portfolio = _run_strategy(prices, {"ema_trend_period": 50})
        buys = [a for a in actions if a.action == ActionType.BUY]
        assert len(buys) >= 1, "Expected at least one buy after flat->uptrend transition"

    def test_no_entry_in_sideways_market(self):
        """ADX filter should block entries in sideways/ranging market."""
        prices = _sideways(4000, center=50000, noise=10.0)
        actions, _ = _run_strategy(prices, {"adx_threshold": 40})
        buys = [a for a in actions if a.action == ActionType.BUY]
        assert len(buys) == 0, "Should not buy in sideways market with high ADX threshold"

    def test_no_entry_below_ema200(self):
        """Should not buy when price is below EMA-200 (downtrend)."""
        prices = _trending_down(4000, start=60000, slope=3.0, noise=30.0)
        actions, _ = _run_strategy(prices)
        buys = [a for a in actions if a.action == ActionType.BUY]
        assert len(buys) == 0, "Should not buy in downtrend below EMA-200"

    def test_last_bar_liquidates(self):
        """Portfolio should be fully liquidated by the end regardless of exit reason."""
        flat = _sideways(2400, center=50000, noise=20.0)
        up = _trending_up(2400, start=50000, slope=5.0, noise=30.0)
        prices = flat + up
        actions, portfolio = _run_strategy(prices, {"ema_trend_period": 50})
        assert len(portfolio.positions) == 0, "Portfolio should be empty at end"

    def test_stop_loss_triggers_on_check_exit(self):
        """Directly verify that _check_exit fires the stop when price drops below the stop level."""
        cfg = {**DEFAULT_CFG, "symbol": "TEST"}
        strategy = MACDRSIAdvancedStrategy(cfg)

        strategy._entry_price = 50000
        strategy._peak_since_entry = 50000
        strategy._prev_macd = 100.0
        strategy._prev_signal = 50.0

        pv = PortfolioView(cash=0.0, position_qty=1.0, position_avg_cost=50000.0)
        details = {"macd": 80.0, "macd_signal": 60.0, "rsi": 55.0, "adx": 30.0, "atr": 1000.0, "ema_200": 49000.0}
        action = strategy._check_exit(45000, pv, 80.0, 60.0, 55.0, 1000.0, False, dict(details))
        assert action.action == ActionType.SELL
        assert "Stop-loss" in action.details["reason"]

    def test_trailing_stop_triggers(self):
        """Trailing stop fires when price drops from peak by more than the trail distance."""
        cfg = {**DEFAULT_CFG, "symbol": "TEST"}
        strategy = MACDRSIAdvancedStrategy(cfg)

        strategy._entry_price = 50000
        strategy._peak_since_entry = 55000
        strategy._prev_macd = 100.0
        strategy._prev_signal = 50.0

        pv = PortfolioView(cash=0.0, position_qty=1.0, position_avg_cost=50000.0)
        details = {"macd": 80.0, "macd_signal": 60.0, "rsi": 55.0, "adx": 30.0, "atr": 1000.0, "ema_200": 49000.0}
        action = strategy._check_exit(50000, pv, 80.0, 60.0, 55.0, 1000.0, False, dict(details))
        assert action.action == ActionType.SELL
        assert "Trailing stop" in action.details["reason"]

    def test_cooldown_prevents_immediate_reentry(self):
        """After a sell, cooldown should prevent buying on the next bar."""
        flat = _sideways(2400, center=50000, noise=20.0)
        up = _trending_up(2400, start=50000, slope=5.0, noise=30.0)
        prices = flat + up
        actions, _ = _run_strategy(prices, {"ema_trend_period": 50, "cooldown_bars": 100})
        buys = [i for i, a in enumerate(actions) if a.action == ActionType.BUY]
        sells = [i for i, a in enumerate(actions) if a.action == ActionType.SELL]

        for sell_idx in sells:
            immediate_buys = [b for b in buys if sell_idx < b < sell_idx + 100 * 12]
            assert len(immediate_buys) == 0, f"Buy within cooldown after sell at bar {sell_idx}"

    def test_details_contain_all_indicators(self):
        """Trade details should include MACD, RSI, ADX, ATR, EMA values."""
        flat = _sideways(2400, center=50000, noise=20.0)
        up = _trending_up(1200, start=50000, slope=5.0, noise=30.0)
        prices = flat + up
        actions, _ = _run_strategy(prices, {"ema_trend_period": 50})
        non_warmup = [a for a in actions if "Warming up" not in a.details.get("reason", "")]
        assert len(non_warmup) > 0
        sample = non_warmup[-1]
        for key in ["macd", "macd_signal", "rsi", "adx", "atr", "ema_200"]:
            assert key in sample.details, f"Missing {key} in details"

    def test_far_fewer_trades_than_naive(self):
        """Should produce far fewer trades than the naive MA crossover on same data."""
        flat = _sideways(2400, center=50000, noise=20.0)
        up = _trending_up(2400, start=50000, slope=5.0, noise=100.0)
        prices = flat + up
        actions, _ = _run_strategy(prices, {"ema_trend_period": 50})
        trades = [a for a in actions if a.action != ActionType.HOLD]
        assert len(trades) < 50, f"Expected fewer than 50 trades, got {len(trades)}"

    def test_registry_contains_strategy(self):
        from strategies.registry import STRATEGY_REGISTRY
        assert "macd_rsi_advanced" in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY["macd_rsi_advanced"] is MACDRSIAdvancedStrategy

    def test_custom_params_from_config(self):
        cfg = {**DEFAULT_CFG, "macd_fast": 8, "adx_threshold": 25, "cooldown_bars": 20}
        strategy = MACDRSIAdvancedStrategy(cfg)
        assert strategy.macd_fast == 8
        assert strategy.adx_threshold == 25
        assert strategy.cooldown_bars == 20
