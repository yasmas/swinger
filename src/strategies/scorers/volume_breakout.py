"""Volume breakout scorer — compares recent volume to long-running average."""

import pandas as pd

from .base import FlipScorer


class VolumeBreakoutScorer(FlipScorer):
    """Scores based on volume breakout ratio.

    Compares recent resampled volume (short_window bars) to long-running
    average (long_window bars). Returns the ratio.
    """

    def __init__(self, params: dict):
        self.short_window = params.get("short_window", 5)
        self.long_window = params.get("long_window", 50)

    def _compute_ratio(self, data_so_far: pd.DataFrame, resample_interval: str) -> float:
        """Resample to interval and compute short/long volume ratio."""
        if data_so_far.empty:
            return 1.0

        resampled = data_so_far["volume"].resample(resample_interval).sum().dropna()

        if len(resampled) < self.long_window:
            return 1.0

        short_avg = resampled.iloc[-self.short_window:].mean()
        long_avg = resampled.iloc[-self.long_window:].mean()

        if long_avg <= 0:
            return 1.0

        return short_avg / long_avg

    def score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
              resample_interval: str) -> float:
        return self._compute_ratio(data_so_far, resample_interval)

    def score_holding(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
                      resample_interval: str) -> float:
        return self._compute_ratio(data_so_far, resample_interval)
