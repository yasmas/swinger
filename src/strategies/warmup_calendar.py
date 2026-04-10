"""Shared warmup window sizing for SwingParty downloads and multi-asset backtests."""

from __future__ import annotations

import math

import pandas as pd

_RTH_HOURS_PER_DAY = 6.5  # ~regular session length for US equities


def warmup_trading_days_from_strategy(strategy: dict) -> int:
    """Trading days of 5m history so 1h-resampled LazySwing + volume_breakout can run."""
    p = strategy.get("params") or {}
    st = int(strategy.get("supertrend_atr_period") or p.get("supertrend_atr_period", 10))
    min_hourly = st * 15

    scorer = strategy.get("scorer") or {}
    if scorer.get("type") == "volume_breakout":
        sp = scorer.get("params") or {}
        lw = int(sp.get("long_window", 100))
        min_hourly = max(min_hourly, lw)

    min_hourly = max(min_hourly, 51 + 12 + 5)

    days = math.ceil(min_hourly / _RTH_HOURS_PER_DAY) + 3
    return max(days, 5)


def warmup_range_start_day(last_day_yyyy_mm_dd: str, n_trading_days: int) -> str:
    """First calendar day of the window — n_trading_days B-days before last_day."""
    if n_trading_days <= 0:
        return last_day_yyyy_mm_dd
    d = pd.Timestamp(last_day_yyyy_mm_dd)
    start = d - pd.offsets.BDay(n_trading_days)
    return start.strftime("%Y-%m-%d")
