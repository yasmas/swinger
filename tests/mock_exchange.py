"""Mock exchange client that replays data from a CSV and supports controllable failures.

Used for integration testing gap-fill, backfill, and outage recovery scenarios
without hitting any real exchange APIs.

Usage:
    mock = MockExchangeClient("data/BTCUSDT-5m-2022-2024-combined.csv")
    mock.set_failing(True)   # simulate outage
    mock.set_failing(False)  # restore
"""

import pandas as pd

from exchange.base import ExchangeClient

FIVE_MIN_MS = 5 * 60 * 1000


class ExchangeUnavailable(Exception):
    """Raised when the mock exchange is in failure mode."""


class MockExchangeClient(ExchangeClient):
    """Replays OHLCV bars from a CSV file. Can be toggled to fail on demand."""

    def __init__(self, csv_path: str, symbol: str = "BTCUSDT"):
        self.symbol = symbol
        self._failing = False

        df = pd.read_csv(csv_path)
        df["open_time"] = df["open_time"].astype(int)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        self._data = df.sort_values("open_time").reset_index(drop=True)

    def set_failing(self, failing: bool):
        self._failing = failing

    def _raise_if_failing(self):
        if self._failing:
            raise ExchangeUnavailable("Mock exchange is simulating an outage")

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        self._raise_if_failing()

        mask = pd.Series(True, index=self._data.index)
        if start_time_ms is not None:
            mask &= self._data["open_time"] >= start_time_ms
        if end_time_ms is not None:
            mask &= self._data["open_time"] <= end_time_ms

        result = self._data[mask].head(limit).copy()

        if "close_time" not in result.columns and not result.empty:
            result["close_time"] = result["open_time"] + FIVE_MIN_MS - 1

        return result

    def get_current_price(self, symbol: str) -> float:
        self._raise_if_failing()
        return float(self._data.iloc[-1]["close"])

    def get_best_bid_ask(self, symbol: str) -> dict:
        self._raise_if_failing()
        price = float(self._data.iloc[-1]["close"])
        return {
            "bid_price": price * 0.9999,
            "bid_qty": 1.0,
            "ask_price": price * 1.0001,
            "ask_qty": 1.0,
        }
