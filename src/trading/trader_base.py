"""TraderBase — abstract base class for trading bots with ZMQ dashboard communication."""

import json
import logging
import os
import signal
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Heartbeat interval in seconds
HEARTBEAT_INTERVAL = 5


class TraderBase(ABC):
    """Base class for trading bots. Provides ZMQ communication, pause/resume, and force-close.

    Subclasses must implement:
        _startup_hook()           — Initialize subclass-specific components
        _tick()                   — One iteration of the main loop
        _sleep_until_next_event() — Sleep until next action (must call _sleep_with_zmq_poll)
        _get_portfolio_state()    — Return dict with portfolio_value, cash, position, etc.
        _force_close()            — Force-close current position
    """

    def __init__(self, config: dict):
        self.config = config
        self.running = False
        self.paused = False

        pt = config["paper_trading"]
        self.symbol = pt["symbol"]
        self.initial_cash = pt["initial_cash"]

        strat = config["strategy"]
        self.strategy_type = strat["type"]
        self.strategy_params = strat.get("params", {})
        self.strategy_version = strat.get("version", "")

        # ZMQ config
        self.trader_name = config.get("trader_name", f"{self.symbol}_{self.strategy_type}")
        zmq_cfg = config.get("zmq", {})
        self.zmq_endpoint = zmq_cfg.get("endpoint", "")

        # ZMQ state (initialized in _init_zmq)
        self._zmq_ctx = None
        self._zmq_socket = None
        self._zmq_poller = None
        self._zmq_enabled = False
        self._last_heartbeat = 0.0

    # ── Lifecycle ──────────────────────────────────────────────────────

    def startup(self):
        """Initialize ZMQ + subclass components."""
        self._init_zmq()
        self._startup_hook()
        self._send_hello()

    def run(self):
        """Main event loop — runs until SIGTERM/SIGINT or quit command."""
        self.running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info("Entering main loop.")

        try:
            while self.running:
                try:
                    self._process_zmq_messages()
                    self._tick()
                except Exception as e:
                    logger.error("Error in main loop tick: %s", e, exc_info=True)

                self._maybe_send_heartbeat()
                self._sleep_until_next_event()

        except Exception as e:
            logger.error("Unhandled exception in main loop: %s", e, exc_info=True)
            sys.exit(1)
        finally:
            self._shutdown()

    def _shutdown(self):
        """Clean shutdown: close ZMQ, call subclass cleanup."""
        logger.info("Shutting down...")
        self._shutdown_hook()
        self._close_zmq()
        logger.info("Shutdown complete.")
        logger.info("=" * 60)

    def _handle_signal(self, signum, frame):
        """Handle SIGTERM/SIGINT for clean shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — shutting down gracefully.", sig_name)
        self.running = False

    # ── ZMQ ────────────────────────────────────────────────────────────

    def _init_zmq(self):
        """Initialize ZMQ DEALER socket. No-op if zmq endpoint not configured."""
        if not self.zmq_endpoint:
            logger.info("ZMQ not configured — running without dashboard connection.")
            return

        try:
            import zmq
            self._zmq_ctx = zmq.Context()
            self._zmq_socket = self._zmq_ctx.socket(zmq.DEALER)
            self._zmq_socket.identity = self.trader_name.encode()
            self._zmq_socket.setsockopt(zmq.LINGER, 1000)
            self._zmq_socket.setsockopt(zmq.RCVTIMEO, 0)  # non-blocking recv
            self._zmq_socket.connect(self.zmq_endpoint)
            self._zmq_poller = zmq.Poller()
            self._zmq_poller.register(self._zmq_socket, zmq.POLLIN)
            self._zmq_enabled = True
            logger.info("ZMQ connected to %s (identity=%s)", self.zmq_endpoint, self.trader_name)
        except ImportError:
            logger.warning("pyzmq not installed — running without dashboard connection.")
        except Exception as e:
            logger.warning("ZMQ init failed: %s — running without dashboard connection.", e)

    def _close_zmq(self):
        """Clean up ZMQ resources."""
        if self._zmq_socket:
            try:
                self._zmq_socket.close()
            except Exception:
                pass
        if self._zmq_ctx:
            try:
                self._zmq_ctx.term()
            except Exception:
                pass

    def _send_zmq(self, msg: dict):
        """Send a JSON message to the dashboard via ZMQ. No-op if not connected."""
        if not self._zmq_enabled:
            return
        try:
            self._zmq_socket.send_json(msg)
        except Exception as e:
            logger.debug("ZMQ send failed: %s", e)

    def _process_zmq_messages(self):
        """Process all pending ZMQ messages (non-blocking)."""
        if not self._zmq_enabled:
            return
        try:
            while True:
                events = dict(self._zmq_poller.poll(0))
                if self._zmq_socket not in events:
                    break
                frames = self._zmq_socket.recv_multipart()
                msg = json.loads(frames[-1])
                self._handle_zmq_message(msg)
        except Exception as e:
            logger.debug("ZMQ recv error: %s", e)

    def _handle_zmq_message(self, msg: dict):
        """Dispatch incoming ZMQ message."""
        msg_type = msg.get("type")
        logger.info("ZMQ received: %s", msg_type)

        if msg_type == "request_info":
            self._handle_request_info(msg)
        elif msg_type == "exit_trade":
            self._handle_exit_trade()
        elif msg_type == "pause":
            self.paused = True
            self._send_zmq({"type": "paused_ack", "paused": True})
            logger.info("Bot paused.")
        elif msg_type == "resume":
            self.paused = False
            self._send_zmq({"type": "paused_ack", "paused": False})
            logger.info("Bot resumed.")
        elif msg_type == "quit":
            logger.info("Received quit command from dashboard.")
            self.running = False
        else:
            logger.warning("Unknown ZMQ message type: %s", msg_type)

    def _handle_request_info(self, msg: dict):
        """Handle request_info: respond with requested data."""
        request_id = msg.get("request_id", "")
        requests = msg.get("requests", [])

        for req in requests:
            params = msg.get("params", {}).get(req, {})
            if req == "profile":
                self._send_zmq({
                    "type": "profile",
                    "request_id": request_id,
                    "name": self.trader_name,
                    "pid": os.getpid(),
                    "strategy": self.strategy_type,
                    "version": self.strategy_version,
                    "exchange": self.config.get("exchange", {}).get("type", ""),
                    "symbol": self.symbol,
                    "initial_cash": self.initial_cash,
                })
            elif req == "portfolio":
                state = self._get_portfolio_state()
                self._send_zmq({
                    "type": "portfolio",
                    "request_id": request_id,
                    **state,
                })
            elif req == "trades":
                self._send_trades_info(request_id, params)
            elif req == "pnl_info":
                self._send_pnl_info(request_id)
            elif req == "price_data_path":
                self._send_price_data_path(request_id)

    def _handle_exit_trade(self):
        """Force-close current position."""
        logger.info("Received exit_trade command.")
        try:
            self._force_close()
        except Exception as e:
            logger.error("Force close failed: %s", e, exc_info=True)

    # ── ZMQ Outbound Messages ─────────────────────────────────────────

    def _send_hello(self):
        """Send hello message on startup."""
        self._send_zmq({
            "type": "hello",
            "name": self.trader_name,
            "pid": os.getpid(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "strategy": self.strategy_type,
            "version": self.strategy_version,
            "exchange": self.config.get("exchange", {}).get("type", ""),
            "symbol": self.symbol,
        })

    def _maybe_send_heartbeat(self):
        """Send status_update heartbeat every HEARTBEAT_INTERVAL seconds."""
        now = time.time()
        if now - self._last_heartbeat < HEARTBEAT_INTERVAL:
            return
        self._last_heartbeat = now

        state = self._get_portfolio_state()
        self._send_zmq({
            "type": "status_update",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "paused": self.paused,
            **state,
        })

    def _send_trade_event(self, event_type: str, action: str, price: float,
                          quantity: float, details: dict | None = None):
        """Send trade_entry or trade_exit event to dashboard."""
        msg = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "price": price,
            "qty": quantity,
        }
        if details:
            for key in ("pnl_pct", "reason", "duration_minutes"):
                if key in details:
                    msg[key] = details[key]
        self._send_zmq(msg)

    # ── Sleep with ZMQ Polling ────────────────────────────────────────

    def _sleep_with_zmq_poll(self, seconds: float):
        """Sleep for `seconds` while polling ZMQ every 1s for incoming commands."""
        end_time = time.time() + seconds
        while self.running and time.time() < end_time:
            remaining = end_time - time.time()
            poll_ms = int(min(1000, max(0, remaining * 1000)))

            if self._zmq_enabled:
                try:
                    events = dict(self._zmq_poller.poll(poll_ms))
                    if self._zmq_socket in events:
                        frames = self._zmq_socket.recv_multipart()
                        msg = json.loads(frames[-1])
                        self._handle_zmq_message(msg)
                except Exception:
                    time.sleep(min(1.0, max(0, remaining)))
            else:
                time.sleep(min(1.0, max(0, remaining)))

            self._maybe_send_heartbeat()

    # ── Abstract Methods ──────────────────────────────────────────────

    @abstractmethod
    def _startup_hook(self):
        """Initialize subclass-specific components (exchange, strategy, etc.)."""

    @abstractmethod
    def _tick(self):
        """One iteration of the main loop."""

    @abstractmethod
    def _sleep_until_next_event(self):
        """Sleep until the next event. Must use _sleep_with_zmq_poll() for the wait."""

    @abstractmethod
    def _get_portfolio_state(self) -> dict:
        """Return current portfolio state as a dict.

        Expected keys: portfolio_value, cash, position, position_qty, position_avg_cost, last_price
        """

    @abstractmethod
    def _force_close(self):
        """Force-close current position immediately (market close)."""

    def _shutdown_hook(self):
        """Subclass cleanup on shutdown. Override as needed."""

    def _send_trades_info(self, request_id: str, params: dict):
        """Send trade list. Override in subclass."""

    def _send_pnl_info(self, request_id: str):
        """Send PnL info paths. Override in subclass."""

    def _send_price_data_path(self, request_id: str):
        """Send price data location. Override in subclass."""
