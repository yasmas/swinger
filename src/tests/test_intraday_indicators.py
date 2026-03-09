"""Tests for intraday indicator functions."""

import math

import numpy as np
import pandas as pd
import pytest

from strategies.intraday_indicators import (
    compute_wma,
    compute_hma,
    compute_supertrend,
    compute_keltner,
    compute_bollinger,
    compute_squeeze,
    compute_vwap_daily,
)


def _make_series(values):
    return pd.Series(values, dtype=float)


class TestWMA:
    def test_basic(self):
        # WMA(3) of [1,2,3,4,5]: last window [3,4,5], weights [1,2,3]
        # = (3*1 + 4*2 + 5*3) / 6 = (3+8+15)/6 = 26/6 ≈ 4.333
        s = _make_series([1, 2, 3, 4, 5])
        result = compute_wma(s, 3)
        assert np.isnan(result.iloc[0])
        assert np.isnan(result.iloc[1])
        expected = (1 * 1 + 2 * 2 + 3 * 3) / 6
        assert abs(result.iloc[2] - expected) < 1e-9
        expected_last = (3 * 1 + 4 * 2 + 5 * 3) / 6
        assert abs(result.iloc[4] - expected_last) < 1e-9

    def test_period_1(self):
        s = _make_series([10, 20, 30])
        result = compute_wma(s, 1)
        assert abs(result.iloc[0] - 10) < 1e-9
        assert abs(result.iloc[2] - 30) < 1e-9


class TestHMA:
    def test_length(self):
        s = _make_series(range(1, 101))
        result = compute_hma(s, 21)
        assert len(result) == 100

    def test_follows_trend(self):
        # On a rising series, HMA should be rising too
        s = _make_series(range(1, 51))
        result = compute_hma(s, 9)
        # After warmup, HMA should be increasing
        valid = result.dropna()
        diffs = valid.diff().dropna()
        assert (diffs > 0).all(), "HMA should be rising on a linear uptrend"

    def test_low_lag(self):
        # HMA should be closer to current price than a simple SMA
        n = 100
        s = _make_series(range(1, n + 1))
        hma = compute_hma(s, 9)
        sma = s.rolling(9).mean()
        # At the end, HMA should be closer to the last value than SMA
        assert abs(hma.iloc[-1] - s.iloc[-1]) < abs(sma.iloc[-1] - s.iloc[-1])


class TestSupertrend:
    def _make_trending_data(self, n=100, start=100, step=0.5):
        """Create uptrending OHLCV data."""
        closes = _make_series([start + i * step for i in range(n)])
        highs = closes + 1.0
        lows = closes - 1.0
        return highs, lows, closes

    def test_returns_correct_shape(self):
        highs, lows, closes = self._make_trending_data()
        st_line, is_bullish = compute_supertrend(highs, lows, closes, 10, 3.0)
        assert len(st_line) == len(closes)
        assert len(is_bullish) == len(closes)

    def test_bullish_in_uptrend(self):
        highs, lows, closes = self._make_trending_data(n=200, step=1.0)
        st_line, is_bullish = compute_supertrend(highs, lows, closes, 10, 3.0)
        # After warmup, should be mostly bullish
        assert is_bullish.iloc[-1] == True

    def test_bearish_in_downtrend(self):
        closes = _make_series([200 - i * 1.0 for i in range(200)])
        highs = closes + 1.0
        lows = closes - 1.0
        st_line, is_bullish = compute_supertrend(highs, lows, closes, 10, 3.0)
        assert is_bullish.iloc[-1] == False

    def test_line_below_price_when_bullish(self):
        highs, lows, closes = self._make_trending_data(n=200, step=1.0)
        st_line, is_bullish = compute_supertrend(highs, lows, closes, 10, 3.0)
        # Where bullish, supertrend line should be below price
        bullish_mask = is_bullish & ~st_line.isna()
        if bullish_mask.any():
            assert (closes[bullish_mask] > st_line[bullish_mask]).all()


class TestKeltner:
    def test_bands_symmetric(self):
        closes = _make_series([100] * 50)
        highs = _make_series([101] * 50)
        lows = _make_series([99] * 50)
        upper, mid, lower = compute_keltner(highs, lows, closes, 15, 10, 2.0)
        # Mid should converge to 100
        assert abs(mid.iloc[-1] - 100) < 1
        # Upper and lower should be symmetric around mid
        assert abs((upper.iloc[-1] - mid.iloc[-1]) - (mid.iloc[-1] - lower.iloc[-1])) < 0.01

    def test_upper_above_lower(self):
        np.random.seed(42)
        n = 100
        closes = _make_series(np.cumsum(np.random.randn(n)) + 100)
        highs = closes + abs(np.random.randn(n))
        lows = closes - abs(np.random.randn(n))
        upper, mid, lower = compute_keltner(highs, lows, closes, 15, 10, 2.0)
        valid = ~upper.isna() & ~lower.isna()
        assert (upper[valid] >= lower[valid]).all()


class TestBollinger:
    def test_constant_series(self):
        closes = _make_series([50.0] * 30)
        upper, mid, lower = compute_bollinger(closes, 20, 2.0)
        # For constant series, std=0, so all bands = mid = 50
        assert abs(mid.iloc[-1] - 50) < 1e-9
        assert abs(upper.iloc[-1] - 50) < 1e-9
        assert abs(lower.iloc[-1] - 50) < 1e-9

    def test_volatile_series_wider_bands(self):
        np.random.seed(42)
        calm = _make_series([100 + 0.01 * np.random.randn() for _ in range(30)])
        wild = _make_series([100 + 5 * np.random.randn() for _ in range(30)])
        _, _, _ = compute_bollinger(calm, 20, 2.0)
        u_wild, _, l_wild = compute_bollinger(wild, 20, 2.0)
        u_calm, _, l_calm = compute_bollinger(calm, 20, 2.0)
        # Wild series should have wider bands
        wild_width = (u_wild.iloc[-1] - l_wild.iloc[-1])
        calm_width = (u_calm.iloc[-1] - l_calm.iloc[-1])
        assert wild_width > calm_width


class TestSqueeze:
    def test_squeeze_on_when_bb_inside_kc(self):
        bb_upper = _make_series([102])
        bb_lower = _make_series([98])
        kc_upper = _make_series([105])
        kc_lower = _make_series([95])
        result = compute_squeeze(bb_upper, bb_lower, kc_upper, kc_lower)
        assert result.iloc[0] == True

    def test_squeeze_off_when_bb_outside_kc(self):
        bb_upper = _make_series([110])
        bb_lower = _make_series([90])
        kc_upper = _make_series([105])
        kc_lower = _make_series([95])
        result = compute_squeeze(bb_upper, bb_lower, kc_upper, kc_lower)
        assert result.iloc[0] == False


class TestVWAPDaily:
    def test_single_day(self):
        dates = pd.date_range("2024-01-01", periods=5, freq="5min")
        highs = _make_series([101, 102, 103, 104, 105])
        lows = _make_series([99, 100, 101, 102, 103])
        closes = _make_series([100, 101, 102, 103, 104])
        volumes = _make_series([10, 10, 10, 10, 10])

        vwap = compute_vwap_daily(highs, lows, closes, volumes, dates)

        # First bar: TP = (101+99+100)/3 = 100, VWAP = 100
        assert abs(vwap.iloc[0] - 100.0) < 0.01
        # VWAP should be rising on this uptrend
        assert vwap.iloc[-1] > vwap.iloc[0]

    def test_daily_reset(self):
        dates = pd.DatetimeIndex([
            "2024-01-01 23:55:00",
            "2024-01-02 00:00:00",
            "2024-01-02 00:05:00",
        ])
        highs = _make_series([101, 201, 202])
        lows = _make_series([99, 199, 200])
        closes = _make_series([100, 200, 201])
        volumes = _make_series([10, 10, 10])

        vwap = compute_vwap_daily(highs, lows, closes, volumes, dates)

        # Day 1 (bar 0): TP = 100, VWAP = 100
        assert abs(vwap.iloc[0] - 100.0) < 0.01
        # Day 2 (bar 1): reset, TP = 200, VWAP = 200
        assert abs(vwap.iloc[1] - 200.0) < 0.01
