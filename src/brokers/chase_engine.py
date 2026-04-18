"""Adaptive limit-order execution engine ("chase") for live trading.

Places a GTC limit order near the best bid/ask, then periodically re-prices
to track the moving market. Falls back to a market IOC if price drifts too
far or time runs out.

All price thresholds are in basis points (bps) for instrument-agnostic sizing.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from enum import Enum

from exchange.coinbase_rest import CoinbaseRestClient

logger = logging.getLogger(__name__)


class ChaseResult(str, Enum):
    WAITING = "WAITING"
    FILLED = "FILLED"
    MARKET_FALLBACK = "MARKET_FALLBACK"
    CANCELLED = "CANCELLED"


# Coinbase order statuses that mean the order is done
_TERMINAL_STATUSES = {"FILLED", "CANCELLED", "EXPIRED", "FAILED"}
_EDIT_IN_PROGRESS = {"EDIT_QUEUED"}


class ChaseEngine:
    """Adaptive limit order chaser.

    Workflow:
        1. start() — place initial GTC limit at best bid/ask ± offset
        2. check() — called periodically; re-prices if market moved, detects
           fill, enforces timeout and adverse-move thresholds
        3. On fill/timeout/adverse → returns terminal ChaseResult

    All thresholds use bps (1 bps = 0.01%).
    """

    def __init__(self, exchange: CoinbaseRestClient, config: dict | None = None):
        self.exchange = exchange
        config = config or {}

        self.reprice_interval_sec: float = config.get("reprice_interval_sec", 3)
        self.offset_bps: float = config.get("offset_bps", 0)
        self.min_reprice_bps: float = config.get("min_reprice_bps", 2)
        self.max_slippage_bps: float = config.get("max_slippage_bps", 30)
        self.timeout_sec: float = config.get("timeout_sec", 120)
        self.on_timeout: str = config.get("on_timeout", "market")
        self.on_adverse_move: str = config.get("on_adverse_move", "market")

        # State
        self.pending: dict | None = None

    # Max attempts when a post_only order is rejected because the bid/ask moved
    # between our quote fetch and the exchange processing. Applies to place and edit.
    _POST_ONLY_MAX_ATTEMPTS = 2
    _POST_ONLY_RETRY_SLEEP_SEC = 0.05

    def _compute_limit_price(self, side: str, bid_ask: dict, tick: float) -> float:
        """Compute the limit price for a post_only maker order."""
        if side == "BUY":
            price = bid_ask["ask_price"] * (1 - max(self.offset_bps, 0) / 10000) - tick
        else:
            price = bid_ask["bid_price"] * (1 + max(self.offset_bps, 0) / 10000) + tick
        return round(price / tick) * tick

    @staticmethod
    def _is_post_only_cross_error(err: Exception) -> bool:
        # Matches Coinbase rejection reasons when a post_only price would cross
        # the book (e.g., "POST_ONLY", "POST_ONLY_WOULD_CROSS").
        return "POST_ONLY" in str(err)

    def _place_limit_with_retry(
        self, side: str, qty: float, bid_ask: dict | None = None,
    ) -> tuple[dict, float, dict]:
        """Place a post_only GTC limit order, retrying on POST_ONLY cross.

        On POST_ONLY rejection, re-fetches bid/ask, recomputes the limit price,
        and retries up to _POST_ONLY_MAX_ATTEMPTS attempts total. Non-POST_ONLY
        errors propagate immediately.

        Returns:
            (order_dict, limit_price, bid_ask) from the successful attempt.
        """
        specs = self.exchange.get_product_specs()
        tick = specs["quote_increment"]

        for attempt in range(self._POST_ONLY_MAX_ATTEMPTS):
            if bid_ask is None:
                bid_ask = self.exchange.get_best_bid_ask(self.exchange.product_id)
            limit_price = self._compute_limit_price(side, bid_ask, tick)
            try:
                order = self.exchange.place_limit_order_gtc(
                    side=side, base_size=qty, limit_price=limit_price, post_only=True,
                )
                return order, limit_price, bid_ask
            except RuntimeError as e:
                is_last = attempt == self._POST_ONLY_MAX_ATTEMPTS - 1
                if not self._is_post_only_cross_error(e) or is_last:
                    raise
                logger.warning(
                    "Chase place: post_only rejected (attempt %d/%d), retrying → "
                    "bid=%.2f ask=%.2f limit=%.2f",
                    attempt + 1, self._POST_ONLY_MAX_ATTEMPTS,
                    bid_ask["bid_price"], bid_ask["ask_price"], limit_price,
                )
                time.sleep(self._POST_ONLY_RETRY_SLEEP_SEC)
                bid_ask = None  # force refetch on next attempt

        raise RuntimeError("Chase place: unreachable retry path")

    def _edit_with_retry(
        self, order_id: str, qty: float, side: str, bid_ask: dict | None = None,
    ) -> tuple[float, dict]:
        """Edit a post_only limit order's price, retrying on POST_ONLY cross.

        On POST_ONLY rejection, re-fetches bid/ask, recomputes the limit price,
        and retries up to _POST_ONLY_MAX_ATTEMPTS attempts total. Non-POST_ONLY
        errors (e.g., order already filled) propagate immediately.

        Returns:
            (new_limit_price, bid_ask) from the successful attempt.
        """
        specs = self.exchange.get_product_specs()
        tick = specs["quote_increment"]

        for attempt in range(self._POST_ONLY_MAX_ATTEMPTS):
            if bid_ask is None:
                bid_ask = self.exchange.get_best_bid_ask(self.exchange.product_id)
            new_price = self._compute_limit_price(side, bid_ask, tick)
            try:
                # Always pass size — CFM futures requires it on edits even when unchanged
                self.exchange.edit_order(order_id, new_price, new_size=qty)
                return new_price, bid_ask
            except RuntimeError as e:
                is_last = attempt == self._POST_ONLY_MAX_ATTEMPTS - 1
                if not self._is_post_only_cross_error(e) or is_last:
                    raise
                logger.warning(
                    "Chase edit: post_only rejected (attempt %d/%d), retrying → "
                    "bid=%.2f ask=%.2f limit=%.2f",
                    attempt + 1, self._POST_ONLY_MAX_ATTEMPTS,
                    bid_ask["bid_price"], bid_ask["ask_price"], new_price,
                )
                time.sleep(self._POST_ONLY_RETRY_SLEEP_SEC)
                bid_ask = None  # force refetch on next attempt

        raise RuntimeError("Chase edit: unreachable retry path")

    def start(self, side: str, qty: float) -> dict:
        """Place the initial GTC limit order and begin chasing.

        Args:
            side: "BUY" or "SELL".
            qty: Quantity in base currency (already rounded to base_increment).

        Returns:
            The pending chase state dict.

        Raises:
            RuntimeError: If the order cannot be placed after retries.
        """
        now = datetime.now(timezone.utc)
        order, limit_price, bid_ask = self._place_limit_with_retry(side, qty)
        decision_mid = (bid_ask["bid_price"] + bid_ask["ask_price"]) / 2

        logger.info(
            "Chase START: %s %.6f %s | bid=%.2f ask=%.2f mid=%.2f → limit=%.2f "
            "(offset=%+.1f bps, timeout=%ds, order_id=%s)",
            side, qty, self.exchange.product_id,
            bid_ask["bid_price"], bid_ask["ask_price"], decision_mid,
            limit_price, self.offset_bps, self.timeout_sec, order["order_id"],
        )

        self.pending = {
            "order_id": order["order_id"],
            "side": side,
            "quantity": qty,
            "decision_time": now.isoformat(),
            "decision_mid": decision_mid,
            "current_limit_price": limit_price,
            "timeout_at": (now + timedelta(seconds=self.timeout_sec)).isoformat(),
            "checks": 0,
            "reprices": 0,
            "last_reprice_time": now.isoformat(),
            "bid_at_decision": bid_ask["bid_price"],
            "ask_at_decision": bid_ask["ask_price"],
        }
        return self.pending

    def check(self) -> tuple[ChaseResult, dict]:
        """Run one check cycle: poll order status, maybe re-price.

        Returns:
            (ChaseResult, details) — details contains fill info on terminal states,
            empty dict on WAITING.
        """
        if self.pending is None:
            raise RuntimeError("No pending chase to check")

        self.pending["checks"] += 1
        now = datetime.now(timezone.utc)
        is_buying = self.pending["side"] == "BUY"
        order_id = self.pending["order_id"]

        # ── Step 1: Check order status on exchange ──
        order_status = self.exchange.get_order(order_id)
        status = order_status["status"]

        if status == "FILLED":
            details = self._build_details(
                fill_price=order_status["average_filled_price"],
                fill_type="limit",
                fees=order_status["total_fees"],
                now=now,
            )
            logger.info(
                "Chase FILLED: %s %.6f @ %.2f (limit, %d checks, %d reprices, %.1fs)",
                self.pending["side"], self.pending["quantity"],
                order_status["average_filled_price"],
                self.pending["checks"], self.pending["reprices"],
                details["time_to_fill_seconds"],
            )
            self.pending = None
            return ChaseResult.FILLED, details

        if status in ("CANCELLED", "EXPIRED", "FAILED"):
            # post_only rejection or stale price — re-post at current market
            timeout_at = datetime.fromisoformat(self.pending["timeout_at"])
            if now < timeout_at:
                logger.info(
                    "Chase order %s was %s (likely post_only rejection). "
                    "Re-posting at current market.",
                    order_id, status,
                )
                self._repost_order(now)
                return ChaseResult.WAITING, {}

            # Past timeout — don't re-post
            details = self._build_details(
                fill_price=None, fill_type=f"exchange_{status.lower()}", fees=0, now=now,
            )
            logger.warning("Chase order %s reached terminal status: %s (past timeout)", order_id, status)
            self.pending = None
            return ChaseResult.CANCELLED, details

        # Order is still open (OPEN, PENDING, EDIT_QUEUED, etc.)
        # Don't re-price if an edit is in progress
        if status in _EDIT_IN_PROGRESS:
            logger.debug("Chase check #%d: edit in progress, waiting", self.pending["checks"])
            return ChaseResult.WAITING, {}

        # ── Step 2: Check adverse move and timeout ──
        bid_ask = self.exchange.get_best_bid_ask(self.exchange.product_id)
        current_mid = (bid_ask["bid_price"] + bid_ask["ask_price"]) / 2
        decision_mid = self.pending["decision_mid"]

        # Adverse move: price moved against our fill direction
        if is_buying:
            drift_bps = (current_mid / decision_mid - 1) * 10000
        else:
            drift_bps = (1 - current_mid / decision_mid) * 10000

        timeout_at = datetime.fromisoformat(self.pending["timeout_at"])
        timed_out = now >= timeout_at

        if drift_bps > self.max_slippage_bps:
            return self._escalate(
                reason="adverse_move",
                policy=self.on_adverse_move,
                bid_ask=bid_ask,
                drift_bps=drift_bps,
                now=now,
            )

        if timed_out:
            return self._escalate(
                reason="timeout",
                policy=self.on_timeout,
                bid_ask=bid_ask,
                drift_bps=drift_bps,
                now=now,
            )

        # ── Step 3: Maybe re-price ──
        last_reprice = datetime.fromisoformat(self.pending["last_reprice_time"])
        if (now - last_reprice).total_seconds() >= self.reprice_interval_sec:
            self._maybe_reprice(bid_ask, now)

        elapsed = (now - datetime.fromisoformat(self.pending["decision_time"])).total_seconds()
        logger.info(
            "Chase check #%d: %s | limit=%.2f, bid=%.2f, ask=%.2f, "
            "drift=%+.1f bps, reprices=%d, elapsed=%.0fs/%.0fs",
            self.pending["checks"], self.pending["side"],
            self.pending["current_limit_price"],
            bid_ask["bid_price"], bid_ask["ask_price"],
            drift_bps, self.pending["reprices"],
            elapsed, self.timeout_sec,
        )
        return ChaseResult.WAITING, {}

    def cancel(self) -> dict | None:
        """Cancel the pending chase order. Returns details or None."""
        if self.pending is None:
            return None

        order_id = self.pending["order_id"]
        self.exchange.cancel_orders([order_id])

        now = datetime.now(timezone.utc)
        details = self._build_details(
            fill_price=None, fill_type="cancelled_by_caller", fees=0, now=now,
        )
        logger.info("Chase CANCELLED by caller: order %s", order_id)
        self.pending = None
        return details

    def resume(self, pending: dict) -> None:
        """Resume a chase from persisted state (crash recovery)."""
        self.pending = pending
        timeout_at = datetime.fromisoformat(pending["timeout_at"])
        now = datetime.now(timezone.utc)

        if now > timeout_at:
            logger.warning(
                "Chase order %s expired during downtime (timeout was %s). "
                "Will resolve on next check.",
                pending.get("order_id"), pending["timeout_at"],
            )

    # ── Internal ──────────────────────────────────────────────────────

    def _repost_order(self, now: datetime) -> None:
        """Re-post a new order after a post_only rejection or cancellation."""
        order, limit_price, _ = self._place_limit_with_retry(
            self.pending["side"], self.pending["quantity"],
        )

        logger.info(
            "Chase REPOST: %s @ %.2f (old order was rejected, new order_id=%s)",
            self.pending["side"], limit_price, order["order_id"],
        )

        self.pending["order_id"] = order["order_id"]
        self.pending["current_limit_price"] = limit_price
        self.pending["reprices"] += 1
        self.pending["last_reprice_time"] = now.isoformat()

    def _maybe_reprice(self, bid_ask: dict, now: datetime) -> None:
        """Re-price the order if the market has moved enough."""
        is_buying = self.pending["side"] == "BUY"

        specs = self.exchange.get_product_specs()
        tick = specs["quote_increment"]
        projected_price = self._compute_limit_price(self.pending["side"], bid_ask, tick)
        reference = bid_ask["ask_price"] if is_buying else bid_ask["bid_price"]

        old_price = self.pending["current_limit_price"]
        delta_bps = abs(projected_price / old_price - 1) * 10000

        if delta_bps < self.min_reprice_bps:
            return  # Not enough movement to justify a re-price

        try:
            new_price, _ = self._edit_with_retry(
                self.pending["order_id"],
                self.pending["quantity"],
                self.pending["side"],
                bid_ask=bid_ask,
            )
            logger.info(
                "Chase REPRICE #%d: %s %.2f → %.2f (delta=%.1f bps, ref %s=%.2f)",
                self.pending["reprices"] + 1,
                self.pending["side"], old_price, new_price, delta_bps,
                "bid" if is_buying else "ask", reference,
            )
            self.pending["current_limit_price"] = new_price
            self.pending["reprices"] += 1
            self.pending["last_reprice_time"] = now.isoformat()
        except RuntimeError as e:
            # Edit may fail if order was filled between check and edit,
            # or all post_only retries were rejected (try again next check).
            logger.warning("Chase reprice failed (may be filled): %s", e)

    def _escalate(
        self, reason: str, policy: str, bid_ask: dict,
        drift_bps: float, now: datetime,
    ) -> tuple[ChaseResult, dict]:
        """Handle timeout or adverse move — go market or cancel."""
        order_id = self.pending["order_id"]

        logger.warning(
            "Chase ESCALATE (%s): %s %s | drift=%+.1f bps, policy=%s, "
            "bid=%.2f, ask=%.2f, limit_was=%.2f",
            reason, self.pending["side"], self.exchange.product_id,
            drift_bps, policy,
            bid_ask["bid_price"], bid_ask["ask_price"],
            self.pending["current_limit_price"],
        )

        # Cancel the limit order first
        self.exchange.cancel_orders([order_id])

        # Check if it got filled in the meantime
        order_status = self.exchange.get_order(order_id)
        if order_status["status"] == "FILLED":
            details = self._build_details(
                fill_price=order_status["average_filled_price"],
                fill_type="limit_filled_during_cancel",
                fees=order_status["total_fees"],
                now=now,
            )
            logger.info(
                "Chase filled during cancel: %.2f", order_status["average_filled_price"],
            )
            self.pending = None
            return ChaseResult.FILLED, details

        if policy == "market":
            # Place market IOC
            market_order = self.exchange.place_market_order_ioc(
                side=self.pending["side"],
                base_size=self.pending["quantity"],
            )
            # Poll for fill (market IOC should fill immediately)
            time.sleep(0.5)
            market_status = self.exchange.get_order(market_order["order_id"])

            if market_status["status"] == "FILLED":
                details = self._build_details(
                    fill_price=market_status["average_filled_price"],
                    fill_type=f"market_{reason}",
                    fees=market_status["total_fees"],
                    now=now,
                )
                logger.info(
                    "Chase MARKET FALLBACK (%s): filled @ %.2f (fees=%.4f)",
                    reason, market_status["average_filled_price"],
                    market_status["total_fees"],
                )
                self.pending = None
                return ChaseResult.MARKET_FALLBACK, details
            else:
                # Market order didn't fill immediately — unusual
                logger.error(
                    "Market IOC order %s status=%s (expected FILLED)",
                    market_order["order_id"], market_status["status"],
                )
                details = self._build_details(
                    fill_price=None,
                    fill_type=f"market_{reason}_unfilled",
                    fees=0,
                    now=now,
                )
                self.pending = None
                return ChaseResult.CANCELLED, details
        else:
            # Cancel policy
            details = self._build_details(
                fill_price=None, fill_type=f"cancelled_{reason}", fees=0, now=now,
            )
            logger.info("Chase CANCELLED (%s): no market fallback per policy", reason)
            self.pending = None
            return ChaseResult.CANCELLED, details

    def _build_details(
        self, fill_price: float | None, fill_type: str,
        fees: float, now: datetime,
    ) -> dict:
        """Build the chase details dict for the trade log."""
        decision_time = datetime.fromisoformat(self.pending["decision_time"])
        elapsed = (now - decision_time).total_seconds()

        slippage_bps = None
        if fill_price is not None and self.pending["decision_mid"] > 0:
            slippage_bps = (fill_price / self.pending["decision_mid"] - 1) * 10000

        return {
            "action": self.pending["side"],
            "quantity": self.pending["quantity"],
            "decision_time": self.pending["decision_time"],
            "decision_mid": self.pending["decision_mid"],
            "bid_at_decision": self.pending["bid_at_decision"],
            "ask_at_decision": self.pending["ask_at_decision"],
            "fill_time": now.isoformat(),
            "fill_price": fill_price,
            "fill_type": fill_type,
            "fees": fees,
            "time_to_fill_seconds": int(elapsed),
            "checks_count": self.pending["checks"],
            "reprices_count": self.pending["reprices"],
            "slippage_vs_decision_bps": round(slippage_bps, 2) if slippage_bps is not None else None,
            "final_limit_price": self.pending["current_limit_price"],
        }
