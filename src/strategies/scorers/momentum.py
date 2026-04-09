"""Momentum scorer — rate of change over N resampled bars."""

import pandas as pd

from .base import FlipScorer


class MomentumScorer(FlipScorer):
    """Scores based on price rate-of-change over a lookback window.

    For longs: positive ROC is good. For shorts: negative ROC is good.
    Returns abs(ROC) so higher = stronger move in the flip direction.
    """

    def __init__(self, params: dict):
        self.lookback_bars = params.get("lookback_bars", 20)

    def _compute_score(self, data_so_far: pd.DataFrame, direction: str,
                       resample_interval: str) -> float:
        if data_so_far.empty:
            return 0.0

        resampled = data_so_far["close"].resample(resample_interval).last().dropna()

        if len(resampled) < self.lookback_bars + 1:
            return 0.0

        current = resampled.iloc[-1]
        past = resampled.iloc[-self.lookback_bars - 1]

        if past <= 0:
            return 0.0

        roc = (current - past) / past

        # For longs we want positive momentum, for shorts negative
        if direction == "long":
            return max(roc, 0.0)
        else:
            return max(-roc, 0.0)

    def score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
              resample_interval: str) -> float:
        return self._compute_score(data_so_far, direction, resample_interval)

    def score_holding(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
                      resample_interval: str) -> float:
        return self._compute_score(data_so_far, direction, resample_interval)
