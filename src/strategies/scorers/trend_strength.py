"""Trend strength scorer — distance from Supertrend line / ATR."""

import numpy as np
import pandas as pd

from .base import FlipScorer
from ..intraday_indicators import compute_supertrend
from ..macd_rsi_advanced import compute_atr


class TrendStrengthScorer(FlipScorer):
    """Scores based on how far price has pulled from the Supertrend line.

    Normalized by ATR. Higher = price has moved further in the trend
    direction relative to volatility. Uses the same ST params as the
    strategy for consistency.
    """

    def __init__(self, params: dict):
        self.st_atr_period = params.get("st_atr_period", 10)
        self.st_multiplier = params.get("st_multiplier", 2.0)

    def _compute_score(self, data_so_far: pd.DataFrame, direction: str,
                       resample_interval: str) -> float:
        if data_so_far.empty:
            return 0.0

        resampled = data_so_far.resample(resample_interval).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last",
        }).dropna()

        needed = self.st_atr_period * 3
        if len(resampled) < needed:
            return 0.0

        closes = resampled["close"]
        highs = resampled["high"]
        lows = resampled["low"]

        st_line, _ = compute_supertrend(
            highs, lows, closes, self.st_atr_period, self.st_multiplier
        )
        atr = compute_atr(highs, lows, closes, self.st_atr_period)

        last_st = st_line.iloc[-1]
        last_atr = atr.iloc[-1]
        last_close = closes.iloc[-1]

        if np.isnan(last_st) or np.isnan(last_atr) or last_atr <= 0:
            return 0.0

        # Distance from ST line normalized by ATR
        dist = (last_close - last_st) / last_atr

        if direction == "long":
            return max(dist, 0.0)
        else:
            return max(-dist, 0.0)

    def score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
              resample_interval: str) -> float:
        return self._compute_score(data_so_far, direction, resample_interval)

    def score_holding(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
                      resample_interval: str) -> float:
        return self._compute_score(data_so_far, direction, resample_interval)
