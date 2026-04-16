"""CoinbaseBroker — live order execution via Coinbase Advanced Trade API.

Uses ChaseEngine for adaptive limit order execution.
Portfolio state is read from CFM (Coinbase Financial Markets) endpoints.
Enforces no-leverage: total notional exposure <= available USD balance.

Supports nano BTC futures (e.g. BIT-24APR26-CDE) where:
- 1 contract = contract_size BTC (e.g. 0.01 BTC)
- Orders are in whole contracts (base_increment = 1)
- Notional = num_contracts × contract_size × price
"""

import logging
import time

from exchange.coinbase_rest import CoinbaseRestClient
from strategies.base import PortfolioView

from brokers.base import (
    BrokerBase, BrokerCapabilities, FillResult, OrderSide, OrderStatus, PortfolioSnapshot,
)
from brokers.chase_engine import ChaseEngine, ChaseResult

logger = logging.getLogger(__name__)

# Map strategy-level sides to Coinbase API sides
_COINBASE_SIDE = {
    OrderSide.BUY: "BUY",
    OrderSide.SELL: "SELL",
    OrderSide.SHORT: "SELL",   # short = sell to open
    OrderSide.COVER: "BUY",   # cover = buy to close
}


class CoinbaseBroker(BrokerBase):
    """Live broker for Coinbase Advanced Trade (CFM futures).

    - Orders are executed via ChaseEngine (adaptive limit + market fallback).
    - Portfolio state from CFM balance/positions endpoints.
    - No leverage: notional exposure <= available USD (not just margin).
    - Orders in whole contracts.
    """

    def __init__(self, exchange: CoinbaseRestClient):
        if not isinstance(exchange, CoinbaseRestClient):
            raise TypeError("CoinbaseBroker requires a CoinbaseRestClient instance")
        self.exchange = exchange
        self._chase: ChaseEngine | None = None
        self._chase_config: dict = {}
        self._order_counter = 0
        self._current_order_id: str | None = None
        self._current_order_side: OrderSide | None = None
        self._current_order_symbol: str | None = None

        # Config — set during startup()
        self._max_notional_usd: float = 1500
        self._max_notional_pct: float | None = None
        self._base_increment: int = 1          # whole contracts
        self._base_min_size: int = 1
        self._contract_size: float = 0.01      # BTC per contract
        self._quote_increment: float = 5.0

    # ── Lifecycle ──────────────────────────────────────────────────────

    def startup(self, config: dict) -> None:
        self._max_notional_usd = config.get("max_notional_usd", 1500)
        self._max_notional_pct = config.get("max_notional_pct")
        self._chase_config = config.get("chase", {})

        # Fetch product specs
        specs = self.exchange.get_product_specs()
        self._base_increment = int(specs["base_increment"])
        self._base_min_size = int(specs["base_min_size"])
        self._contract_size = specs["contract_size"]
        self._quote_increment = specs["quote_increment"]

        # Verify account access via CFM balance
        balance = self.exchange.get_cfm_balance()

        logger.info("=" * 60)
        logger.info("CoinbaseBroker started")
        logger.info("  Product: %s", self.exchange.product_id)
        logger.info("  Contract size: %s BTC", self._contract_size)
        logger.info("  USD balance: $%.2f (buying power: $%.2f)",
                     balance["total_usd_balance"], balance["buying_power"])
        logger.info("  Max notional: $%.2f", self._max_notional_usd)
        if self._max_notional_pct:
            logger.info("  Max notional pct: %.1f%%", self._max_notional_pct)
        logger.info("  Expiry: %s", specs.get("contract_expiry", "N/A"))
        logger.info("  Chase config: %s", self._chase_config)
        logger.info("=" * 60)

    def shutdown(self) -> None:
        if self._chase and self._chase.pending:
            logger.info("CoinbaseBroker shutdown: cancelling pending chase")
            self._chase.cancel()
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

    # ── Asset metadata ─────────────────────────────────────────────────

    def get_contract_size(self, symbol: str) -> float:
        if symbol == self.exchange.product_id:
            return self._contract_size
        return 1.0

    # ── Portfolio ──────────────────────────────────────────────────────

    def get_portfolio_snapshot(self, prices: dict[str, float] | None = None) -> PortfolioSnapshot:
        balance = self.exchange.get_cfm_balance()
        cfm_positions = self.exchange.get_cfm_positions()

        positions = {}
        for p in cfm_positions:
            notional = p["number_of_contracts"] * self._contract_size * p["current_price"]
            positions[p["product_id"]] = {
                "qty": p["number_of_contracts"],
                "avg_cost": p["avg_entry_price"],
                "side": p["side"],
                "unrealized_pnl": p["unrealized_pnl"],
                "notional": notional,
            }

        return PortfolioSnapshot(
            cash=balance["total_usd_balance"],
            positions=positions,
            total_value=balance["total_usd_balance"] + balance["unrealized_pnl"],
        )

    def get_position(self, symbol: str) -> dict | None:
        cfm_positions = self.exchange.get_cfm_positions()
        for p in cfm_positions:
            if p["product_id"] == symbol:
                return {
                    "qty": p["number_of_contracts"],
                    "avg_cost": p["avg_entry_price"],
                    "side": p["side"],
                }
        return None

    def portfolio_view(self, symbol: str) -> PortfolioView:
        balance = self.exchange.get_cfm_balance()
        pos = self.get_position(symbol)

        position_qty = 0.0
        position_avg_cost = 0.0
        short_qty = 0.0
        short_avg_cost = 0.0

        if pos is not None:
            if pos["side"] == "LONG":
                position_qty = float(pos["qty"])
                position_avg_cost = pos["avg_cost"]
            else:
                short_qty = float(pos["qty"])
                short_avg_cost = pos["avg_cost"]

        return PortfolioView(
            cash=balance["total_usd_balance"],
            position_qty=position_qty,
            position_avg_cost=position_avg_cost,
            short_qty=short_qty,
            short_avg_cost=short_avg_cost,
        )

    # ── Orders ─────────────────────────────────────────────────────────

    def submit_order(self, symbol: str, side: OrderSide,
                     notional: float | None = None) -> str:
        if self._current_order_id is not None:
            raise RuntimeError(
                f"Cannot submit order: pending order {self._current_order_id} exists"
            )

        price = self.exchange.get_current_price(symbol)
        num_contracts = self._compute_contracts(symbol, side, price, notional)

        if num_contracts <= 0:
            raise ValueError(f"Computed 0 contracts for {side.value} {symbol}")

        coinbase_side = _COINBASE_SIDE[side]

        # Start the chase — quantity is in contracts
        self._chase = ChaseEngine(self.exchange, self._chase_config)
        self._chase.start(coinbase_side, num_contracts)

        self._order_counter += 1
        self._current_order_id = f"cb_{self._order_counter}"
        self._current_order_side = side
        self._current_order_symbol = symbol

        contract_notional = num_contracts * self._contract_size * price
        logger.info(
            "CoinbaseBroker: submitted %s %s %d contracts "
            "(%.4f BTC, notional=$%.2f, order %s)",
            side.value, symbol, num_contracts,
            num_contracts * self._contract_size, contract_notional,
            self._current_order_id,
        )
        return self._current_order_id

    def check_order(self, order_id: str) -> FillResult | None:
        if order_id != self._current_order_id:
            raise ValueError(f"Unknown order_id: {order_id}")
        if self._chase is None or self._chase.pending is None:
            raise RuntimeError("No pending chase to check")

        result, details = self._chase.check()

        if result == ChaseResult.WAITING:
            return None

        side = self._current_order_side
        symbol = self._current_order_symbol

        if result in (ChaseResult.FILLED, ChaseResult.MARKET_FALLBACK):
            fill_price = details["fill_price"]
            self._current_order_id = None
            self._current_order_side = None
            self._current_order_symbol = None
            return FillResult(
                status=OrderStatus.FILLED,
                side=side,
                symbol=symbol,
                filled_qty=details["quantity"],
                filled_price=fill_price,
                fill_type=details["fill_type"],
                details=details,
            )

        # CANCELLED
        self._current_order_id = None
        self._current_order_side = None
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
        if self._chase:
            self._chase.cancel()
        self._current_order_id = None
        self._current_order_side = None
        self._current_order_symbol = None
        logger.info("CoinbaseBroker: cancelled order %s", order_id)
        return True

    def has_pending_order(self) -> bool:
        return self._current_order_id is not None

    def get_pending_order_info(self) -> dict | None:
        if self._chase and self._chase.pending:
            return {
                "order_id": self._current_order_id,
                "symbol": self._current_order_symbol,
                "side": self._current_order_side.value if self._current_order_side else None,
                **self._chase.pending,
            }
        return None

    # ── Emergency ──────────────────────────────────────────────────────

    def emergency_close(self, symbol: str) -> FillResult | None:
        # Cancel any pending chase first
        if self._current_order_id:
            self.cancel_order(self._current_order_id)

        # Query position from CFM
        pos = self.get_position(symbol)
        if pos is None:
            logger.info("CoinbaseBroker.emergency_close(%s): already flat", symbol)
            return None

        # Determine close side
        if pos["side"] == "LONG":
            close_side_cb = "SELL"
            close_side = OrderSide.SELL
        else:
            close_side_cb = "BUY"
            close_side = OrderSide.COVER

        num_contracts = pos["qty"]

        logger.warning(
            "CoinbaseBroker EMERGENCY CLOSE: %s %d contracts %s via market IOC",
            close_side_cb, num_contracts, symbol,
        )

        # Market IOC to close immediately
        market_order = self.exchange.place_market_order_ioc(
            side=close_side_cb, base_size=num_contracts,
        )

        # Brief wait then check
        time.sleep(0.5)
        status = self.exchange.get_order(market_order["order_id"])

        if status["status"] == "FILLED":
            return FillResult(
                status=OrderStatus.FILLED,
                side=close_side,
                symbol=symbol,
                filled_qty=status["filled_size"],
                filled_price=status["average_filled_price"],
                fill_type="market_emergency",
                details={"reason": "emergency_close", "fees": status["total_fees"]},
            )

        logger.error(
            "Emergency close market order %s status=%s (expected FILLED)",
            market_order["order_id"], status["status"],
        )
        return None

    # ── Crash Recovery ─────────────────────────────────────────────────

    def export_state(self) -> dict:
        state = {
            "broker_type": "coinbase",
            "order_counter": self._order_counter,
            "max_notional_usd": self._max_notional_usd,
            "max_notional_pct": self._max_notional_pct,
        }

        if self._chase and self._chase.pending:
            state["pending_chase"] = {
                "order_id": self._current_order_id,
                "side": self._current_order_side.value if self._current_order_side else None,
                "symbol": self._current_order_symbol,
                **self._chase.pending,
            }

        return state

    def import_state(self, state: dict) -> None:
        self._order_counter = state.get("order_counter", 0)

        pending = state.get("pending_chase")
        if pending:
            order_id = pending.pop("order_id", None)
            side_str = pending.pop("side", None)
            symbol = pending.pop("symbol", None)
            if order_id and symbol:
                self._current_order_id = order_id
                self._current_order_side = OrderSide(side_str) if side_str else None
                self._current_order_symbol = symbol
                self._chase = ChaseEngine(self.exchange, self._chase_config)
                self._chase.resume(pending)
                logger.info(
                    "CoinbaseBroker: resumed pending chase %s (%s %s)",
                    order_id, side_str, symbol,
                )

        logger.info("CoinbaseBroker: state imported (order_counter=%d)", self._order_counter)

    # ── Internal ───────────────────────────────────────────────────────

    def _get_contract_size(self, product_id: str) -> float:
        """Get contract size for a product, with caching."""
        if product_id == self.exchange.product_id:
            return self._contract_size
        if not hasattr(self, "_contract_size_cache"):
            self._contract_size_cache: dict[str, float] = {}
        if product_id not in self._contract_size_cache:
            from exchange.coinbase_rest import CoinbaseRestClient
            client = CoinbaseRestClient({"product_id": product_id})
            specs = client.get_product_specs()
            self._contract_size_cache[product_id] = specs["contract_size"]
        return self._contract_size_cache[product_id]

    def _compute_contracts(self, symbol: str, side: OrderSide,
                           price: float, notional: float | None) -> int:
        """Compute number of contracts to trade.

        For entries: notional / (contract_size × price), rounded DOWN to whole contracts,
        capped by max_notional_usd and available balance (no leverage).
        For exits: full position size from exchange.
        """
        if side in (OrderSide.BUY, OrderSide.SHORT):
            balance = self.exchange.get_cfm_balance()
            equity = balance["total_usd_balance"]

            # Compute unleveraged available cash: equity minus notional of all open positions
            # Notional per position = contracts × contract_size × current_price
            positions = self.exchange.get_cfm_positions()
            committed = 0.0
            for p in positions:
                cs = self._get_contract_size(p["product_id"])
                committed += p["number_of_contracts"] * cs * p["current_price"]
            available = max(equity - committed, 0.0)
            logger.info("  Unleveraged available: equity=$%.2f - committed=$%.2f = $%.2f",
                        equity, committed, available)

            # Cap by max notional and available cash (no leverage)
            max_notional = min(available, self._max_notional_usd)
            if self._max_notional_pct:
                pct_cap = equity * self._max_notional_pct / 100.0
                max_notional = min(max_notional, pct_cap)
                logger.info("  Pct cap: %.1f%% of equity $%.2f = $%.2f",
                            self._max_notional_pct, equity, pct_cap)
            if notional is not None:
                max_notional = min(max_notional, notional)

            # Notional per contract = contract_size × price
            notional_per_contract = self._contract_size * price
            num_contracts = int(max_notional / notional_per_contract)

            if num_contracts < self._base_min_size:
                logger.warning(
                    "Computed %d contracts, below minimum %d for %s "
                    "(available=$%.2f, notional_per_contract=$%.2f)",
                    num_contracts, self._base_min_size, symbol,
                    available, notional_per_contract,
                )
                return 0

            logger.info(
                "Position sizing: available=$%.2f, max_notional=$%.2f "
                "(usd_cap=$%.2f, pct_cap=%s), price=%.2f, contract_size=%s → %d contracts "
                "(notional=$%.2f, BTC=%.4f)",
                available, max_notional,
                self._max_notional_usd,
                f"{self._max_notional_pct}%" if self._max_notional_pct else "none",
                price, self._contract_size, num_contracts,
                num_contracts * notional_per_contract,
                num_contracts * self._contract_size,
            )
            return num_contracts
        else:
            # Exit: close full position
            pos = self.get_position(symbol)
            if pos is None:
                logger.warning("No open position for %s — cannot compute exit qty", symbol)
                return 0

            num_contracts = pos["qty"]
            logger.info(
                "Exit sizing: %s %s %d contracts (%.4f BTC)",
                pos["side"], symbol, num_contracts,
                num_contracts * self._contract_size,
            )
            return num_contracts
