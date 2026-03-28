"""Coinbase Advanced Trade API implementation of ExchangeClient.

Supports BTC-PERP-INTX perpetual futures and other Coinbase products.
Uses only public market-data endpoints (no API key required).
"""

import logging
import time

import pandas as pd
import requests

from .base import ExchangeClient

logger = logging.getLogger(__name__)

# Map Binance-style interval strings to (Coinbase granularity, seconds per bar)
INTERVAL_MAP = {
    "1m": ("ONE_MINUTE", 60),
    "5m": ("FIVE_MINUTE", 300),
    "15m": ("FIFTEEN_MINUTE", 900),
    "1h": ("ONE_HOUR", 3600),
    "6h": ("SIX_HOUR", 21600),
    "1d": ("ONE_DAY", 86400),
}

MAX_CANDLES_PER_REQUEST = 300

OHLCV_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]


class CoinbaseRestClient(ExchangeClient):
    """Coinbase Advanced Trade REST API client.

    Default product: BTC-PERP-INTX (perpetual futures on Coinbase International Exchange).
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.base_url = config.get("base_url", "https://api.coinbase.com")
        self.product_id = config.get("product_id", "BTC-PERP-INTX")
        self.timeout = config.get("request_timeout_seconds", 10)
        self.max_retries = config.get("max_retries", 3)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "swinger/1.0"

        # Future: authenticated trading endpoints
        self.api_key = config.get("api_key")
        self.api_secret = config.get("api_secret")

    def _request(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == self.max_retries:
                    logger.error("Coinbase API failed after %d retries: %s", self.max_retries, e)
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "Coinbase API attempt %d/%d failed (%s), retrying in %ds",
                    attempt, self.max_retries, e, wait,
                )
                time.sleep(wait)

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 300,
    ) -> pd.DataFrame:
        if interval not in INTERVAL_MAP:
            raise ValueError(f"Unsupported interval '{interval}'. Supported: {list(INTERVAL_MAP.keys())}")

        granularity, bar_seconds = INTERVAL_MAP[interval]
        bar_ms = bar_seconds * 1000

        product_id = self.product_id

        # If no time range, fetch the most recent `limit` candles
        if start_time_ms is None and end_time_ms is None:
            end_ts = int(time.time())
            start_ts = end_ts - (limit * bar_seconds)
            return self._fetch_candle_range(product_id, granularity, bar_seconds, bar_ms, start_ts, end_ts)

        # Convert ms → seconds
        start_ts = int(start_time_ms / 1000) if start_time_ms is not None else int(time.time()) - (limit * bar_seconds)
        end_ts = int(end_time_ms / 1000) if end_time_ms is not None else int(time.time())

        return self._fetch_candle_range(product_id, granularity, bar_seconds, bar_ms, start_ts, end_ts)

    def _fetch_candle_range(
        self,
        product_id: str,
        granularity: str,
        bar_seconds: int,
        bar_ms: int,
        start_ts: int,
        end_ts: int,
    ) -> pd.DataFrame:
        """Fetch candles with automatic pagination (max 300 per request)."""
        chunk_seconds = bar_seconds * MAX_CANDLES_PER_REQUEST
        all_candles = []

        chunk_start = start_ts
        while chunk_start < end_ts:
            chunk_end = min(chunk_start + chunk_seconds, end_ts)

            data = self._request(
                f"/api/v3/brokerage/market/products/{product_id}/candles",
                params={"granularity": granularity, "start": chunk_start, "end": chunk_end},
            )

            candles = data.get("candles", [])
            all_candles.extend(candles)

            chunk_start = chunk_end
            # Small delay between paginated requests to respect rate limits
            if chunk_start < end_ts:
                time.sleep(0.1)

        if not all_candles:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        # Parse candles: Coinbase fields are strings, timestamps are Unix seconds
        rows = []
        for c in all_candles:
            open_time = int(c["start"]) * 1000  # → ms
            rows.append({
                "open_time": open_time,
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
                "close_time": open_time + bar_ms - 1,
            })

        df = pd.DataFrame(rows)
        df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
        return df

    def get_current_price(self, symbol: str) -> float:
        data = self._request(f"/api/v3/brokerage/market/products/{self.product_id}")
        return float(data["price"])

    def get_best_bid_ask(self, symbol: str) -> dict:
        # best_bid_ask endpoint requires authentication.
        # When API keys are configured, use the authenticated endpoint.
        if self.api_key and self.api_secret:
            return self._get_authenticated_bid_ask()

        # Fallback: derive from product price with minimal synthetic spread.
        # BTC-PERP-INTX has $0.10 quote increment; use that as spread.
        price = self.get_current_price(symbol)
        half_spread = 0.05  # $0.05 each side
        return {
            "bid_price": price - half_spread,
            "bid_qty": 0.0,
            "ask_price": price + half_spread,
            "ask_qty": 0.0,
        }

    def _get_authenticated_bid_ask(self) -> dict:
        """Fetch real bid/ask via authenticated endpoint (requires API keys)."""
        data = self._request(
            "/api/v3/brokerage/best_bid_ask",
            params={"product_ids": self.product_id},
        )
        pricebooks = data.get("pricebooks", [])
        if not pricebooks:
            raise ValueError(f"No bid/ask data returned for {self.product_id}")

        book = pricebooks[0]
        bids = book.get("bids", [])
        asks = book.get("asks", [])

        return {
            "bid_price": float(bids[0]["price"]) if bids else 0.0,
            "bid_qty": float(bids[0]["size"]) if bids else 0.0,
            "ask_price": float(asks[0]["price"]) if asks else 0.0,
            "ask_qty": float(asks[0]["size"]) if asks else 0.0,
        }
