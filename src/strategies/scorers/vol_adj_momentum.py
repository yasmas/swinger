"""Volatility-adjusted momentum scorer — momentum / ATR."""

import numpy as np
import pandas as pd

from .base import FlipScorer


class VolAdjMomentumScorer(FlipScorer):
    """Scores based on momentum normalized by ATR.

    Rewards moves that are large relative to the asset's own noise level.
    Higher score = stronger directional move per unit of volatility.
    """

    def __init__(self, params: dict):
        self.lookback_bars = params.get("lookback_bars", 20)
        self.atr_period = params.get("atr_period", 14)

    def _compute_score(self, data_so_far: pd.DataFrame, direction: str,
                       resample_interval: str) -> float:
        if data_so_far.empty:
            return 0.0

        resampled = data_so_far.resample(resample_interval).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last",
        }).dropna()

        needed = max(self.lookback_bars + 1, self.atr_period + 1)
        if len(resampled) < needed:
            return 0.0

        closes = resampled["close"]
        highs = resampled["high"]
        lows = resampled["low"]

        # ATR
        tr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows - closes.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_period).mean().iloc[-1]

        if atr <= 0 or np.isnan(atr):
            return 0.0

        # Momentum
        current = closes.iloc[-1]
        past = closes.iloc[-self.lookback_bars - 1]
        if past <= 0:
            return 0.0

        move = current - past

        # Normalize by ATR
        if direction == "long":
            return max(move / atr, 0.0)
        else:
            return max(-move / atr, 0.0)

    def score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
              resample_interval: str) -> float:
        return self._compute_score(data_so_far, direction, resample_interval)

    def score_holding(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
                      resample_interval: str) -> float:
        return self._compute_score(data_so_far, direction, resample_interval)
