"""Relative strength scorer — asset return vs universe average."""

import pandas as pd

from .base import FlipScorer


class RelativeStrengthScorer(FlipScorer):
    """Scores based on how an asset's recent return compares to the universe.

    Computes each asset's return over lookback_bars resampled periods.
    For new entries: uses the absolute return (direction-adjusted).
    For holdings: same calculation (re-evaluated with current data).

    Note: This scorer needs all assets' data to compute relative strength,
    but the FlipScorer interface only passes one asset at a time. We store
    a reference to all datasets and compute relative to the universe.
    """

    def __init__(self, params: dict):
        self.lookback_bars = params.get("lookback_bars", 20)
        self._all_data: dict[str, pd.DataFrame] = {}

    def set_universe_data(self, datasets: dict[str, pd.DataFrame]) -> None:
        """Called by coordinator to provide all asset data for relative comparison."""
        self._all_data = datasets

    def _asset_return(self, data_so_far: pd.DataFrame, resample_interval: str) -> float:
        """Compute return over lookback window for one asset."""
        if data_so_far.empty:
            return 0.0

        resampled = data_so_far["close"].resample(resample_interval).last().dropna()

        if len(resampled) < self.lookback_bars + 1:
            return 0.0

        current = resampled.iloc[-1]
        past = resampled.iloc[-self.lookback_bars - 1]

        if past <= 0:
            return 0.0

        return (current - past) / past

    def _compute_score(self, symbol: str, data_so_far: pd.DataFrame,
                       direction: str, resample_interval: str) -> float:
        asset_ret = self._asset_return(data_so_far, resample_interval)

        # Compute universe average return
        if self._all_data:
            returns = []
            for sym, df in self._all_data.items():
                # Use data up to the same timestamp as data_so_far
                if not data_so_far.empty and not df.empty:
                    last_ts = data_so_far.index[-1]
                    df_trimmed = df.loc[:last_ts]
                    ret = self._asset_return(df_trimmed, resample_interval)
                    returns.append(ret)

            if returns:
                avg_ret = sum(returns) / len(returns)
                relative = asset_ret - avg_ret
            else:
                relative = asset_ret
        else:
            relative = asset_ret

        # Direction adjustment: for longs, positive relative strength is good
        # For shorts, negative relative strength (weakest) is good to short
        if direction == "long":
            return max(relative, 0.0)
        else:
            return max(-relative, 0.0)

    def score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
              resample_interval: str) -> float:
        return self._compute_score(symbol, data_so_far, direction, resample_interval)

    def score_holding(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
                      resample_interval: str) -> float:
        return self._compute_score(symbol, data_so_far, direction, resample_interval)
