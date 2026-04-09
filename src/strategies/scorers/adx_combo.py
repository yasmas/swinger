"""ADX-combined scorers — multiply base scorer by ADX trend strength.

ADX (Average Directional Index) measures trend strength (0-100), not direction.
High ADX = strong trend. Used here as a multiplier on top of an existing scorer:

    final_score = base_score * (adx / adx_scale)

This boosts assets where the flip is backed by a strong directional trend,
and suppresses flips in choppy, low-ADX markets.
"""

import numpy as np
import pandas as pd

from .base import FlipScorer
from .volume_breakout import VolumeBreakoutScorer
from .relative_strength import RelativeStrengthScorer


def _compute_adx(highs: pd.Series, lows: pd.Series, closes: pd.Series,
                 period: int = 14) -> pd.Series:
    """Compute ADX from resampled OHLC data."""
    # True Range
    tr = pd.concat([
        highs - lows,
        (highs - closes.shift(1)).abs(),
        (lows - closes.shift(1)).abs(),
    ], axis=1).max(axis=1)

    # Directional movement
    up_move = highs - highs.shift(1)
    down_move = lows.shift(1) - lows

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # Smoothed (Wilder)
    atr_s = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_s.replace(0, np.nan)

    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx


def _adx_value(data_so_far: pd.DataFrame, resample_interval: str,
               adx_period: int) -> float:
    """Return the latest ADX value on resampled data. Returns 25.0 (neutral) on failure."""
    if data_so_far.empty:
        return 25.0

    resampled = data_so_far.resample(resample_interval).agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna()

    needed = adx_period * 3
    if len(resampled) < needed:
        return 25.0

    adx = _compute_adx(resampled["high"], resampled["low"], resampled["close"], adx_period)
    val = adx.iloc[-1]
    return float(val) if not np.isnan(val) else 25.0


class VolumeBreakoutADX(FlipScorer):
    """Volume breakout score multiplied by ADX trend strength.

    score = volume_ratio * (adx / adx_scale)

    adx_scale normalises ADX so that adx=25 (neutral trend) → multiplier=1.0.
    Strong trends (adx=50) → multiplier=2.0. Weak/choppy (adx=12) → 0.5.
    """

    def __init__(self, params: dict):
        self.vb = VolumeBreakoutScorer(params)
        self.adx_period = params.get("adx_period", 14)
        self.adx_scale = params.get("adx_scale", 25.0)

    def _score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
               resample_interval: str) -> float:
        vb_score = self.vb._compute_ratio(data_so_far, resample_interval)
        adx = _adx_value(data_so_far, resample_interval, self.adx_period)
        return vb_score * (adx / self.adx_scale)

    def score(self, symbol, data_so_far, direction, resample_interval):
        return self._score(symbol, data_so_far, direction, resample_interval)

    def score_holding(self, symbol, data_so_far, direction, resample_interval):
        return self._score(symbol, data_so_far, direction, resample_interval)


class RelativeStrengthADX(FlipScorer):
    """Relative strength score multiplied by ADX trend strength.

    score = relative_return * (adx / adx_scale)
    """

    def __init__(self, params: dict):
        self.rs = RelativeStrengthScorer(params)
        self.adx_period = params.get("adx_period", 14)
        self.adx_scale = params.get("adx_scale", 25.0)

    def set_universe_data(self, datasets: dict) -> None:
        self.rs.set_universe_data(datasets)

    def _score(self, symbol: str, data_so_far: pd.DataFrame, direction: str,
               resample_interval: str) -> float:
        rs_score = self.rs._compute_score(symbol, data_so_far, direction, resample_interval)
        adx = _adx_value(data_so_far, resample_interval, self.adx_period)
        return rs_score * (adx / self.adx_scale)

    def score(self, symbol, data_so_far, direction, resample_interval):
        return self._score(symbol, data_so_far, direction, resample_interval)

    def score_holding(self, symbol, data_so_far, direction, resample_interval):
        return self._score(symbol, data_so_far, direction, resample_interval)
