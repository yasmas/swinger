"""Alpaca REST API v2 ExchangeClient — US equity market data for paper trading.

Provides 5m OHLCV bars, latest trade price, and best bid/ask via the
Alpaca Data API v2.  Authentication uses env vars ALPACA_API_KEY and
ALPACA_API_SECRET (or explicit keys in the exchange config block).

Feed options:
  iex  — free, real-time IEX feed (~15-20% of consolidated volume)
  sip  — paid, full consolidated tape (SIP)

For paper trading the IEX feed is more than sufficient.
"""

import logging
import os
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from .base import ExchangeClient

logger = logging.getLogger(__name__)

DATA_BASE_URL = "https://data.alpaca.markets"

# Map internal interval strings → Alpaca timeframe strings
_TIMEFRAME_MAP = {
    "1m":  "1Min",
    "5m":  "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h":  "1Hour",
    "4h":  "4Hour",
    "1d":  "1Day",
}

# Milliseconds per interval (for computing close_time)
_INTERVAL_MS = {
    "1m":  60_000,
    "5m":  300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h":  3_600_000,
    "4h":  14_400_000,
    "1d":  86_400_000,
}


class AlpacaRestClient(ExchangeClient):
    """Alpaca REST API v2 implementation of ExchangeClient.

    Only market-data endpoints are used — no brokerage API key scope
    needed beyond basic market data access.

    Config keys (all optional; fall back to env vars):
        api_key   — Alpaca API key ID  (env: ALPACA_API_KEY)
        api_secret — Alpaca secret key (env: ALPACA_API_SECRET)
        feed      — "iex" (default, free) or "sip" (paid consolidated)
        request_timeout_seconds — HTTP timeout (default 10)
        max_retries — retry attempts on transient errors (default 3)
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.api_key = (
            config.get("api_key")
            or os.getenv("ALPACA_API_KEY", "")
        )
        self.api_secret = (
            config.get("api_secret")
            or os.getenv("ALPACA_API_SECRET", "")
        )
        self.feed = config.get("feed", "iex")
        self.base_url = DATA_BASE_URL
        self.timeout = config.get("request_timeout_seconds", 10)
        self.max_retries = config.get("max_retries", 3)

        self._session = requests.Session()
        self._session.headers.update({
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Accept": "application/json",
        })

    # ── Internal helpers ──────────────────────────────────────────────

    def _request(self, endpoint: str, params: dict) -> dict:
        url = f"{self.base_url}{endpoint}"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                if attempt == self.max_retries:
                    logger.error(
                        "Alpaca API (%s) failed after %d retries: %s",
                        url, self.max_retries, exc,
                    )
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "Alpaca API attempt %d/%d failed (%s), retrying in %ds",
                    attempt, self.max_retries, exc, wait,
                )
                time.sleep(wait)

    @staticmethod
    def _ms_to_rfc3339(ms: int) -> str:
        """Convert epoch-milliseconds to RFC 3339 UTC string."""
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _ts_to_ms(ts: str) -> int:
        """Convert an Alpaca RFC 3339 / ISO 8601 timestamp to epoch-ms."""
        return int(pd.Timestamp(ts).timestamp() * 1000)

    # ── ExchangeClient interface ──────────────────────────────────────

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars from Alpaca Data API v2.

        Handles pagination automatically via next_page_token so the
        caller always gets a contiguous block up to ``limit`` bars.
        Returns an empty DataFrame when no data is available (e.g.
        outside market hours, weekends, holidays).
        """
        timeframe = _TIMEFRAME_MAP.get(interval, interval)
        interval_ms = _INTERVAL_MS.get(interval, 300_000)

        params: dict = {
            "timeframe": timeframe,
            "limit": min(limit, 10_000),
            "feed": self.feed,
            "sort": "asc",
        }
        if start_time_ms is not None:
            params["start"] = self._ms_to_rfc3339(start_time_ms)
        if end_time_ms is not None:
            params["end"] = self._ms_to_rfc3339(end_time_ms)

        all_bars: list[dict] = []
        page_params = dict(params)

        while True:
            try:
                data = self._request(f"/v2/stocks/{symbol}/bars", page_params)
            except Exception:
                break

            bars = data.get("bars") or []
            all_bars.extend(bars)

            next_token = data.get("next_page_token")
            if not next_token or not bars:
                break
            # Pagination: replace start/end with page_token
            page_params = {k: v for k, v in params.items()
                           if k not in ("start", "end")}
            page_params["page_token"] = next_token

        if not all_bars:
            return pd.DataFrame(columns=[
                "open_time", "open", "high", "low", "close", "volume", "close_time",
            ])

        rows = []
        for bar in all_bars:
            open_ms = self._ts_to_ms(bar["t"])
            rows.append({
                "open_time":               open_ms,
                "open":                    float(bar["o"]),
                "high":                    float(bar["h"]),
                "low":                     float(bar["l"]),
                "close":                   float(bar["c"]),
                "volume":                  float(bar["v"]),
                "close_time":              open_ms + interval_ms - 1,
                "quote_asset_volume":      0,
                "number_of_trades":        int(bar.get("n", 0)),
                "taker_buy_base_volume":   0,
                "taker_buy_quote_volume":  0,
                "ignore":                  0,
            })

        df = pd.DataFrame(rows)
        df["open_time"] = df["open_time"].astype(int)
        df["close_time"] = df["close_time"].astype(int)
        return df.sort_values("open_time").reset_index(drop=True)

    def get_current_price(self, symbol: str) -> float:
        """Return the price of the most recent trade."""
        try:
            data = self._request(
                f"/v2/stocks/{symbol}/trades/latest",
                {"feed": self.feed},
            )
            return float(data["trade"]["p"])
        except Exception:
            # Fallback: mid-point of latest quote
            data = self._request(
                f"/v2/stocks/{symbol}/quotes/latest",
                {"feed": self.feed},
            )
            q = data["quote"]
            return (float(q["ap"]) + float(q["bp"])) / 2.0

    def get_best_bid_ask(self, symbol: str) -> dict:
        """Return the latest NBBO bid and ask."""
        data = self._request(
            f"/v2/stocks/{symbol}/quotes/latest",
            {"feed": self.feed},
        )
        q = data["quote"]
        return {
            "bid_price": float(q["bp"]),
            "bid_qty":   float(q.get("bs", 0)),
            "ask_price": float(q["ap"]),
            "ask_qty":   float(q.get("as", 0)),
        }
