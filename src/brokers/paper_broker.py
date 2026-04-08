"""PaperBroker — simulated order execution using local Portfolio + FulfillmentEngine."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from exchange.base import ExchangeClient
from portfolio import Portfolio
from strategies.base import PortfolioView, portfolio_view_from
from trade_log import TradeLogReader
from brokers.fulfillment import FulfillmentEngine, FulfillmentResult

from brokers.base import (
    BrokerBase, BrokerCapabilities, FillResult, OrderSide, OrderStatus, PortfolioSnapshot,
)

logger = logging.getLogger(__name__)

TRADE_ACTIONS = {"BUY", "SELL", "SHORT", "COVER"}

# Reserve fraction of cash for slippage on entries
CASH_RESERVE_FRACTION = 0.005


class PaperBroker(BrokerBase):
    """Simulated broker using local Portfolio + FulfillmentEngine.

    - Portfolio state is maintained in-memory (local Portfolio object).
    - Order fulfillment uses the existing FulfillmentEngine (polls 1m klines).
    - Sizing: entries use available cash minus a small reserve; exits close full position.
    """

    def __init__(self, exchange: ExchangeClient):
        self.exchange = exchange
        self._portfolio: Portfolio | None = None
        self._fulfillment: FulfillmentEngine | None = None
        self._fulfillment_config: dict = {}
        self._max_notional_pct: float | None = None
        self._order_counter = 0
        self._current_order_id: str | None = None
        self._current_order_symbol: str | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    def startup(self, config: dict) -> None:
        initial_cash = config.get("initial_cash", 100000)
        self._max_notional_pct: float | None = config.get("max_notional_pct")
        self._portfolio = Portfolio(initial_cash)
        self._fulfillment_config = config.get("fulfillment", {})
        logger.info("PaperBroker started with $%.2f", initial_cash)
        if self._max_notional_pct:
            logger.info("  Max notional pct: %.1f%%", self._max_notional_pct)

    def shutdown(self) -> None:
        if self._fulfillment and self._fulfillment.pending:
            logger.info("PaperBroker shutdown: cancelling pending fulfillment")
            self._fulfillment.pending = None
            self._current_order_id = None

    # ── Capabilities ───────────────────────────────────────────────────

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            supports_shorting=True,
            supports_margin=False,
            supports_leverage=False,
            max_leverage=None,
            supported_order_types=["limit", "market"],
        )

    # ── Portfolio ──────────────────────────────────────────────────────

    def get_portfolio_snapshot(self, prices: dict[str, float] | None = None) -> PortfolioSnapshot:
        positions = {}
        for sym, pos in self._portfolio.positions.items():
            positions[sym] = {"qty": pos.quantity, "avg_cost": pos.avg_cost, "side": "LONG"}
        for sym, pos in self._portfolio.short_positions.items():
            positions[sym] = {"qty": pos.quantity, "avg_cost": pos.avg_cost, "side": "SHORT"}

        total = self._portfolio.total_value(prices) if prices else self._portfolio.cash
        return PortfolioSnapshot(
            cash=self._portfolio.cash,
            positions=positions,
            total_value=total,
        )

    def get_position(self, symbol: str) -> dict | None:
        if symbol in self._portfolio.positions:
            pos = self._portfolio.positions[symbol]
            return {"qty": pos.quantity, "avg_cost": pos.avg_cost, "side": "LONG"}
        if symbol in self._portfolio.short_positions:
            pos = self._portfolio.short_positions[symbol]
            return {"qty": pos.quantity, "avg_cost": pos.avg_cost, "side": "SHORT"}
        return None

    def portfolio_view(self, symbol: str) -> PortfolioView:
        return portfolio_view_from(self._portfolio, symbol)

    # ── Orders ─────────────────────────────────────────────────────────

    def submit_order(self, symbol: str, side: OrderSide,
                     notional: float | None = None) -> str:
        if self._current_order_id is not None:
            raise RuntimeError(
                f"Cannot submit order: pending order {self._current_order_id} exists"
            )

        price = self.exchange.get_current_price(symbol)
        qty = self._compute_quantity(symbol, side, price, notional)

        if qty <= 0:
            raise ValueError(f"Computed quantity is zero for {side.value} {symbol}")

        self._fulfillment = FulfillmentEngine(
            self.exchange, symbol, self._fulfillment_config,
        )
        self._fulfillment.start(side.value, qty)

        self._order_counter += 1
        self._current_order_id = f"paper_{self._order_counter}"
        self._current_order_symbol = symbol

        logger.info(
            "PaperBroker: submitted %s %s %.8f %s (order %s)",
            side.value, symbol, qty,
            f"notional=${notional:.2f}" if notional else "full",
            self._current_order_id,
        )
        return self._current_order_id

    def check_order(self, order_id: str) -> FillResult | None:
        if order_id != self._current_order_id:
            raise ValueError(f"Unknown order_id: {order_id}")
        if self._fulfillment is None or self._fulfillment.pending is None:
            raise RuntimeError("No pending fulfillment to check")

        result, details = self._fulfillment.check()

        if result == FulfillmentResult.WAITING:
            return None

        symbol = self._current_order_symbol
        side = OrderSide(details["action"])
        qty = details["quantity"]

        if result in (FulfillmentResult.FILLED, FulfillmentResult.ABORTED_MARKET):
            fill_price = details["fill_price"]

            # Apply to portfolio
            try:
                self._apply_to_portfolio(side, symbol, qty, fill_price)
            except ValueError as e:
                details["portfolio_error"] = str(e)
                logger.error("Portfolio operation failed: %s — returning as rejected", e)
                self._current_order_id = None
                self._current_order_symbol = None
                return FillResult(
                    status=OrderStatus.REJECTED,
                    side=side,
                    symbol=symbol,
                    filled_qty=0,
                    filled_price=0,
                    fill_type="rejected",
                    details=details,
                )

            self._current_order_id = None
            self._current_order_symbol = None
            return FillResult(
                status=OrderStatus.FILLED,
                side=side,
                symbol=symbol,
                filled_qty=qty,
                filled_price=fill_price,
                fill_type=details.get("fill_type", "limit"),
                details=details,
            )

        # ABORTED_CANCEL
        self._current_order_id = None
        self._current_order_symbol = None
        return FillResult(
            status=OrderStatus.CANCELLED,
            side=side,
            symbol=symbol,
            filled_qty=0,
            filled_price=0,
            fill_type=details.get("fill_type", "cancelled"),
            details=details,
        )

    def cancel_order(self, order_id: str) -> bool:
        if order_id != self._current_order_id:
            return False
        if self._fulfillment:
            self._fulfillment.pending = None
        self._current_order_id = None
        self._current_order_symbol = None
        logger.info("PaperBroker: cancelled order %s", order_id)
        return True

    def has_pending_order(self) -> bool:
        return self._current_order_id is not None

    def get_pending_order_info(self) -> dict | None:
        if self._fulfillment and self._fulfillment.pending:
            return {
                "order_id": self._current_order_id,
                "symbol": self._current_order_symbol,
                **self._fulfillment.pending,
            }
        return None

    # ── Emergency ──────────────────────────────────────────────────────

    def emergency_close(self, symbol: str) -> FillResult | None:
        # Cancel any pending order first
        if self._current_order_id:
            self.cancel_order(self._current_order_id)

        # Determine position and close side
        if symbol in self._portfolio.positions:
            pos = self._portfolio.positions[symbol]
            side = OrderSide.SELL
            qty = pos.quantity
        elif symbol in self._portfolio.short_positions:
            pos = self._portfolio.short_positions[symbol]
            side = OrderSide.COVER
            qty = pos.quantity
        else:
            return None  # already flat

        # Market close at current price
        price = self.exchange.get_current_price(symbol)
        self._apply_to_portfolio(side, symbol, qty, price)

        logger.info(
            "PaperBroker: emergency close %s %.8f %s @ %.2f",
            side.value, qty, symbol, price,
        )
        return FillResult(
            status=OrderStatus.FILLED,
            side=side,
            symbol=symbol,
            filled_qty=qty,
            filled_price=price,
            fill_type="market",
            details={"reason": "emergency_close"},
        )

    # ── Crash Recovery ─────────────────────────────────────────────────

    def export_state(self) -> dict:
        state = {
            "initial_cash": self._portfolio.initial_cash,
            "cash": self._portfolio.cash,
            "positions": {
                sym: {"quantity": pos.quantity, "avg_cost": pos.avg_cost}
                for sym, pos in self._portfolio.positions.items()
            },
            "short_positions": {
                sym: {"quantity": pos.quantity, "avg_cost": pos.avg_cost}
                for sym, pos in self._portfolio.short_positions.items()
            },
            "order_counter": self._order_counter,
        }

        # Include pending fulfillment if any
        if self._fulfillment and self._fulfillment.pending:
            state["pending_order"] = {
                "order_id": self._current_order_id,
                "symbol": self._current_order_symbol,
                **self._fulfillment.pending,
            }

        return state

    def import_state(self, state: dict) -> None:
        initial_cash = state.get("initial_cash", 100000)
        self._portfolio = Portfolio(initial_cash)

        # Restore cash (override the initial_cash that Portfolio sets)
        self._portfolio.cash = state.get("cash", initial_cash)

        # Restore positions
        from portfolio import Position
        for sym, pos_data in state.get("positions", {}).items():
            self._portfolio.positions[sym] = Position(
                symbol=sym,
                quantity=pos_data["quantity"],
                avg_cost=pos_data["avg_cost"],
            )
        for sym, pos_data in state.get("short_positions", {}).items():
            self._portfolio.short_positions[sym] = Position(
                symbol=sym,
                quantity=pos_data["quantity"],
                avg_cost=pos_data["avg_cost"],
            )

        self._order_counter = state.get("order_counter", 0)

        # Restore fulfillment config from broker config (caller must set this)
        # _fulfillment_config should be set before import_state via startup()

        # Resume pending fulfillment if any
        pending_data = state.get("pending_order")
        if pending_data:
            order_id = pending_data.pop("order_id", None)
            symbol = pending_data.pop("symbol", None)
            if order_id and symbol:
                self._current_order_id = order_id
                self._current_order_symbol = symbol
                self._fulfillment = FulfillmentEngine(
                    self.exchange, symbol, self._fulfillment_config,
                )
                self._fulfillment.resume(pending_data)
                logger.info(
                    "PaperBroker: resumed pending order %s (%s %s)",
                    order_id, pending_data.get("action"), symbol,
                )

        logger.info(
            "PaperBroker: state imported. Cash=$%.2f, %d long, %d short positions",
            self._portfolio.cash,
            len(self._portfolio.positions),
            len(self._portfolio.short_positions),
        )

    def reconstruct_from_trades(self, trade_log_path: str, symbol: str) -> None:
        """Reconstruct portfolio state by replaying the trade log.

        This is the fallback for first startup or when upgrading from
        the old PaperTrader state format (which didn't persist broker state).
        """
        path = Path(trade_log_path)
        if not path.exists():
            logger.info("No trade log at %s — starting with fresh portfolio", path)
            return

        try:
            trades = TradeLogReader.read(str(path))
        except Exception as e:
            logger.warning("Failed to read trade log %s: %s", path, e)
            return

        actions = trades[trades["action"].isin(TRADE_ACTIONS)]
        if actions.empty:
            logger.info("Trade log has no trades — portfolio stays at initial state")
            return

        has_position_cols = (
            "position_qty" in trades.columns and
            "position_avg_cost" in trades.columns and
            "short_qty" in trades.columns and
            "short_avg_cost" in trades.columns
        )

        if has_position_cols:
            # Fast path: read last trade row — O(1), no replay needed.
            last = actions.iloc[-1]
            self._portfolio.cash = float(last["cash_balance"])

            position_qty = float(last["position_qty"])
            short_qty = float(last["short_qty"])

            from portfolio import Position
            if position_qty > 0:
                avg_cost = float(last["position_avg_cost"])
                self._portfolio.positions[symbol] = Position(symbol, position_qty, avg_cost)
            elif short_qty > 0:
                avg_cost = float(last["short_avg_cost"])
                self._portfolio.short_positions[symbol] = Position(symbol, short_qty, avg_cost)

            logger.info(
                "Portfolio reconstructed from last trade row. Cash=$%.2f, "
                "long=%.8f, short=%.8f",
                self._portfolio.cash, position_qty, short_qty,
            )
        else:
            # Legacy fallback: replay all trades.
            logger.info("Trade log missing position columns — replaying all %d trades", len(actions))
            for _, row in actions.iterrows():
                action = row["action"]
                qty = float(row["quantity"])
                price = float(row["price"])

                details = row.get("details", {})
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except Exception:
                        details = {}
                if details.get("portfolio_error"):
                    logger.warning(
                        "Skipping trade with portfolio_error: %s %.8f @ %.2f",
                        action, qty, price,
                    )
                    continue

                try:
                    self._apply_to_portfolio(OrderSide(action), symbol, qty, price)
                except ValueError as e:
                    logger.error(
                        "Portfolio reconstruction error on %s %.8f @ %.2f: %s — skipping",
                        action, qty, price, e,
                    )

            logger.info(
                "Portfolio reconstructed from %d trades. Cash=$%.2f",
                len(actions), self._portfolio.cash,
            )

        # Cross-check cash against last trade log entry
        last_logged_cash = float(trades.iloc[-1]["cash_balance"])
        diff = abs(self._portfolio.cash - last_logged_cash)
        if diff > 0.02:
            logger.warning(
                "Portfolio cross-check MISMATCH: reconstructed=$%.2f, logged=$%.2f (diff=$%.2f)",
                self._portfolio.cash, last_logged_cash, diff,
            )
        else:
            logger.info("Portfolio cross-check passed (cash diff: $%.4f)", diff)

    # ── Internal ───────────────────────────────────────────────────────

    def _compute_quantity(self, symbol: str, side: OrderSide,
                          price: float, notional: float | None) -> float:
        """Compute order quantity from notional or available capital/position."""
        if side in (OrderSide.BUY, OrderSide.SHORT):
            # Entry: use notional or available cash minus reserve
            if notional is None:
                notional = self._portfolio.cash * (1 - CASH_RESERVE_FRACTION)
            if self._max_notional_pct:
                prices = {symbol: price}
                portfolio_value = self._portfolio.total_value(prices)
                pct_cap = portfolio_value * self._max_notional_pct / 100.0
                notional = min(notional, pct_cap)
                logger.info("  Pct cap: %.1f%% of $%.2f = $%.2f",
                            self._max_notional_pct, portfolio_value, pct_cap)
            return notional / price
        else:
            # Exit: close full position
            pos = self.get_position(symbol)
            if pos is None:
                return 0.0
            return pos["qty"]

    def _apply_to_portfolio(self, side: OrderSide, symbol: str,
                            qty: float, price: float) -> None:
        """Apply a trade to the internal portfolio."""
        if side == OrderSide.BUY:
            self._portfolio.buy(symbol, qty, price)
        elif side == OrderSide.SELL:
            self._portfolio.sell(symbol, qty, price)
        elif side == OrderSide.SHORT:
            self._portfolio.short_sell(symbol, qty, price)
        elif side == OrderSide.COVER:
            self._portfolio.cover(symbol, qty, price)
