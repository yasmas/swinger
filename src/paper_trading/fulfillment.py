"""Simulates limit-order execution with realistic price mechanics."""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from exchange.base import ExchangeClient

logger = logging.getLogger(__name__)


class FulfillmentResult(str, Enum):
    WAITING = "WAITING"
    FILLED = "FILLED"
    ABORTED_MARKET = "ABORTED_MARKET"
    ABORTED_CANCEL = "ABORTED_CANCEL"
    EXPIRED = "EXPIRED"


class FulfillmentEngine:
    """Simulates limit-order fulfillment by polling 1m klines.

    When the strategy signals a trade, this engine:
    1. Calculates a target price slightly better than market.
    2. Polls 1m bars to check if price touched the target.
    3. Fills at target, or aborts if price moves too far against us.
    """

    def __init__(self, exchange: ExchangeClient, symbol: str, config: dict | None = None):
        self.exchange = exchange
        self.symbol = symbol

        config = config or {}
        self.target_improvement_pct = config.get("target_improvement_pct", 0.02)
        self.abort_threshold_pct = config.get("abort_threshold_pct", 0.3)
        self.timeout_minutes = config.get("timeout_minutes", 30)
        self.on_timeout = config.get("on_timeout", "market")

        self.pending: dict | None = None

    def start(self, action_type: str, quantity: float) -> dict:
        """Start a new fulfillment attempt.

        Args:
            action_type: "BUY", "SELL", "SHORT", or "COVER".
            quantity: Amount to trade.

        Returns:
            The pending order dict (also stored in self.pending).
        """
        price = self.exchange.get_current_price(self.symbol)
        bid_ask = self.exchange.get_best_bid_ask(self.symbol)
        now = datetime.now(timezone.utc)

        is_buying = action_type in ("BUY", "COVER")

        if is_buying:
            target = bid_ask["bid_price"] * (1 - self.target_improvement_pct / 100)
            abort_price = price * (1 + self.abort_threshold_pct / 100)
        else:
            target = bid_ask["ask_price"] * (1 + self.target_improvement_pct / 100)
            abort_price = price * (1 - self.abort_threshold_pct / 100)

        self.pending = {
            "action": action_type,
            "quantity": quantity,
            "decision_time": now.isoformat(),
            "decision_price": price,
            "bid_at_decision": bid_ask["bid_price"],
            "ask_at_decision": bid_ask["ask_price"],
            "target_price": round(target, 2),
            "abort_price": round(abort_price, 2),
            "timeout_at": (now + timedelta(minutes=self.timeout_minutes)).isoformat(),
            "checks": 0,
            "price_low_during_fill": price,
            "price_high_during_fill": price,
        }

        logger.info(
            "Fulfillment started: %s %.8f %s target=%.2f abort=%.2f timeout=%s",
            action_type, quantity, self.symbol,
            target, abort_price, self.pending["timeout_at"],
        )
        return self.pending

    def resume(self, pending: dict):
        """Resume monitoring a pending order loaded from state file."""
        self.pending = pending
        timeout_at = datetime.fromisoformat(pending["timeout_at"])
        now = datetime.now(timezone.utc)

        if now > timeout_at:
            logger.warning(
                "Pending order expired during downtime (timeout was %s). "
                "Will resolve on next check.",
                pending["timeout_at"],
            )

    def check(self) -> tuple[FulfillmentResult, dict]:
        """Check if the pending order should be filled or aborted.

        Returns:
            (result, details) where result is FulfillmentResult and details
            contains fulfillment metadata for the trade log.
        """
        if self.pending is None:
            raise RuntimeError("No pending order to check")

        self.pending["checks"] += 1
        now = datetime.now(timezone.utc)
        timeout_at = datetime.fromisoformat(self.pending["timeout_at"])

        is_buying = self.pending["action"] in ("BUY", "COVER")
        target = self.pending["target_price"]
        abort_price = self.pending["abort_price"]

        # Fetch latest 1m kline
        kline = self.exchange.fetch_ohlcv(self.symbol, "1m", limit=1)
        if kline.empty:
            logger.warning("Empty 1m kline response, will retry next check.")
            return FulfillmentResult.WAITING, {}

        bar = kline.iloc[-1]
        bar_low = float(bar["low"])
        bar_high = float(bar["high"])
        current_price = float(bar["close"])

        self.pending["price_low_during_fill"] = min(
            self.pending["price_low_during_fill"], bar_low
        )
        self.pending["price_high_during_fill"] = max(
            self.pending["price_high_during_fill"], bar_high
        )

        # Check for fill
        filled = False
        if is_buying and bar_low <= target:
            filled = True
        elif not is_buying and bar_high >= target:
            filled = True

        if filled:
            details = self._build_details(target, "limit", now)
            logger.info(
                "Fulfillment FILLED: %s @ %.2f (limit, %ds, %d checks)",
                self.pending["action"], target,
                details["time_to_fill_seconds"], self.pending["checks"],
            )
            self.pending = None
            return FulfillmentResult.FILLED, details

        # Check for adverse movement
        aborted = False
        if is_buying and current_price > abort_price:
            aborted = True
        elif not is_buying and current_price < abort_price:
            aborted = True

        if aborted:
            if self.on_timeout == "market":
                details = self._build_details(current_price, "market_abort", now)
                logger.warning(
                    "Fulfillment ABORTED (adverse movement): %s @ %.2f market "
                    "(decision was %.2f, threshold %.2f)",
                    self.pending["action"], current_price,
                    self.pending["decision_price"], abort_price,
                )
                self.pending = None
                return FulfillmentResult.ABORTED_MARKET, details
            else:
                details = self._build_details(None, "cancelled_adverse", now)
                logger.warning(
                    "Fulfillment CANCELLED (adverse movement): %s "
                    "(price %.2f crossed abort %.2f)",
                    self.pending["action"], current_price, abort_price,
                )
                self.pending = None
                return FulfillmentResult.ABORTED_CANCEL, details

        # Check for timeout
        if now >= timeout_at:
            if self.on_timeout == "market":
                details = self._build_details(current_price, "market_timeout", now)
                logger.warning(
                    "Fulfillment TIMEOUT: %s @ %.2f market (target was %.2f)",
                    self.pending["action"], current_price, target,
                )
                self.pending = None
                return FulfillmentResult.ABORTED_MARKET, details
            else:
                details = self._build_details(None, "cancelled_timeout", now)
                logger.warning(
                    "Fulfillment TIMEOUT CANCELLED: %s (no fill after %d min)",
                    self.pending["action"], self.timeout_minutes,
                )
                self.pending = None
                return FulfillmentResult.ABORTED_CANCEL, details

        logger.info(
            "Fulfillment check #%d: %s target=%.2f, 1m %s=%.2f → waiting",
            self.pending["checks"], self.pending["action"],
            target, "low" if is_buying else "high",
            bar_low if is_buying else bar_high,
        )
        return FulfillmentResult.WAITING, {}

    def _build_details(self, fill_price: float | None, fill_type: str,
                       now: datetime) -> dict:
        """Build the fulfillment details dict for the trade log."""
        decision_time = datetime.fromisoformat(self.pending["decision_time"])
        elapsed = (now - decision_time).total_seconds()

        slippage = None
        if fill_price is not None:
            slippage = (fill_price / self.pending["decision_price"] - 1) * 100

        return {
            "decision_time": self.pending["decision_time"],
            "decision_price": self.pending["decision_price"],
            "bid_at_decision": self.pending["bid_at_decision"],
            "ask_at_decision": self.pending["ask_at_decision"],
            "target_price": self.pending["target_price"],
            "fill_time": now.isoformat(),
            "fill_price": fill_price,
            "fill_type": fill_type,
            "time_to_fill_seconds": int(elapsed),
            "checks_count": self.pending["checks"],
            "slippage_vs_decision_pct": round(slippage, 4) if slippage is not None else None,
            "price_range_during_fill": [
                self.pending["price_low_during_fill"],
                self.pending["price_high_during_fill"],
            ],
        }

    def get_pending_for_state(self) -> dict | None:
        """Return the pending order dict for state persistence."""
        return self.pending
