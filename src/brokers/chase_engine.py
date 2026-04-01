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

    def start(self, side: str, qty: float) -> dict:
        """Place the initial GTC limit order and begin chasing.

        Args:
            side: "BUY" or "SELL".
            qty: Quantity in base currency (already rounded to base_increment).

        Returns:
            The pending chase state dict.
        """
        bid_ask = self.exchange.get_best_bid_ask(self.exchange.product_id)
        now = datetime.now(timezone.utc)

        is_buying = side == "BUY"

        specs = self.exchange.get_product_specs()
        tick = specs["quote_increment"]

        if is_buying:
            # Post just below the ask — top of the bid side, likely to fill, still maker
            # offset_bps pushes further from ask (less aggressive)
            limit_price = bid_ask["ask_price"] * (1 - max(self.offset_bps, 0) / 10000) - tick
            limit_price = round(limit_price / tick) * tick
        else:
            # Post just above the bid — top of the ask side, likely to fill, still maker
            # offset_bps pushes further from bid (less aggressive)
            limit_price = bid_ask["bid_price"] * (1 + max(self.offset_bps, 0) / 10000) + tick
            limit_price = round(limit_price / tick) * tick

        decision_mid = (bid_ask["bid_price"] + bid_ask["ask_price"]) / 2

        logger.info(
            "Chase START: %s %.6f %s | bid=%.2f ask=%.2f mid=%.2f → limit=%.2f "
            "(offset=%+.1f bps, timeout=%ds)",
            side, qty, self.exchange.product_id,
            bid_ask["bid_price"], bid_ask["ask_price"], decision_mid,
            limit_price, self.offset_bps, self.timeout_sec,
        )

        # Place the order — post_only=True ensures we're always a maker
        order = self.exchange.place_limit_order_gtc(
            side=side, base_size=qty, limit_price=limit_price, post_only=True,
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
        bid_ask = self.exchange.get_best_bid_ask(self.exchange.product_id)
        is_buying = self.pending["side"] == "BUY"
        specs = self.exchange.get_product_specs()
        tick = specs["quote_increment"]

        if is_buying:
            limit_price = bid_ask["ask_price"] * (1 - max(self.offset_bps, 0) / 10000) - tick
            limit_price = round(limit_price / tick) * tick
        else:
            limit_price = bid_ask["bid_price"] * (1 + max(self.offset_bps, 0) / 10000) + tick
            limit_price = round(limit_price / tick) * tick

        order = self.exchange.place_limit_order_gtc(
            side=self.pending["side"],
            base_size=self.pending["quantity"],
            limit_price=limit_price,
            post_only=True,
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

        if is_buying:
            # Track just below the ask
            new_price = bid_ask["ask_price"] * (1 - max(self.offset_bps, 0) / 10000) - tick
            new_price = round(new_price / tick) * tick
            reference = bid_ask["ask_price"]
        else:
            # Track just above the bid
            new_price = bid_ask["bid_price"] * (1 + max(self.offset_bps, 0) / 10000) + tick
            new_price = round(new_price / tick) * tick
            reference = bid_ask["bid_price"]

        old_price = self.pending["current_limit_price"]
        delta_bps = abs(new_price / old_price - 1) * 10000

        if delta_bps < self.min_reprice_bps:
            return  # Not enough movement to justify a re-price

        try:
            self.exchange.edit_order(self.pending["order_id"], new_price)
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
            # Edit may fail if order was filled between check and edit
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
