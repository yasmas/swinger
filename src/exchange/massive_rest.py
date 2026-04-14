"""Massive.com (formerly Polygon.io) REST API ExchangeClient — US equity market data.

Provides 5m OHLCV bars including extended hours (pre-market 4 AM - after-hours
8 PM ET), latest trade price, and best bid/ask via the Polygon-compatible REST API.

Authentication uses env var MASSIVE_API_KEY or explicit api_key in config.
Polygon accepts **either** ``Authorization: Bearer <key>`` (what we use) **or**
the ``apiKey`` query parameter — not both required. We use the Bearer header so
the key does not appear in ``response.url`` / ``403 … for url:`` log lines.

**HTTP 403 / 401 on ``/v2/aggs/...``** usually means wrong host for your key
(``api.massive.com`` vs ``api.polygon.io``), wrong/expired key, or a plan that
does not include aggregates for that symbol. Retries are skipped for 401/403.
"""

import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .base import ExchangeClient

logger = logging.getLogger(__name__)

# Massive-issued keys expect api.massive.com; legacy Polygon-only keys may use
# ``exchange.base_url: https://api.polygon.io`` in bot YAML.
BASE_URL = "https://api.massive.com"

# Map internal interval strings → Massive/Polygon multiplier + timespan
_INTERVAL_MAP = {
    "1m":  (1, "minute"),
    "5m":  (5, "minute"),
    "15m": (15, "minute"),
    "30m": (30, "minute"),
    "1h":  (1, "hour"),
    "4h":  (4, "hour"),
    "1d":  (1, "day"),
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


class MassiveRestClient(ExchangeClient):
    """Massive.com (Polygon.io) REST API implementation of ExchangeClient.

    Config keys (all optional; fall back to env vars):
        api_key                 — Massive API key (env: MASSIVE_API_KEY)
        request_timeout_seconds — HTTP timeout (default 10)
        max_retries             — retry attempts on transient errors (default 3)
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.api_key = (
            config.get("api_key")
            or os.getenv("MASSIVE_API_KEY", "")
        )
        if not self.api_key:
            raise ValueError(
                "MassiveRestClient: missing MASSIVE_API_KEY. "
                "Set it in the .env file or as an environment variable."
            )

        self.base_url = (config.get("base_url") or BASE_URL).rstrip("/")
        self.timeout = config.get("request_timeout_seconds", 10)
        self.max_retries = config.get("max_retries", 2)

        self._session = requests.Session()
        # Polygon/Massive: Bearer is equivalent to ?apiKey=… on the URL.
        self._session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        })

    # ── Market hours ─────────────────────────────────────────────────

    _ET = ZoneInfo("US/Eastern")

    def is_market_open(self, ts_ms: int) -> bool:
        """Return True if ts_ms falls within US extended hours (4 AM–8 PM ET, Mon–Fri)."""
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=self._ET)
        return dt.weekday() < 5 and 4 <= dt.hour < 20

    # ── Internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _polygon_error_summary(resp: requests.Response) -> str:
        try:
            data = resp.json()
            return str(
                data.get("message")
                or data.get("error")
                or data.get("status")
                or data
            )[:400]
        except Exception:
            return (resp.text or "")[:400]

    def _request(self, url: str, params: dict | None = None) -> dict:
        """GET request with retry + exponential backoff.

        401/403 are **not** retried: they indicate bad credentials or a plan that
        does not allow the endpoint (common for stock aggregates on free/limited keys).
        """
        params = params or {}
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)

                if resp.status_code in (401, 403):
                    summary = self._polygon_error_summary(resp)
                    logger.error(
                        "Massive/Polygon HTTP %s — not retrying. %s "
                        "US stock aggregates (e.g. /v2/aggs/ticker/...) need a key with "
                        "appropriate Stocks / SIP access on your Massive or Polygon plan. "
                        "URL (truncated): %s",
                        resp.status_code,
                        summary,
                        url[:160],
                    )
                    resp.raise_for_status()

                if resp.status_code == 429:
                    if attempt == self.max_retries:
                        resp.raise_for_status()
                    wait = 2 ** attempt
                    logger.warning(
                        "Massive API rate limited (429), sleeping %ds then retry (same HTTP request).",
                        wait,
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.HTTPError as exc:
                sc = exc.response.status_code if exc.response is not None else None
                if sc in (401, 403):
                    raise
                last_exc = exc
                if attempt == self.max_retries:
                    logger.error(
                        "Massive API (%s) failed after %d retries: %s",
                        url, self.max_retries, exc,
                    )
                    raise
                wait = attempt
                logger.warning(
                    "Massive API attempt %d/%d failed (%s), sleeping %ds then retry (same HTTP request).",
                    attempt, self.max_retries, exc, wait,
                )
                time.sleep(wait)
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    logger.error(
                        "Massive API (%s) failed after %d retries: %s",
                        url, self.max_retries, exc,
                    )
                    raise
                wait = attempt
                logger.warning(
                    "Massive API attempt %d/%d failed (%s), sleeping %ds then retry (same HTTP request).",
                    attempt, self.max_retries, exc, wait,
                )
                time.sleep(wait)
        # Should not reach here, but guard against it
        raise requests.RequestException(
            f"Massive API ({url}) failed after {self.max_retries} retries: {last_exc}"
        )

    # ── ExchangeClient interface ──────────────────────────────────────

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch OHLCV bars from Massive/Polygon aggregates endpoint.

        Handles pagination via next_url. Returns contiguous bars up to limit.

        Do **not** short-circuit on Eastern session clock vs ``start_time_ms``: range
        requests use ``start_day`` at **midnight UTC**, which often maps to Sunday
        evening or off-hours in US/Eastern even for valid historical windows (e.g.
        Monday RTH bars). The aggregates API must still be called for those ranges.
        """
        if interval not in _INTERVAL_MAP:
            raise ValueError(f"Unsupported interval: {interval}")

        multiplier, timespan = _INTERVAL_MAP[interval]
        interval_ms = _INTERVAL_MS[interval]

        # API accepts ms timestamps or date strings for from/to
        from_val = start_time_ms if start_time_ms is not None else "2020-01-01"
        to_val = end_time_ms if end_time_ms is not None else int(time.time() * 1000)

        url = (
            f"{self.base_url}/v2/aggs/ticker/{symbol}"
            f"/range/{multiplier}/{timespan}/{from_val}/{to_val}"
        )
        params = {
            "sort": "asc",
            "limit": min(limit, 50_000),
            "adjusted": "true",
        }

        all_bars: list[dict] = []
        first_page = True

        while True:
            try:
                data = self._request(url, params)
            except Exception:
                if first_page:
                    raise  # let caller handle API errors
                break  # partial data is better than nothing on pagination

            first_page = False
            results = data.get("results") or []
            all_bars.extend(results)

            if len(all_bars) >= limit:
                all_bars = all_bars[:limit]
                break

            next_url = data.get("next_url")
            if not next_url or not results:
                break
            # next_url is a full URL; use it directly
            url = next_url
            params = {}

        if not all_bars:
            return pd.DataFrame(columns=[
                "open_time", "open", "high", "low", "close", "volume", "close_time",
            ])

        rows = []
        for bar in all_bars:
            open_ms = int(bar["t"])
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

    def _get_snapshot(self, symbol: str) -> dict:
        """Fetch ticker snapshot (works on Starter plan).

        Caches for 5 seconds to avoid duplicate API calls when
        get_current_price and get_best_bid_ask are called back-to-back.
        """
        now = time.monotonic()
        if (
            hasattr(self, "_snap_cache")
            and self._snap_cache[0] == symbol
            and now - self._snap_cache[1] < 5.0
        ):
            return self._snap_cache[2]

        data = self._request(
            f"{self.base_url}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}",
        )
        snap = data.get("ticker", {})
        self._snap_cache = (symbol, now, snap)
        return snap

    def get_current_price(self, symbol: str) -> float:
        """Return the latest price from the ticker snapshot.

        Prefers min.c (latest 1-min bar close, updates in real-time)
        over day.c (regular-session close, stale during extended hours).
        """
        snap = self._get_snapshot(symbol)
        minute = snap.get("min", {})
        if minute.get("c") is not None:
            return float(minute["c"])
        day = snap.get("day", {})
        return float(day["c"])

    def get_best_bid_ask(self, symbol: str) -> dict:
        """Return bid/ask approximated from the ticker snapshot.

        Uses min.c (latest 1-min bar close) as the price. The Starter
        plan doesn't include the quotes endpoint, so bid == ask (zero
        spread). Sufficient for paper trading with liquid stocks.
        """
        snap = self._get_snapshot(symbol)
        minute = snap.get("min", {})
        price = float(minute["c"]) if minute.get("c") is not None else float(snap.get("day", {}).get("c", 0))
        return {
            "bid_price": price,
            "bid_qty":   0.0,
            "ask_price": price,
            "ask_qty":   0.0,
        }
