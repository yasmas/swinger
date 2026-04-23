"""Coinbase Advanced Trade API implementation of ExchangeClient.

Supports BTC-PERP-INTX perpetual futures and other Coinbase products.
Public endpoints require no API key; trading endpoints require CDP API keys.
"""

import logging
import os
import secrets
import time
from pathlib import Path
from uuid import uuid4

import jwt
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
    Supports both public market-data and authenticated trading endpoints.
    """

    def __init__(self, config: dict | None = None):
        config = config or {}
        self.base_url = config.get("base_url", "https://api.coinbase.com")
        self.product_id = config.get("product_id", "BTC-PERP-INTX")
        self.timeout = config.get("request_timeout_seconds", 10)
        self.max_retries = config.get("max_retries", 3)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "swinger/1.0"

        # Authenticated trading: CDP API keys
        # Supported formats for api_secret:
        #   - Inline PEM string
        #   - ${ENV_VAR} reference
        #   - file:/path/to/key.pem  (reads PEM from file)
        #   - file:/path/to/cdp_api_key.json  (Coinbase JSON download)
        self.api_key = self._resolve_env(config.get("api_key"))
        raw_secret = self._resolve_env(config.get("api_secret"))
        self.api_key, self.api_secret = self._load_credentials(self.api_key, raw_secret)

        # Cached state — populated on first access
        self._product_specs: dict | None = None
        self._portfolio_uuid: str | None = None

    @staticmethod
    def _load_credentials(api_key: str | None, api_secret: str | None) -> tuple[str | None, str | None]:
        """Load API credentials, supporting file paths and Coinbase JSON format.

        api_secret can be:
          - A PEM string (inline or from env var)
          - "file:/path/to/key.pem" — reads PEM directly from file
          - "file:/path/to/cdp_api_key.json" — Coinbase JSON download format
            (also extracts api_key name from the JSON if not set)
        """
        if not api_secret:
            return api_key, api_secret

        # File-based loading
        if api_secret.startswith("file:"):
            import json as _json
            filepath = api_secret[5:]
            path = Path(filepath)
            if not path.exists():
                raise FileNotFoundError(f"API secret file not found: {filepath}")

            content = path.read_text()

            if path.suffix == ".json":
                # Coinbase CDP JSON download format:
                # {"name": "organizations/.../apiKeys/...", "privateKey": "-----BEGIN EC..."}
                data = _json.loads(content)
                api_secret = data.get("privateKey", "")
                # Use the key name from JSON if not explicitly set
                if not api_key:
                    api_key = data.get("name", "")
                logger.info("Loaded API credentials from JSON: %s", filepath)
            else:
                # Plain PEM file
                api_secret = content
                logger.info("Loaded API secret from PEM file: %s", filepath)

        return api_key, api_secret

    @staticmethod
    def _resolve_env(value: str | None) -> str | None:
        """Resolve ${ENV_VAR} references in config values."""
        if value and value.startswith("${") and value.endswith("}"):
            value = os.environ.get(value[2:-1])
        return value


    def _build_jwt(self, method: str, endpoint: str) -> str:
        """Build a JWT bearer token for authenticated requests."""
        if not self.api_key or not self.api_secret:
            raise RuntimeError("API key and secret required for authenticated requests")

        uri = f"{method} api.coinbase.com{endpoint}"
        now = int(time.time())

        payload = {
            "sub": self.api_key,
            "iss": "cdp",
            "aud": ["cdp_service"],
            "nbf": now,
            "exp": now + 120,
            "uri": uri,
        }
        headers = {
            "kid": self.api_key,
            "nonce": secrets.token_hex(16),
            "typ": "JWT",
        }

        # api_secret is an EC private key in PEM format (ES256)
        return jwt.encode(payload, self.api_secret, algorithm="ES256", headers=headers)

    def _request(self, endpoint: str, params: dict | None = None) -> dict:
        """Unauthenticated GET request with retries."""
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

    def _auth_request(
        self, method: str, endpoint: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Authenticated request with JWT bearer token and retries.

        JWT signing and auth errors fail immediately (no retry).
        Only network/transient errors are retried.
        """
        url = f"{self.base_url}{endpoint}"
        logger.debug("API call: %s %s params=%s body_keys=%s",
                      method, endpoint, params,
                      list(json_body.keys()) if json_body else None)

        # Build JWT outside the retry loop — key/signing errors should fail fast
        token = self._build_jwt(method, endpoint)
        headers = {"Authorization": f"Bearer {token}"}

        for attempt in range(1, self.max_retries + 1):
            try:
                if method == "GET":
                    resp = self._session.get(
                        url, params=params, headers=headers, timeout=self.timeout,
                    )
                elif method == "POST":
                    resp = self._session.post(
                        url, json=json_body, headers=headers, timeout=self.timeout,
                    )
                elif method == "DELETE":
                    resp = self._session.delete(
                        url, json=json_body, headers=headers, timeout=self.timeout,
                    )
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                # Don't retry auth/permission errors
                if resp.status_code in (401, 403):
                    logger.error(
                        "Coinbase auth error: %s %s → HTTP %d: %s",
                        method, endpoint, resp.status_code, resp.text,
                    )
                    resp.raise_for_status()

                resp.raise_for_status()
                data = resp.json()
                logger.debug("API response: %s %s → HTTP %d (%d bytes)",
                             method, endpoint, resp.status_code, len(resp.content))
                return data
            except requests.exceptions.HTTPError:
                # Auth/server errors already logged; don't retry
                raise
            except (requests.RequestException, ValueError) as e:
                if attempt == self.max_retries:
                    logger.error(
                        "Coinbase API %s %s failed after %d retries: %s",
                        method, endpoint, self.max_retries, e,
                    )
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
        data = self._auth_request(
            "GET", "/api/v3/brokerage/best_bid_ask",
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

    # ── Product Specs ─────────────────────────────────────────────────

    def get_product_specs(self) -> dict:
        """Fetch and cache product specifications (increments, tick size).

        Returns:
            dict with keys: base_increment (float), quote_increment (float),
            product_id (str), product_type (str).
        """
        if self._product_specs is not None:
            return self._product_specs

        data = self._request(f"/api/v3/brokerage/market/products/{self.product_id}")

        fpd = data.get("future_product_details", {})
        contract_size = float(fpd.get("contract_size", "1"))

        # price_increment is the real tick size for orders (not quote_increment)
        price_increment = float(data.get("price_increment", "0") or
                                data.get("quote_increment", "1"))

        self._product_specs = {
            "product_id": data.get("product_id", self.product_id),
            "product_type": data.get("product_type", ""),
            "base_increment": float(data.get("base_increment", "0.0001")),
            "quote_increment": price_increment,
            "base_min_size": float(data.get("base_min_size", "0.0001")),
            "base_max_size": float(data.get("base_max_size", "1000")),
            # Futures-specific
            "contract_size": contract_size,
            "contract_root_unit": fpd.get("contract_root_unit", ""),
            "contract_expiry": fpd.get("contract_expiry", ""),
            "venue": fpd.get("venue", ""),
        }

        logger.info(
            "Product specs for %s: base_increment=%s, price_increment=$%s, "
            "contract_size=%s %s, expiry=%s",
            self.product_id,
            self._product_specs["base_increment"],
            price_increment,
            contract_size,
            self._product_specs["contract_root_unit"],
            self._product_specs["contract_expiry"],
        )
        return self._product_specs

    # ── Trading ───────────────────────────────────────────────────────

    def place_limit_order_gtc(
        self, side: str, base_size: float, limit_price: float, post_only: bool = True,
    ) -> dict:
        """Place a GTC limit order.

        Args:
            side: "BUY" or "SELL".
            base_size: Quantity in base currency (e.g. BTC).
            limit_price: Limit price in quote currency (e.g. USD).
            post_only: If True, order is rejected if it would cross the book.

        Returns:
            dict with keys: order_id, client_order_id, side, product_id.
        """
        client_order_id = str(uuid4())
        body = {
            "client_order_id": client_order_id,
            "product_id": self.product_id,
            "side": side,
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": str(base_size),
                    "limit_price": str(limit_price),
                    "post_only": post_only,
                }
            },
        }

        data = self._auth_request("POST", "/api/v3/brokerage/orders", json_body=body)

        if not data.get("success"):
            error = data.get("error_response", data)
            raise RuntimeError(f"Order placement failed: {error}")

        result = data["success_response"]
        logger.info(
            "Placed GTC limit %s %s %.6f @ %.2f (order_id=%s, post_only=%s)",
            side, self.product_id, base_size, limit_price,
            result["order_id"], post_only,
        )
        return {
            "order_id": result["order_id"],
            "client_order_id": client_order_id,
            "side": side,
            "product_id": result.get("product_id", self.product_id),
        }

    def place_market_order_ioc(self, side: str, base_size: float) -> dict:
        """Place an IOC market order (immediate fill or cancel).

        Args:
            side: "BUY" or "SELL".
            base_size: Quantity in base currency.

        Returns:
            dict with keys: order_id, client_order_id, side, product_id.
        """
        client_order_id = str(uuid4())
        body = {
            "client_order_id": client_order_id,
            "product_id": self.product_id,
            "side": side,
            "order_configuration": {
                "market_market_ioc": {
                    "base_size": str(base_size),
                }
            },
        }

        data = self._auth_request("POST", "/api/v3/brokerage/orders", json_body=body)

        if not data.get("success"):
            error = data.get("error_response", data)
            raise RuntimeError(f"Market order failed: {error}")

        result = data["success_response"]
        logger.info(
            "Placed market IOC %s %s %.6f (order_id=%s)",
            side, self.product_id, base_size, result["order_id"],
        )
        return {
            "order_id": result["order_id"],
            "client_order_id": client_order_id,
            "side": side,
            "product_id": result.get("product_id", self.product_id),
        }

    def get_order(self, order_id: str) -> dict:
        """Get order status and fill details.

        Returns:
            dict with keys: order_id, status, side, product_id,
            filled_size, filled_value, average_filled_price, total_fees,
            completion_percentage.
        """
        data = self._auth_request(
            "GET", f"/api/v3/brokerage/orders/historical/{order_id}",
        )
        order = data.get("order", data)
        result = {
            "order_id": order.get("order_id", order_id),
            "status": order.get("status", "UNKNOWN"),
            "side": order.get("side", ""),
            "product_id": order.get("product_id", ""),
            "filled_size": float(order.get("filled_size", "0")),
            "filled_value": float(order.get("filled_value", "0")),
            "average_filled_price": float(order.get("average_filled_price", "0")),
            "total_fees": float(order.get("total_fees", "0")),
            "completion_percentage": float(order.get("completion_percentage", "0")),
            "created_time": order.get("created_time", ""),
        }
        logger.info(
            "Order %s: status=%s, filled=%.6f @ avg=%.2f, fees=%.4f, completion=%.0f%%",
            order_id, result["status"], result["filled_size"],
            result["average_filled_price"], result["total_fees"],
            result["completion_percentage"],
        )
        return result

    def edit_order(self, order_id: str, new_price: float, new_size: float | None = None) -> dict:
        """Edit (re-price) a pending GTC limit order atomically.

        Args:
            order_id: The order to edit.
            new_price: New limit price.
            new_size: New size (optional, keeps current size if None).

        Returns:
            dict with success status.
        """
        body = {
            "order_id": order_id,
            "price": str(new_price),
        }
        if new_size is not None:
            body["size"] = str(new_size)

        data = self._auth_request("POST", "/api/v3/brokerage/orders/edit", json_body=body)

        success = data.get("success", False)
        if not success:
            errors = data.get("errors", [])
            error_msg = errors[0].get("edit_failure_reason", str(errors)) if errors else str(data)
            raise RuntimeError(f"Order edit failed: {error_msg}")

        logger.info("Edited order %s → price=%.2f", order_id, new_price)
        return data

    def cancel_orders(self, order_ids: list[str]) -> list[dict]:
        """Cancel one or more orders.

        Returns:
            List of result dicts with success/failure per order.
        """
        body = {"order_ids": order_ids}
        data = self._auth_request("POST", "/api/v3/brokerage/orders/batch_cancel", json_body=body)

        results = data.get("results", [])
        for r in results:
            if r.get("success"):
                logger.info("Cancelled order %s", r.get("order_id"))
            else:
                logger.warning("Failed to cancel order %s: %s", r.get("order_id"), r.get("failure_reason"))

        return results

    def get_account_balance(self, currency: str = "USDC") -> dict:
        """Get account balance for a currency.

        Returns:
            dict with keys: available, hold, total.
        """
        data = self._auth_request("GET", "/api/v3/brokerage/accounts")
        accounts = data.get("accounts", [])

        for acct in accounts:
            if acct.get("currency") == currency:
                available = float(acct.get("available_balance", {}).get("value", "0"))
                hold = float(acct.get("hold", {}).get("value", "0"))
                result = {
                    "currency": currency,
                    "available": available,
                    "hold": hold,
                    "total": available + hold,
                }
                logger.info(
                    "Account balance %s: available=%.2f, hold=%.2f, total=%.2f",
                    currency, available, hold, available + hold,
                )
                return result

        raise ValueError(f"No account found for currency {currency}")

    def get_all_account_balances(self) -> list[dict]:
        """Get all account balances.

        Returns:
            List of dicts with keys: currency, available, hold, total.
        """
        data = self._auth_request("GET", "/api/v3/brokerage/accounts")
        accounts = data.get("accounts", [])

        results = []
        for acct in accounts:
            available = float(acct.get("available_balance", {}).get("value", "0"))
            hold = float(acct.get("hold", {}).get("value", "0"))
            results.append({
                "currency": acct.get("currency", "???"),
                "available": available,
                "hold": hold,
                "total": available + hold,
            })

        return results

    # ── CFM Futures Balance & Positions ─────────────────────────────

    def get_cfm_balance(self) -> dict:
        """Get CFM (Coinbase Financial Markets) futures balance summary.

        Returns:
            dict with keys: buying_power, total_usd_balance, cbi_usd_balance,
            cfm_usd_balance, total_open_orders_hold_amount, unrealized_pnl,
            daily_realized_pnl, available_margin, initial_margin.
        """
        data = self._auth_request("GET", "/api/v3/brokerage/cfm/balance_summary")
        bs = data.get("balance_summary", {})

        result = {
            "buying_power": float(bs.get("futures_buying_power", {}).get("value", "0")),
            "total_usd_balance": float(bs.get("total_usd_balance", {}).get("value", "0")),
            "cbi_usd_balance": float(bs.get("cbi_usd_balance", {}).get("value", "0")),
            "cfm_usd_balance": float(bs.get("cfm_usd_balance", {}).get("value", "0")),
            "total_open_orders_hold_amount": float(
                bs.get("total_open_orders_hold_amount", {}).get("value", "0")
            ),
            "unrealized_pnl": float(bs.get("unrealized_pnl", {}).get("value", "0")),
            "daily_realized_pnl": float(bs.get("daily_realized_pnl", {}).get("value", "0")),
            "available_margin": float(bs.get("available_margin", {}).get("value", "0")),
            "initial_margin": float(bs.get("initial_margin", {}).get("value", "0")),
        }

        logger.debug(
            "CFM balance: buying_power=$%.2f, total=$%.2f, cfm=$%.2f, cbi=$%.2f, "
            "holds=$%.2f, uPnL=$%.2f, realized_today=$%.2f, avail_margin=$%.2f, "
            "init_margin=$%.2f",
            result["buying_power"], result["total_usd_balance"],
            result["cfm_usd_balance"], result["cbi_usd_balance"],
            result["total_open_orders_hold_amount"],
            result["unrealized_pnl"], result["daily_realized_pnl"],
            result["available_margin"], result["initial_margin"],
        )
        return result

    def get_cfm_positions(self) -> list[dict]:
        """Get all open CFM futures positions.

        Returns:
            List of position dicts with keys: product_id, side, number_of_contracts,
            avg_entry_price, current_price, unrealized_pnl, expiration_time.
        """
        data = self._auth_request("GET", "/api/v3/brokerage/cfm/positions")

        def _parse_money(field):
            """Parse a money field that may be a string or {"value": "..."} dict."""
            if isinstance(field, dict):
                return float(field.get("value", "0"))
            return float(field) if field else 0.0

        positions = []
        for pos in data.get("positions", []):
            number_of_contracts = int(pos.get("number_of_contracts", "0"))
            if number_of_contracts == 0:
                continue

            side = pos.get("side", "UNKNOWN")

            positions.append({
                "product_id": pos.get("product_id", ""),
                "side": "LONG" if side == "LONG" else "SHORT",
                "number_of_contracts": number_of_contracts,
                "avg_entry_price": _parse_money(pos.get("avg_entry_price", "0")),
                "current_price": _parse_money(pos.get("current_price", "0")),
                "unrealized_pnl": _parse_money(pos.get("unrealized_pnl", "0")),
                "expiration_time": pos.get("expiration_time", ""),
            })

        logger.debug("CFM positions: %d open", len(positions))
        for p in positions:
            logger.debug(
                "  %s %s: %d contracts @ avg=%.2f, current=%.2f, uPnL=$%.2f",
                p["side"], p["product_id"], p["number_of_contracts"],
                p["avg_entry_price"], p["current_price"], p["unrealized_pnl"],
            )
        return positions

    # ── Portfolio (legacy INTX — kept for reference) ──────────────────

    def get_portfolio_uuid(self) -> str:
        """Fetch and cache the default portfolio UUID."""
        if self._portfolio_uuid is not None:
            return self._portfolio_uuid

        data = self._auth_request("GET", "/api/v3/brokerage/portfolios")
        portfolios = data.get("portfolios", [])

        if not portfolios:
            raise ValueError("No portfolios found on this account")

        # Use the default portfolio (or the first one)
        for p in portfolios:
            if p.get("type") == "DEFAULT" or p.get("deleted") is False:
                self._portfolio_uuid = p["uuid"]
                logger.info("Portfolio UUID: %s (name=%s, type=%s)",
                            p["uuid"], p.get("name"), p.get("type"))
                return self._portfolio_uuid

        # Fallback to first
        self._portfolio_uuid = portfolios[0]["uuid"]
        logger.info("Portfolio UUID (fallback): %s", self._portfolio_uuid)
        return self._portfolio_uuid

    def get_position(self, symbol: str | None = None) -> dict | None:
        """Get the open position for a symbol (defaults to self.product_id).

        Returns:
            dict with keys: symbol, side, size, avg_entry_price, unrealized_pnl,
            notional, mark_price. Returns None if no position (size == 0).
        """
        symbol = symbol or self.product_id
        portfolio_uuid = self.get_portfolio_uuid()

        data = self._auth_request(
            "GET",
            f"/api/v3/brokerage/intx/positions/{portfolio_uuid}/{symbol}",
        )

        pos = data.get("position", data)
        net_size = float(pos.get("net_size", "0"))

        if net_size == 0:
            logger.info("Position %s: flat (no position)", symbol)
            return None

        side_raw = pos.get("position_side", "")
        if "LONG" in side_raw:
            side = "LONG"
        elif "SHORT" in side_raw:
            side = "SHORT"
        else:
            side = "LONG" if net_size > 0 else "SHORT"

        entry_vwap = pos.get("entry_vwap", {})
        unrealized_pnl = pos.get("unrealized_pnl", {})
        position_notional = pos.get("position_notional", {})
        mark_price = pos.get("mark_price", {})

        result = {
            "symbol": symbol,
            "side": side,
            "size": abs(net_size),
            "net_size": net_size,
            "avg_entry_price": float(entry_vwap.get("value", "0")),
            "unrealized_pnl": float(unrealized_pnl.get("value", "0")),
            "notional": float(position_notional.get("value", "0")),
            "mark_price": float(mark_price.get("value", "0")),
        }

        logger.info(
            "Position %s: %s %.6f @ avg=%.2f, mark=%.2f, uPnL=$%.2f, notional=$%.2f",
            symbol, side, result["size"], result["avg_entry_price"],
            result["mark_price"], result["unrealized_pnl"], result["notional"],
        )
        return result

    def get_all_positions(self) -> list[dict]:
        """Get all open positions.

        Returns:
            List of position dicts (same format as get_position).
        """
        portfolio_uuid = self.get_portfolio_uuid()

        data = self._auth_request(
            "GET",
            f"/api/v3/brokerage/intx/positions/{portfolio_uuid}",
        )

        positions = []
        for pos in data.get("positions", []):
            net_size = float(pos.get("net_size", "0"))
            if net_size == 0:
                continue

            side_raw = pos.get("position_side", "")
            if "LONG" in side_raw:
                side = "LONG"
            elif "SHORT" in side_raw:
                side = "SHORT"
            else:
                side = "LONG" if net_size > 0 else "SHORT"

            entry_vwap = pos.get("entry_vwap", {})
            unrealized_pnl = pos.get("unrealized_pnl", {})
            position_notional = pos.get("position_notional", {})
            mark_price = pos.get("mark_price", {})

            positions.append({
                "symbol": pos.get("symbol", ""),
                "side": side,
                "size": abs(net_size),
                "net_size": net_size,
                "avg_entry_price": float(entry_vwap.get("value", "0")),
                "unrealized_pnl": float(unrealized_pnl.get("value", "0")),
                "notional": float(position_notional.get("value", "0")),
                "mark_price": float(mark_price.get("value", "0")),
            })

        logger.info("All positions: %d open", len(positions))
        for p in positions:
            logger.info(
                "  %s %s %.6f @ %.2f, uPnL=$%.2f",
                p["side"], p["symbol"], p["size"],
                p["avg_entry_price"], p["unrealized_pnl"],
            )
        return positions
