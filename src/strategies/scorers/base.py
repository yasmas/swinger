"""Scorer interface for SwingParty candidate ranking."""

from abc import ABC, abstractmethod

import pandas as pd


class FlipScorer(ABC):
    """Scores flip candidates and existing holdings for rotation decisions."""

    @abstractmethod
    def score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
              resample_interval: str) -> float:
        """Score a new flip candidate. Higher = stronger signal.

        Args:
            symbol: Asset ticker.
            data_so_far: All 5m bars up to current timestamp for this symbol.
            direction: "long" or "short".
            resample_interval: Resample frequency (e.g. "1h").

        Returns:
            Score value (higher is better).
        """
        pass

    @abstractmethod
    def score_holding(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
                      resample_interval: str) -> float:
        """Re-score a current holding. Used for eviction comparison.

        Args:
            symbol: Asset ticker.
            data_so_far: All 5m bars up to current timestamp for this symbol.
            direction: "long" or "short".
            resample_interval: Resample frequency (e.g. "1h").

        Returns:
            Score value (higher is better, lowest gets evicted).
        """
        pass
