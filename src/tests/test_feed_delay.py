"""Tests for delayed feed horizon (feed_delay_minutes / _feed_now)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from trading.data_manager import (
    DataManager,
    FIVE_MIN_MS,
    feed_delay_minutes_from_config,
)


def test_feed_delay_minutes_from_config_exchange_first() -> None:
    cfg = {
        "exchange": {"type": "massive", "feed_delay_minutes": 12},
        "bot": {"feed_delay_minutes": 99},
    }
    assert feed_delay_minutes_from_config(cfg) == 12


def test_feed_delay_minutes_from_config_bot_fallback() -> None:
    cfg = {"exchange": {"type": "massive"}, "bot": {"feed_delay_minutes": 7}}
    assert feed_delay_minutes_from_config(cfg) == 7


def test_feed_delay_minutes_from_config_invalid_defaults_zero() -> None:
    assert feed_delay_minutes_from_config({"exchange": {"feed_delay_minutes": "x"}}) == 0
    assert feed_delay_minutes_from_config({}) == 0


class _RecordingExchange:
    """Minimal exchange: equities session always open; records fetch_ohlcv args."""

    def __init__(self) -> None:
        self.fetch_calls: list[dict] = []

    def is_market_open(self, ts_ms: int) -> bool:
        return True

    def fetch_ohlcv(self, symbol, interval, start_time_ms=None, end_time_ms=None, limit=1000):
        self.fetch_calls.append(
            {
                "symbol": symbol,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
            }
        )
        o = int(start_time_ms)
        return pd.DataFrame(
            [
                {
                    "open_time": o,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "close_time": o + FIVE_MIN_MS - 1,
                    "quote_asset_volume": 0,
                    "number_of_trades": 1,
                    "taker_buy_base_volume": 0,
                    "taker_buy_quote_volume": 0,
                    "ignore": 0,
                }
            ]
        )


def _last_closed_start_ms(now_ms: int) -> int:
    return ((now_ms // FIVE_MIN_MS) - 1) * FIVE_MIN_MS


def test_fetch_and_append_5m_uses_feed_clock_for_bar_window() -> None:
    wall = datetime(2024, 6, 1, 12, 17, 30, tzinfo=timezone.utc)
    wall_ms = int(wall.timestamp() * 1000)
    delay_min = 15
    feed_ms = wall_ms - delay_min * 60 * 1000
    expected_start = _last_closed_start_ms(feed_ms)

    with tempfile.TemporaryDirectory() as tmp:
        ex = _RecordingExchange()
        dm = DataManager(
            ex,
            "TEST",
            tmp,
            warm_up_hours=1,
            now_fn=lambda: wall,
            feed_delay_minutes=delay_min,
        )
        bar = dm.fetch_and_append_5m()
        assert bar is not None
        assert len(ex.fetch_calls) == 1
        assert ex.fetch_calls[0]["start_time_ms"] == expected_start
        assert ex.fetch_calls[0]["limit"] == 1

    no_delay_start = _last_closed_start_ms(wall_ms)
    assert expected_start != no_delay_start


def test_feed_delay_zero_matches_wall_bar_window() -> None:
    wall = datetime(2024, 6, 1, 12, 17, 30, tzinfo=timezone.utc)
    wall_ms = int(wall.timestamp() * 1000)
    expected_start = _last_closed_start_ms(wall_ms)

    with tempfile.TemporaryDirectory() as tmp:
        ex = _RecordingExchange()
        dm = DataManager(
            ex,
            "TEST",
            tmp,
            warm_up_hours=1,
            now_fn=lambda: wall,
            feed_delay_minutes=0,
        )
        dm.fetch_and_append_5m()
        assert ex.fetch_calls[0]["start_time_ms"] == expected_start
