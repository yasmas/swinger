"""SwingBot — broker-agnostic trading bot daemon."""

import atexit
import csv
import fcntl
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from brokers.base import FillResult, OrderSide, OrderStatus
from brokers.registry import BROKER_REGISTRY
from exchange.registry import create_exchange
from trading.data_manager import DataManager, FIVE_MIN_MS
from trading.logging_config import setup_logging
from trading.state_manager import StateManager
from trading.strategy_runner import StrategyRunner
from reporting.reporter import Reporter
from strategies.base import ActionType
from strategies.registry import get_display_name
from trade_log import TRADE_LOG_COLUMNS
from trading.trader_base import TraderBase

logger = logging.getLogger(__name__)


class SwingBot(TraderBase):
    """Broker-agnostic trading bot. Uses a pluggable Broker for order execution
    and portfolio management.

    Replaces PaperTrader with a cleaner separation of concerns:
    - SwingBot: orchestration, data, strategy, reporting, ZMQ
    - Broker: orders, fills, portfolio state, sizing
    """

    def __init__(self, config: dict):
        super().__init__(config)

        bot_cfg = config.get("bot") or config.get("paper_trading", {})
        self.data_dir = bot_cfg["data_dir"]
        self.state_file = bot_cfg["state_file"]
        self.warm_up_hours = bot_cfg.get("warm_up_hours", 250)

        rpt = config.get("reporting", {})
        self.trade_log_path = rpt.get("trade_log", f"{self.data_dir}/trades.csv")
        self.report_output_dir = rpt.get("output_dir", "reports/live")
        self.report_file = rpt.get("report_file", "reports/live/report.html")
        self.cost_per_trade_pct = rpt.get("cost_per_trade_pct", 0.05)

        self.exchange = None
        self.broker = None
        self.data_manager = None
        self.state_manager = None
        self.strategy_runner = None
        self.reporter = None

        self._df_5m = None
        self._df_1h = None
        self._current_order_id = None

        fetch_cfg = bot_cfg.get("fetch", {})
        self._fetch_delay_seconds = fetch_cfg.get("delay_seconds", 3)
        self._fetch_poll_interval = fetch_cfg.get("poll_interval_seconds", 3.0)
        self._fetch_timeout = fetch_cfg.get("timeout_seconds", 30)

    # ── TraderBase Overrides ──────────────────────────────────────────

    def _startup_hook(self):
        """Initialize all SwingBot components in the correct order."""
        broker_cfg = self.config.get("broker", {"type": "paper"})
        broker_type = broker_cfg.get("type", "paper")

        logger.info("=" * 60)
        logger.info("SwingBot starting up")
        logger.info("  Symbol: %s", self.symbol)
        logger.info("  Strategy: %s %s", self.strategy_type, self.strategy_version)
        logger.info("  Broker: %s", broker_type)
        logger.info("  Initial cash: $%.2f", self.initial_cash)
        logger.info("  Data dir: %s", self.data_dir)
        logger.info("=" * 60)

        # 1. Exchange client
        ex_cfg = self.config.get("exchange", {})
        self.exchange = create_exchange(ex_cfg)
        logger.info("Exchange client initialized: %s (%s)", ex_cfg.get("type", "binance"), self.exchange.base_url)

        # 2. Data manager — backfill + load
        # Use the larger of config warm_up_hours and strategy's minimum requirement
        strategy_min = StrategyRunner.get_min_warmup_hours(
            self.strategy_type, self.strategy_params,
        )
        effective_warmup = max(self.warm_up_hours, strategy_min)
        if effective_warmup > self.warm_up_hours:
            logger.info(
                "Strategy requires %d warmup hours (config: %d). Using %d.",
                strategy_min, self.warm_up_hours, effective_warmup,
            )
        self.data_manager = DataManager(
            self.exchange, self.symbol, self.data_dir,
            warm_up_hours=effective_warmup,
        )
        self._df_5m, self._df_1h = self.data_manager.startup()

        # 3. State manager
        self.state_manager = StateManager(self.state_file)
        state = self.state_manager.load()

        # 4. Broker — create and initialize
        if broker_type not in BROKER_REGISTRY:
            raise ValueError(
                f"Unknown broker type: '{broker_type}'. "
                f"Available: {list(BROKER_REGISTRY.keys())}"
            )
        broker_cls = BROKER_REGISTRY[broker_type]
        self.broker = broker_cls(self.exchange)

        # If we have persisted broker state, restore it; otherwise startup fresh
        broker_state = state.get("broker_state")
        if broker_state:
            # Set fulfillment config before import_state (needed for pending order resume)
            self.broker._fulfillment_config = broker_cfg.get("fulfillment", {})
            self.broker.import_state(broker_state)
        else:
            self.broker.startup(broker_cfg)
            # First startup or upgrading from old state format — reconstruct from trade log
            if Path(self.trade_log_path).exists():
                self.broker.reconstruct_from_trades(self.trade_log_path, self.symbol)
            # If old-format pending_order exists, migrate it
            if state.get("pending_order"):
                self._migrate_pending_order(state["pending_order"], broker_cfg)

        # Resume pending order tracking if broker has one
        if self.broker.has_pending_order():
            info = self.broker.get_pending_order_info()
            self._current_order_id = info.get("order_id") if info else None

        # Log capabilities
        caps = self.broker.capabilities()
        logger.info(
            "Broker capabilities: shorting=%s, margin=%s, leverage=%s",
            caps.supports_shorting, caps.supports_margin, caps.supports_leverage,
        )

        # 5. Strategy runner (no portfolio — gets portfolio_view from broker)
        exchange_price = None
        try:
            exchange_price = self.exchange.get_current_price(self.symbol)
        except Exception as e:
            logger.warning("Could not fetch exchange price for sanity check: %s", e)

        diagnostics_path = os.path.join(self.data_dir, "diagnostics.csv")
        self.strategy_runner = StrategyRunner(
            strategy_type=self.strategy_type,
            strategy_params=self.strategy_params,
            symbol=self.symbol,
            diagnostics_path=diagnostics_path,
        )
        self.strategy_runner.startup(
            self._df_5m, self._df_1h, exchange_price,
            strategy_state=state.get("strategy_state"),
        )

        # 6. Trade logger (append mode if file exists)
        self._init_trade_logger()

        # 7. Reporter
        self.reporter = Reporter(output_dir=self.report_output_dir)

        portfolio_value = self._get_portfolio_value()
        pending_info = self.broker.get_pending_order_info()
        logger.info(
            "Startup complete. Portfolio: $%.2f, Pending: %s",
            portfolio_value,
            pending_info.get("action") if pending_info else "none",
        )

    def run(self):
        """Main event loop — acquire lock first, then delegate to TraderBase.run()."""
        self._acquire_lock()
        super().run()

    def _tick(self):
        """One iteration of the main loop."""
        now = datetime.now(timezone.utc)

        if self.broker.has_pending_order():
            self._check_fulfillment(now)

        self._try_fetch_5m(now)

    def _sleep_until_next_event(self):
        """Sleep until the next 5m bar close (+ delay), or 1 min if fulfillment pending."""
        now = datetime.now(timezone.utc)
        has_pending = self.broker.has_pending_order()

        if has_pending:
            next_wake = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        else:
            now_ms = int(now.timestamp() * 1000)
            next_bar_close_ms = ((now_ms // FIVE_MIN_MS) + 1) * FIVE_MIN_MS
            next_wake = datetime.fromtimestamp(
                next_bar_close_ms / 1000 + self._fetch_delay_seconds,
                tz=timezone.utc,
            )

        sleep_seconds = max(0, (next_wake - datetime.now(timezone.utc)).total_seconds())

        if sleep_seconds > 0:
            logger.debug(
                "Sleeping %.0fs until %s (pending=%s)",
                sleep_seconds, next_wake.strftime("%H:%M:%S"), has_pending,
            )
            self._sleep_with_zmq_poll(sleep_seconds)

    def _get_portfolio_state(self) -> dict:
        """Return current portfolio state for ZMQ status updates."""
        last_price = 0.0
        if self._df_5m is not None and not self._df_5m.empty:
            last_price = float(self._df_5m.iloc[-1]["close"])

        snapshot = self.broker.get_portfolio_snapshot(
            {self.symbol: last_price} if last_price > 0 else None
        )
        pos = self.broker.get_position(self.symbol)

        return {
            "portfolio_value": snapshot.total_value,
            "cash": snapshot.cash,
            "position": pos["side"] if pos else "FLAT",
            "position_qty": pos["qty"] if pos else 0.0,
            "position_avg_cost": pos["avg_cost"] if pos else 0.0,
            "last_price": last_price,
        }

    def _force_close(self):
        """Force-close current position via broker emergency_close."""
        result = self.broker.emergency_close(self.symbol)
        if result and result.status == OrderStatus.FILLED:
            now = datetime.now(timezone.utc)
            self._log_trade(
                date=now.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                action=result.side.value,
                quantity=result.filled_qty,
                price=result.filled_price,
                details=result.details,
            )
            is_exit = result.side in (OrderSide.SELL, OrderSide.COVER)
            event_type = "trade_exit" if is_exit else "trade_entry"
            self._send_trade_event(
                event_type, result.side.value, result.filled_price,
                result.filled_qty, result.details,
            )
            self.strategy_runner.strategy.reset_position()

        self._current_order_id = None
        self._save_state()
        self._regenerate_report()

    def _shutdown_hook(self):
        """SwingBot cleanup: save state, close files, release lock."""
        self._save_state()

        if hasattr(self, "_trade_log_file") and self._trade_log_file:
            self._trade_log_file.close()

        self._release_lock()

        portfolio_value = self._get_portfolio_value()
        logger.info("Final portfolio value: $%.2f", portfolio_value)

    def _send_trades_info(self, request_id: str, params: dict):
        """Send recent trades from the trade log."""
        count = params.get("count", 100)
        trades = []
        trade_log = Path(self.trade_log_path)
        if trade_log.exists():
            import pandas as pd
            try:
                df = pd.read_csv(trade_log)
                for _, row in df.tail(count).iterrows():
                    trades.append({
                        "date": str(row.get("date", "")),
                        "action": str(row.get("action", "")),
                        "symbol": str(row.get("symbol", "")),
                        "qty": float(row.get("quantity", 0)),
                        "price": float(row.get("price", 0)),
                        "cash_balance": float(row.get("cash_balance", 0)),
                        "portfolio_value": float(row.get("portfolio_value", 0)),
                        "details": str(row.get("details", "{}")),
                    })
            except Exception as e:
                logger.warning("Failed to read trades for ZMQ: %s", e)

        self._send_zmq({
            "type": "trades",
            "request_id": request_id,
            "trades": trades,
        })

    def _send_pnl_info(self, request_id: str):
        """Send trade log path and initial cash for PnL computation."""
        self._send_zmq({
            "type": "pnl_info",
            "request_id": request_id,
            "trade_log_path": str(self.trade_log_path),
            "initial_cash": self.initial_cash,
        })

    def _send_price_data_path(self, request_id: str):
        """Send price data directory info."""
        self._send_zmq({
            "type": "price_data_path",
            "request_id": request_id,
            "data_dir": str(self.data_dir),
            "symbol": self.symbol,
            "interval": "5m",
            "file_pattern": f"{self.symbol}-5m-YYYY-MM.csv",
            "csv_columns": ["timestamp", "open", "high", "low", "close", "volume"],
        })

    # ── SwingBot-specific Methods ─────────────────────────────────────

    def _init_trade_logger(self):
        """Open the trade log CSV in append mode, creating with header if needed."""
        path = Path(self.trade_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0

        self._trade_log_file = open(path, "a", newline="")
        self._trade_log_writer = csv.writer(self._trade_log_file, quoting=csv.QUOTE_MINIMAL)
        if write_header:
            self._trade_log_writer.writerow(TRADE_LOG_COLUMNS)
            self._trade_log_file.flush()

    def _log_trade(self, date: str, action: str, quantity: float, price: float,
                   details: dict | None = None):
        """Append a row to the trade log CSV."""
        snapshot = self.broker.get_portfolio_snapshot({self.symbol: price})
        details_str = json.dumps(details) if details else "{}"
        self._trade_log_writer.writerow([
            date, action, self.symbol,
            f"{quantity:.8f}", f"{price:.2f}",
            f"{snapshot.cash:.2f}",
            f"{snapshot.total_value:.2f}",
            details_str,
        ])
        self._trade_log_file.flush()
        logger.info(
            "Trade logged: %s %.8f %s @ $%.2f (cash=$%.2f, value=$%.2f)",
            action, quantity, self.symbol, price,
            snapshot.cash, snapshot.total_value,
        )

    def _get_portfolio_value(self) -> float:
        """Get current portfolio value using latest local price."""
        if not self.broker:
            return self.initial_cash
        if self._df_5m is not None and not self._df_5m.empty:
            price = float(self._df_5m.iloc[-1]["close"])
        else:
            try:
                price = self.exchange.get_current_price(self.symbol)
            except Exception:
                price = 0.0
        snapshot = self.broker.get_portfolio_snapshot({self.symbol: price})
        return snapshot.total_value

    def _acquire_lock(self):
        """Acquire a file lock to prevent multiple instances."""
        bot_cfg = self.config.get("bot") or self.config.get("paper_trading", {})
        lock_path = Path(bot_cfg["data_dir"]) / "swing_bot.lock"
        self._lock_file = open(lock_path, "w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            logger.error("Another SwingBot instance is already running (lock: %s)", lock_path)
            sys.exit(1)
        self._lock_file.write(str(os.getpid()))
        self._lock_file.flush()
        atexit.register(self._release_lock)

    def _release_lock(self):
        """Release the file lock."""
        if hasattr(self, "_lock_file") and self._lock_file and not self._lock_file.closed:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()

    def _try_fetch_5m(self, now: datetime):
        """Attempt to fetch the latest closed 5m bar. Tight-polls if close was recent."""
        now_ms = int(now.timestamp() * 1000)
        current_bucket_start = (now_ms // FIVE_MIN_MS) * FIVE_MIN_MS
        ms_since_close = now_ms - current_bucket_start

        new_bar = self.data_manager.fetch_and_append_5m()

        if new_bar is None and ms_since_close < self._fetch_timeout * 1000:
            deadline = time.time() + self._fetch_timeout - ms_since_close / 1000
            while new_bar is None and self.running and time.time() < deadline:
                time.sleep(self._fetch_poll_interval)
                new_bar = self.data_manager.fetch_and_append_5m()

        if new_bar is None:
            return

        self._on_new_5m_bar(new_bar, now)

    def _on_new_5m_bar(self, new_bar, now: datetime):
        """Process a successfully fetched 5m bar."""
        self._df_5m = self.data_manager._load_recent("5m")

        is_hour = self.data_manager.is_hour_boundary(new_bar)

        if is_hour:
            hourly = self.data_manager.resample_latest_hour(self._df_5m)
            if hourly is not None:
                self.data_manager.append_1h(hourly)
                self._df_1h = self.data_manager._load_recent("1h")
            self._regenerate_report()

        if not self.broker.has_pending_order():
            self._evaluate_strategy(now)
        else:
            logger.debug("5m bar received but order pending — skipping strategy eval.")

        # Save state on every 5m bar for correct crash recovery
        self._save_state()

    def _evaluate_strategy(self, now: datetime):
        """Run strategy on_bar and submit order to broker if signal fires."""
        if self.paused:
            logger.debug("Strategy evaluation skipped — bot is paused.")
            return

        # Get portfolio view from broker
        pv = self.broker.portfolio_view(self.symbol)
        action = self.strategy_runner.on_5m_bar(self._df_5m, portfolio_view=pv)

        if action.action == ActionType.HOLD:
            return

        # Capability gate
        caps = self.broker.capabilities()
        if action.action in (ActionType.SHORT, ActionType.COVER) and not caps.supports_shorting:
            logger.warning(
                "Broker doesn't support shorting — ignoring %s signal",
                action.action.value,
            )
            return

        side = OrderSide(action.action.value)
        logger.info("Strategy signal: %s %s", side.value, self.symbol)
        self._current_order_id = self.broker.submit_order(self.symbol, side)

    def _check_fulfillment(self, now: datetime):
        """Poll broker for order status and handle terminal results."""
        if not self._current_order_id:
            return

        result = self.broker.check_order(self._current_order_id)

        if result is None:
            return  # still pending

        if result.status == OrderStatus.FILLED:
            self._log_trade(
                date=now.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                action=result.side.value,
                quantity=result.filled_qty,
                price=result.filled_price,
                details=result.details,
            )

            # Send ZMQ trade event
            is_entry = result.side in (OrderSide.BUY, OrderSide.SHORT)
            event_type = "trade_entry" if is_entry else "trade_exit"
            self._send_trade_event(
                event_type, result.side.value, result.filled_price,
                result.filled_qty, result.details,
            )

        elif result.status == OrderStatus.CANCELLED:
            logger.info("Order cancelled — no trade executed.")
        elif result.status == OrderStatus.REJECTED:
            logger.error(
                "Order rejected: %s",
                result.details.get("portfolio_error", "unknown"),
            )

        self._current_order_id = None
        self._save_state()
        self._regenerate_report()

    def _save_state(self):
        """Persist current state (broker state + strategy state)."""
        if not self.state_manager:
            return
        self.state_manager.save(
            broker_state=self.broker.export_state(),
            strategy_state=self.strategy_runner.get_strategy_state(),
        )

    def _regenerate_report(self):
        """Regenerate the HTML report from the current trade log."""
        if not Path(self.trade_log_path).exists():
            return
        try:
            report_path = self.reporter.generate(
                trade_log_path=self.trade_log_path,
                price_data=self._df_5m,
                strategy_name=self.strategy_type,
                symbol=self.symbol,
                initial_cash=self.initial_cash,
                version=self.strategy_version,
                output_filename=Path(self.report_file).name,
                auto_refresh_seconds=300,
            )
            logger.info("Report regenerated: %s", report_path)
        except Exception as e:
            logger.warning("Failed to regenerate report: %s", e)

    def _migrate_pending_order(self, pending_order: dict, broker_cfg: dict):
        """Migrate a pending order from old PaperTrader state format.

        The old format stored pending_order at the top level of the state file.
        The new format stores it inside broker_state.
        """
        logger.info("Migrating pending order from old state format: %s", pending_order.get("action"))
        symbol = self.symbol
        self.broker._fulfillment_config = broker_cfg.get("fulfillment", {})

        from brokers.fulfillment import FulfillmentEngine
        self.broker._fulfillment = FulfillmentEngine(
            self.exchange, symbol, self.broker._fulfillment_config,
        )
        self.broker._fulfillment.resume(pending_order)
        self.broker._order_counter += 1
        self.broker._current_order_id = f"paper_{self.broker._order_counter}"
        self.broker._current_order_symbol = symbol


def load_config(config_path: str) -> dict:
    """Load and validate SwingBot config from YAML.

    Supports both new (bot: + broker:) and legacy (paper_trading:) config formats.

    If strategy.config points to a strategy YAML file, the strategy type, version,
    and params are loaded from that file (same format as backtest configs).
    """
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Accept both old and new format
    if "paper_trading" in config and "bot" not in config:
        config["bot"] = config["paper_trading"]
        if "broker" not in config:
            config["broker"] = {
                "type": "paper",
                "initial_cash": config["paper_trading"].get("initial_cash", 100000),
                "fulfillment": config.get("fulfillment", {}),
            }

    # Validate required sections
    bot_cfg = config.get("bot") or config.get("paper_trading")
    if not bot_cfg:
        raise ValueError("Missing required config section: 'bot' (or 'paper_trading')")

    if "strategy" not in config:
        raise ValueError("Missing required config section: 'strategy'")

    # Resolve strategy config reference
    strat = config["strategy"]
    if "config" in strat:
        strat_config_path = Path(strat["config"])
        if not strat_config_path.is_absolute():
            strat_config_path = Path(config_path).parent / strat_config_path
        if not strat_config_path.exists():
            # Try relative to project root (cwd)
            strat_config_path = Path(strat["config"])
        with open(strat_config_path) as f:
            strat_file = yaml.safe_load(f)
        # Extract strategy info from backtest config format
        strat_entry = strat_file["strategies"][0]
        strat_type = strat_entry["type"]
        config["strategy"] = {
            "type": strat_type,
            "version": strat_file.get("backtest", {}).get("version", ""),
            "display_name": get_display_name(strat_type),
            "params": strat_entry.get("params", {}),
        }

    # Ensure display_name is set even for inline strategy configs
    strat = config["strategy"]
    if "display_name" not in strat:
        strat["display_name"] = get_display_name(strat.get("type", ""))

    for key in ("symbol", "data_dir", "state_file"):
        if key not in bot_cfg:
            raise ValueError(f"Missing bot.{key}")

    return config


def main():
    """Entry point for SwingBot daemon."""
    if len(sys.argv) < 2:
        print("Usage: python -m trading.swing_bot <config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_config(config_path)

    log_cfg = config.get("logging", {})
    setup_logging(
        log_file=log_cfg.get("file", "data/live/swing_bot.log"),
        level=log_cfg.get("level", "INFO"),
        max_days=log_cfg.get("max_days", 30),
    )

    bot = SwingBot(config)
    bot.startup()
    bot.run()


if __name__ == "__main__":
    main()
