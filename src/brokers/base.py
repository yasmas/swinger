"""Broker interface — abstract base for order execution and portfolio management."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from strategies.base import PortfolioView


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SHORT = "SHORT"
    COVER = "COVER"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class FillResult:
    """Result of a completed (terminal) order."""
    status: OrderStatus
    side: OrderSide
    symbol: str
    filled_qty: float
    filled_price: float
    fill_type: str          # "limit", "market", "market_abort", "market_timeout", etc.
    details: dict = field(default_factory=dict)


@dataclass
class PortfolioSnapshot:
    """Point-in-time snapshot of broker portfolio state."""
    cash: float
    positions: dict         # symbol -> {qty, avg_cost, side}
    total_value: float


@dataclass
class BrokerCapabilities:
    """What this broker supports."""
    supports_shorting: bool
    supports_margin: bool
    supports_leverage: bool
    max_leverage: float | None
    supported_order_types: list[str] = field(default_factory=lambda: ["limit", "market"])


class BrokerBase(ABC):
    """Abstract base for order execution and portfolio management.

    One instance per exchange account. Methods take symbol as parameter
    so a single broker can handle multiple assets on the same API.
    The broker owns portfolio state and handles position sizing internally.
    """

    # ── Lifecycle ──────────────────────────────────────────────────────

    @abstractmethod
    def startup(self, config: dict) -> None:
        """Initialize broker, create portfolio, restore persisted state if any."""

    @abstractmethod
    def shutdown(self) -> None:
        """Clean shutdown: cancel pending orders, persist state."""

    # ── Capabilities ───────────────────────────────────────────────────

    @abstractmethod
    def capabilities(self) -> BrokerCapabilities:
        """Return what this broker supports (shorting, margin, etc.)."""

    # ── Portfolio ──────────────────────────────────────────────────────

    @abstractmethod
    def get_portfolio_snapshot(self, prices: dict[str, float] | None = None) -> PortfolioSnapshot:
        """Return current portfolio state.

        Args:
            prices: Current market prices by symbol. Required for
                    accurate total_value when holding positions.
        """

    @abstractmethod
    def get_position(self, symbol: str) -> dict | None:
        """Return position info for symbol, or None if flat.

        Returns dict with keys: qty, avg_cost, side ("LONG" or "SHORT").
        """

    @abstractmethod
    def portfolio_view(self, symbol: str) -> PortfolioView:
        """Build a frozen PortfolioView for the strategy layer."""

    # ── Orders ─────────────────────────────────────────────────────────

    @abstractmethod
    def submit_order(self, symbol: str, side: OrderSide,
                     notional: float | None = None) -> str:
        """Submit an order. Broker handles sizing internally.

        Args:
            symbol: Trading pair / instrument.
            side: BUY, SELL, SHORT, or COVER.
            notional: Desired notional value. None means full available
                      capital for entries, or full position for exits.

        Returns:
            order_id string for tracking.
        """

    @abstractmethod
    def check_order(self, order_id: str) -> FillResult | None:
        """Poll order status.

        Returns FillResult if the order reached a terminal state
        (filled, cancelled, rejected), or None if still pending.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order. Returns True if successfully cancelled."""

    @abstractmethod
    def has_pending_order(self) -> bool:
        """Return True if there is a pending (non-terminal) order."""

    @abstractmethod
    def get_pending_order_info(self) -> dict | None:
        """Return metadata about the pending order, or None if no pending order."""

    # ── Emergency ──────────────────────────────────────────────────────

    @abstractmethod
    def emergency_close(self, symbol: str) -> FillResult | None:
        """Market-close all positions in symbol immediately.

        Cancels any pending orders first. Returns FillResult if a
        position was closed, None if already flat.
        """

    # ── Crash Recovery ─────────────────────────────────────────────────

    @abstractmethod
    def export_state(self) -> dict:
        """Serialize broker state for crash recovery.

        Returns a plain dict safe for YAML/JSON serialization,
        including portfolio positions and any pending order.
        """

    @abstractmethod
    def import_state(self, state: dict) -> None:
        """Restore broker state from a previously exported dict."""
