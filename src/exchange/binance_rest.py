import logging
import time

import pandas as pd
import requests

from .base import ExchangeClient

logger = logging.getLogger(__name__)

OHLCV_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_asset_volume", "number_of_trades",
    "taker_buy_base_volume", "taker_buy_quote_volume", "ignore",
]


class BinanceRestClient(ExchangeClient):
    """Binance REST API implementation of ExchangeClient.

    Uses only public market-data endpoints (no API key required).
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.base_url = config.get("base_url", "https://api.binance.us")
        self.timeout = config.get("request_timeout_seconds", 10)
        self.max_retries = config.get("max_retries", 3)
        self._session = requests.Session()

    def _request(self, endpoint: str, params: dict) -> dict | list:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == self.max_retries:
                    logger.error("Binance API failed after %d retries: %s", self.max_retries, e)
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "Binance API attempt %d/%d failed (%s), retrying in %ds",
                    attempt, self.max_retries, e, wait,
                )
                time.sleep(wait)

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time_ms is not None:
            params["startTime"] = start_time_ms
        if end_time_ms is not None:
            params["endTime"] = end_time_ms

        data = self._request("/api/v3/klines", params)

        if not data:
            return pd.DataFrame(columns=OHLCV_COLUMNS[:7])

        df = pd.DataFrame(data, columns=OHLCV_COLUMNS)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["open_time"] = df["open_time"].astype(int)
        df["close_time"] = df["close_time"].astype(int)

        df = df[["open_time", "open", "high", "low", "close", "volume", "close_time"]]
        return df.sort_values("open_time").reset_index(drop=True)

    def get_current_price(self, symbol: str) -> float:
        data = self._request("/api/v3/ticker/price", {"symbol": symbol})
        return float(data["price"])

    def get_best_bid_ask(self, symbol: str) -> dict:
        data = self._request("/api/v3/ticker/bookTicker", {"symbol": symbol})
        return {
            "bid_price": float(data["bidPrice"]),
            "bid_qty": float(data["bidQty"]),
            "ask_price": float(data["askPrice"]),
            "ask_qty": float(data["askQty"]),
        }
