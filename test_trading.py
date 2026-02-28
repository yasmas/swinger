#!/usr/bin/env python3
"""Test script for Binance trading: buy, sell, short, cover with limit orders at mid price.

This script:
1. Queries bid/ask spread
2. Places a limit BUY order at mid price for $100 worth of BTC
3. Polls until filled (or timeout), then places a SELL order
4. Opens a SHORT position for $50 worth (margin trade)
5. Covers the short

Requires API keys in environment variables:
    BINANCE_API_KEY
    BINANCE_API_SECRET

For testnet, also set:
    BINANCE_TESTNET=1

Usage:
    # Dry run (no actual orders)
    python test_trading.py --dry-run

    # Real trading (requires API keys)
    BINANCE_API_KEY=xxx BINANCE_API_SECRET=yyy python test_trading.py
"""

import argparse
import hashlib
import hmac
import logging
import os
import sys
import time
from urllib.parse import urlencode

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SYMBOL = "BTCUSDT"
POLL_INTERVAL = 10
ORDER_TIMEOUT = 120


class BinanceTradingClient:
    """Binance authenticated trading client."""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        if testnet:
            self.base_url = "https://testnet.binance.vision"
        else:
            self.base_url = "https://api.binance.us"
        self._session = requests.Session()
        self._session.headers["X-MBX-APIKEY"] = api_key

    def _sign(self, params: dict) -> dict:
        """Add timestamp and signature to params."""
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _request(self, method: str, endpoint: str, params: dict, signed: bool = True) -> dict:
        """Make an API request."""
        if signed:
            params = self._sign(params)
        url = f"{self.base_url}{endpoint}"
        try:
            if method == "GET":
                resp = self._session.get(url, params=params, timeout=10)
            elif method == "POST":
                resp = self._session.post(url, params=params, timeout=10)
            elif method == "DELETE":
                resp = self._session.delete(url, params=params, timeout=10)
            else:
                raise ValueError(f"Unknown method: {method}")
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error("API request failed: %s", e)
            if hasattr(e, "response") and e.response is not None:
                logger.error("Response: %s", e.response.text)
            raise

    def get_best_bid_ask(self, symbol: str) -> dict:
        """Get best bid/ask (public endpoint, no signature needed)."""
        data = self._request("GET", "/api/v3/ticker/bookTicker", {"symbol": symbol}, signed=False)
        return {
            "bid": float(data["bidPrice"]),
            "ask": float(data["askPrice"]),
        }

    def get_exchange_info(self, symbol: str) -> dict:
        """Get symbol trading rules."""
        data = self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol}, signed=False)
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                filters = {f["filterType"]: f for f in s["filters"]}
                lot_size = filters.get("LOT_SIZE", {})
                price_filter = filters.get("PRICE_FILTER", {})
                return {
                    "min_qty": float(lot_size.get("minQty", 0)),
                    "step_size": float(lot_size.get("stepSize", 0.00001)),
                    "tick_size": float(price_filter.get("tickSize", 0.01)),
                    "min_notional": float(filters.get("NOTIONAL", {}).get("minNotional", 10)),
                }
        raise ValueError(f"Symbol {symbol} not found")

    def round_qty(self, qty: float, step_size: float) -> float:
        """Round quantity to valid step size."""
        precision = len(str(step_size).rstrip("0").split(".")[-1])
        return round(qty - (qty % step_size), precision)

    def round_price(self, price: float, tick_size: float) -> float:
        """Round price to valid tick size."""
        precision = len(str(tick_size).rstrip("0").split(".")[-1])
        return round(price - (price % tick_size), precision)

    def place_limit_order(self, symbol: str, side: str, qty: float, price: float) -> dict:
        """Place a limit order."""
        params = {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": qty,
            "price": price,
        }
        return self._request("POST", "/api/v3/order", params)

    def get_order(self, symbol: str, order_id: int) -> dict:
        """Get order status."""
        return self._request("GET", "/api/v3/order", {"symbol": symbol, "orderId": order_id})

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancel an order."""
        return self._request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id})

    def get_account(self) -> dict:
        """Get account info."""
        return self._request("GET", "/api/v3/account", {})


def wait_for_fill(client: BinanceTradingClient, symbol: str, order_id: int, timeout: int = ORDER_TIMEOUT) -> bool:
    """Poll until order is filled or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        order = client.get_order(symbol, order_id)
        status = order["status"]
        filled = float(order["executedQty"])
        logger.info("  Order %s: status=%s, filled=%s", order_id, status, filled)
        if status == "FILLED":
            return True
        if status in ("CANCELED", "REJECTED", "EXPIRED"):
            return False
        time.sleep(POLL_INTERVAL)
    logger.warning("  Order %s timed out after %ds", order_id, timeout)
    return False


def run_test(client: BinanceTradingClient, dry_run: bool = False):
    """Run the full test sequence."""
    logger.info("=" * 60)
    logger.info("BINANCE TRADING TEST")
    logger.info("=" * 60)

    info = client.get_exchange_info(SYMBOL)
    logger.info("Exchange info: min_qty=%.8f, step=%.8f, tick=%.2f",
                info["min_qty"], info["step_size"], info["tick_size"])

    # --- Step 1: Buy $100 worth ---
    logger.info("\n--- STEP 1: BUY $100 worth of BTC ---")
    book = client.get_best_bid_ask(SYMBOL)
    logger.info("Bid: %.2f, Ask: %.2f", book["bid"], book["ask"])

    buy_price = client.round_price(book["ask"], info["tick_size"])
    buy_qty = client.round_qty(100 / buy_price, info["step_size"])
    logger.info("Order: BUY %.8f BTC @ %.2f (notional: $%.2f)", buy_qty, buy_price, buy_qty * buy_price)

    if dry_run:
        logger.info("[DRY RUN] Would place BUY order")
        bought_qty = buy_qty
    else:
        order = client.place_limit_order(SYMBOL, "BUY", buy_qty, buy_price)
        logger.info("Order placed: id=%s", order["orderId"])
        if wait_for_fill(client, SYMBOL, order["orderId"]):
            logger.info("BUY order FILLED")
            bought_qty = float(order["executedQty"]) or buy_qty
        else:
            logger.warning("BUY order not filled, canceling...")
            client.cancel_order(SYMBOL, order["orderId"])
            return

    # --- Step 2: Sell what we bought ---
    logger.info("\n--- STEP 2: SELL the BTC we bought ---")
    book = client.get_best_bid_ask(SYMBOL)
    logger.info("Bid: %.2f, Ask: %.2f", book["bid"], book["ask"])

    sell_price = client.round_price(book["bid"], info["tick_size"])
    sell_qty = client.round_qty(bought_qty, info["step_size"])
    logger.info("Order: SELL %.8f BTC @ %.2f", sell_qty, sell_price)

    if dry_run:
        logger.info("[DRY RUN] Would place SELL order")
    else:
        order = client.place_limit_order(SYMBOL, "SELL", sell_qty, sell_price)
        logger.info("Order placed: id=%s", order["orderId"])
        if wait_for_fill(client, SYMBOL, order["orderId"]):
            logger.info("SELL order FILLED")
        else:
            logger.warning("SELL order not filled, canceling...")
            client.cancel_order(SYMBOL, order["orderId"])

    # --- Step 3: Short $50 worth ---
    logger.info("\n--- STEP 3: SHORT $50 worth of BTC ---")
    logger.info("NOTE: Shorting requires margin account. On spot-only accounts, this will fail.")
    book = client.get_best_bid_ask(SYMBOL)
    logger.info("Bid: %.2f, Ask: %.2f", book["bid"], book["ask"])

    short_price = client.round_price(book["bid"], info["tick_size"])
    short_qty = client.round_qty(50 / short_price, info["step_size"])
    logger.info("Order: SELL (short) %.8f BTC @ %.2f", short_qty, short_price)

    if dry_run:
        logger.info("[DRY RUN] Would place SHORT (margin SELL) order")
        shorted_qty = short_qty
    else:
        logger.info("Attempting margin sell (short)...")
        try:
            order = client.place_limit_order(SYMBOL, "SELL", short_qty, short_price)
            logger.info("Order placed: id=%s", order["orderId"])
            if wait_for_fill(client, SYMBOL, order["orderId"]):
                logger.info("SHORT order FILLED")
                shorted_qty = float(order["executedQty"]) or short_qty
            else:
                logger.warning("SHORT order not filled, canceling...")
                client.cancel_order(SYMBOL, order["orderId"])
                shorted_qty = 0
        except Exception as e:
            logger.error("SHORT failed (margin not enabled?): %s", e)
            shorted_qty = 0

    # --- Step 4: Cover the short ---
    if shorted_qty > 0:
        logger.info("\n--- STEP 4: COVER the short ---")
        book = client.get_best_bid_ask(SYMBOL)
        logger.info("Bid: %.2f, Ask: %.2f", book["bid"], book["ask"])

        cover_price = client.round_price(book["ask"], info["tick_size"])
        cover_qty = client.round_qty(shorted_qty, info["step_size"])
        logger.info("Order: BUY (cover) %.8f BTC @ %.2f", cover_qty, cover_price)

        if dry_run:
            logger.info("[DRY RUN] Would place COVER (BUY) order")
        else:
            order = client.place_limit_order(SYMBOL, "BUY", cover_qty, cover_price)
            logger.info("Order placed: id=%s", order["orderId"])
            if wait_for_fill(client, SYMBOL, order["orderId"]):
                logger.info("COVER order FILLED")
            else:
                logger.warning("COVER order not filled, canceling...")
                client.cancel_order(SYMBOL, order["orderId"])

    logger.info("\n" + "=" * 60)
    logger.info("TEST COMPLETE")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Test Binance trading")
    parser.add_argument("--dry-run", action="store_true", help="Don't place real orders")
    args = parser.parse_args()

    api_key = os.environ.get("BINANCE_API_KEY")
    api_secret = os.environ.get("BINANCE_API_SECRET")
    testnet = os.environ.get("BINANCE_TESTNET", "").lower() in ("1", "true", "yes")

    if not api_key or not api_secret:
        if args.dry_run:
            logger.info("No API keys found, running in dry-run mode with public endpoints only")
            api_key = "dummy"
            api_secret = "dummy"
        else:
            logger.error("BINANCE_API_KEY and BINANCE_API_SECRET must be set")
            logger.error("Or use --dry-run for simulation")
            sys.exit(1)

    client = BinanceTradingClient(api_key, api_secret, testnet=testnet)

    try:
        run_test(client, dry_run=args.dry_run)
    except KeyboardInterrupt:
        logger.info("\nInterrupted")
    except Exception as e:
        logger.error("Test failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
