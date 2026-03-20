"""Pure indicator functions for the intraday trend strategy.

All functions take pandas Series/arrays and return pandas Series.
Reuses compute_ema, compute_atr, compute_adx from macd_rsi_advanced.
"""

import math

import numpy as np
import pandas as pd


def compute_wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average — weights linearly from 1 to period."""
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(window=period).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )


def compute_hma(closes: pd.Series, period: int) -> pd.Series:
    """Hull Moving Average — low-lag trend indicator.

    HMA(n) = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
    """
    half_period = max(int(period / 2), 1)
    sqrt_period = max(int(math.sqrt(period)), 1)

    wma_half = compute_wma(closes, half_period)
    wma_full = compute_wma(closes, period)
    diff = 2 * wma_half - wma_full
    hma = compute_wma(diff, sqrt_period)
    return hma


def compute_hmacd(
    closes: pd.Series, fast: int, slow: int, signal: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """HMACD — MACD variant using Hull Moving Averages instead of EMAs.

    Produces smoother, lower-lag crossover signals than standard EMA-based MACD.
    """
    hma_fast = compute_hma(closes, fast)
    hma_slow = compute_hma(closes, slow)
    hmacd_line = hma_fast - hma_slow
    signal_line = compute_hma(hmacd_line, signal)
    histogram = hmacd_line - signal_line
    return hmacd_line, signal_line, histogram


def compute_supertrend(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    atr_period: int,
    multiplier: float,
) -> tuple[pd.Series, pd.Series]:
    """Supertrend indicator.

    Returns:
        (supertrend_line, is_bullish) — the trend line and boolean direction.
    """
    from .macd_rsi_advanced import compute_atr

    atr = compute_atr(highs, lows, closes, atr_period)
    hl2 = (highs + lows) / 2

    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    n = len(closes)
    close_arr = closes.values
    st_line = np.full(n, np.nan)
    is_bullish = np.full(n, True)

    final_upper = upper_band.values.copy()
    final_lower = lower_band.values.copy()

    for i in range(1, n):
        # Skip if current bands are NaN (ATR warmup period)
        if np.isnan(final_upper[i]) or np.isnan(final_lower[i]):
            is_bullish[i] = is_bullish[i - 1]
            continue

        # If previous bands were NaN, initialize from current raw values
        if np.isnan(final_upper[i - 1]):
            final_upper[i - 1] = final_upper[i]
        if np.isnan(final_lower[i - 1]):
            final_lower[i - 1] = final_lower[i]

        # Final upper band: can only decrease (tighten)
        if final_upper[i] < final_upper[i - 1] or close_arr[i - 1] > final_upper[i - 1]:
            pass  # keep current value
        else:
            final_upper[i] = final_upper[i - 1]

        # Final lower band: can only increase (tighten)
        if final_lower[i] > final_lower[i - 1] or close_arr[i - 1] < final_lower[i - 1]:
            pass  # keep current value
        else:
            final_lower[i] = final_lower[i - 1]

        # Direction logic
        if is_bullish[i - 1]:
            if close_arr[i] < final_lower[i]:
                is_bullish[i] = False
                st_line[i] = final_upper[i]
            else:
                is_bullish[i] = True
                st_line[i] = final_lower[i]
        else:
            if close_arr[i] > final_upper[i]:
                is_bullish[i] = True
                st_line[i] = final_lower[i]
            else:
                is_bullish[i] = False
                st_line[i] = final_upper[i]

    # First bar
    if not np.isnan(final_lower[0]) and not np.isnan(final_upper[0]):
        st_line[0] = final_lower[0] if is_bullish[0] else final_upper[0]

    return (
        pd.Series(st_line, index=closes.index),
        pd.Series(is_bullish, index=closes.index),
    )


def compute_keltner(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    ema_period: int,
    atr_period: int,
    multiplier: float,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Keltner Channels.

    Returns:
        (upper, mid, lower) bands.
    """
    from .macd_rsi_advanced import compute_ema, compute_atr

    mid = compute_ema(closes, ema_period)
    atr = compute_atr(highs, lows, closes, atr_period)
    upper = mid + multiplier * atr
    lower = mid - multiplier * atr
    return upper, mid, lower


def compute_bollinger(
    closes: pd.Series, period: int, stddev: float
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands.

    Returns:
        (upper, mid, lower) bands.
    """
    mid = closes.rolling(window=period).mean()
    std = closes.rolling(window=period).std()
    upper = mid + stddev * std
    lower = mid - stddev * std
    return upper, mid, lower


def compute_squeeze(
    bb_upper: pd.Series,
    bb_lower: pd.Series,
    kc_upper: pd.Series,
    kc_lower: pd.Series,
) -> pd.Series:
    """TTM Squeeze detection — True when BB is inside KC (consolidation)."""
    return (bb_lower > kc_lower) & (bb_upper < kc_upper)


def compute_vwap_daily(
    highs: pd.Series,
    lows: pd.Series,
    closes: pd.Series,
    volumes: pd.Series,
    dates: pd.DatetimeIndex,
) -> pd.Series:
    """Daily-reset VWAP (resets at 00:00 UTC each day).

    typical_price = (high + low + close) / 3
    VWAP = cumsum(TP * volume) / cumsum(volume), reset daily.
    """
    tp = (highs + lows + closes) / 3
    tp_vol = tp * volumes

    # Group by date for daily reset
    day = dates.date

    cum_tp_vol = tp_vol.groupby(day).cumsum()
    cum_vol = volumes.groupby(day).cumsum()

    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    return vwap
