from abc import ABC, abstractmethod

import pandas as pd


class ExchangeClient(ABC):
    """Abstract interface for exchange market-data operations.

    Implementations map these generic methods to exchange-specific APIs
    (e.g., Binance REST, Kraken, Bybit). Only public endpoints are used —
    no API key required for paper trading.
    """

    @abstractmethod
    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch OHLCV candlestick bars.

        Args:
            symbol: Trading pair (e.g., "BTCUSDT").
            interval: Bar interval (e.g., "1m", "5m", "1h").
            start_time_ms: Start time in milliseconds (inclusive). None = exchange default.
            end_time_ms: End time in milliseconds (inclusive). None = exchange default.
            limit: Maximum bars to return (exchange-specific cap).

        Returns:
            DataFrame with columns: open_time (int ms), open, high, low, close,
            volume, close_time (int ms). Sorted by open_time ascending.
        """

    @abstractmethod
    def get_current_price(self, symbol: str) -> float:
        """Get the last traded price for a symbol."""

    @abstractmethod
    def get_best_bid_ask(self, symbol: str) -> dict:
        """Get the best bid/ask from the order book.

        Returns:
            dict with keys: bid_price (float), bid_qty (float),
            ask_price (float), ask_qty (float).
        """
