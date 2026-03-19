"""PaperTrader daemon — single-threaded main loop for simulated live trading."""

import atexit
import fcntl
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from exchange.binance_rest import BinanceRestClient
from paper_trading.data_manager import DataManager
from paper_trading.fulfillment import FulfillmentEngine, FulfillmentResult
from paper_trading.logging_config import setup_logging
from paper_trading.state_manager import StateManager
from paper_trading.strategy_runner import StrategyRunner
from portfolio import Portfolio
from reporting.reporter import Reporter
from strategies.base import ActionType
from trade_log import TradeLogger, TRADE_LOG_COLUMNS

logger = logging.getLogger(__name__)


class PaperTrader:
    """Orchestrates paper trading: data collection, strategy, fulfillment, reporting."""

    def __init__(self, config: dict):
        self.config = config
        self.running = False

        pt = config["paper_trading"]
        self.symbol = pt["symbol"]
        self.initial_cash = pt["initial_cash"]
        self.data_dir = pt["data_dir"]
        self.state_file = pt["state_file"]
        self.warm_up_hours = pt.get("warm_up_hours", 250)

        strat = config["strategy"]
        self.strategy_type = strat["type"]
        self.strategy_params = strat.get("params", {})
        self.strategy_version = strat.get("version", "")

        rpt = config.get("reporting", {})
        self.trade_log_path = rpt.get("trade_log", f"{self.data_dir}/trades.csv")
        self.report_output_dir = rpt.get("output_dir", "reports/live")
        self.report_file = rpt.get("report_file", "reports/live/report.html")
        self.cost_per_trade_pct = rpt.get("cost_per_trade_pct", 0.05)

        self.exchange = None
        self.data_manager = None
        self.state_manager = None
        self.strategy_runner = None
        self.fulfillment_engine = None
        self.trade_logger = None
        self.reporter = None

        self._df_5m = None
        self._df_1h = None

        fetch_cfg = pt.get("fetch", {})
        self._fetch_delay_seconds = fetch_cfg.get("delay_seconds", 3)
        self._fetch_poll_interval = fetch_cfg.get("poll_interval_seconds", 3.0)
        self._fetch_timeout = fetch_cfg.get("timeout_seconds", 30)

    def startup(self):
        """Initialize all components in the correct order."""
        logger.info("=" * 60)
        logger.info("PaperTrader starting up")
        logger.info("  Symbol: %s", self.symbol)
        logger.info("  Strategy: %s %s", self.strategy_type, self.strategy_version)
        logger.info("  Initial cash: $%.2f", self.initial_cash)
        logger.info("  Data dir: %s", self.data_dir)
        logger.info("=" * 60)

        # 1. Exchange client
        ex_cfg = self.config.get("exchange", {})
        self.exchange = BinanceRestClient(ex_cfg)
        logger.info("Exchange client initialized: %s", self.exchange.base_url)

        # 2. Data manager — backfill + load
        self.data_manager = DataManager(
            self.exchange, self.symbol, self.data_dir,
            warm_up_hours=self.warm_up_hours,
        )
        self._df_5m, self._df_1h = self.data_manager.startup()

        # 3. State manager
        self.state_manager = StateManager(self.state_file)
        state = self.state_manager.load()

        # 4. Strategy runner
        exchange_price = None
        try:
            exchange_price = self.exchange.get_current_price(self.symbol)
        except Exception as e:
            logger.warning("Could not fetch exchange price for sanity check: %s", e)

        self.strategy_runner = StrategyRunner(
            strategy_type=self.strategy_type,
            strategy_params=self.strategy_params,
            initial_cash=self.initial_cash,
            symbol=self.symbol,
            trade_log_path=self.trade_log_path,
        )
        self.strategy_runner.startup(
            self._df_5m, self._df_1h, exchange_price,
            strategy_state=state.get("strategy_state"),
        )

        # 5. Fulfillment engine — resume pending if any
        ful_cfg = self.config.get("fulfillment", {})
        self.fulfillment_engine = FulfillmentEngine(
            self.exchange, self.symbol, ful_cfg,
        )
        if state.get("pending_order"):
            self.fulfillment_engine.resume(state["pending_order"])
            logger.info("Resumed pending fulfillment: %s", state["pending_order"]["action"])

        # 6. Trade logger (append mode if file exists)
        self._init_trade_logger()

        # 7. Reporter
        self.reporter = Reporter(output_dir=self.report_output_dir)

        portfolio_value = self._portfolio_value()
        logger.info(
            "Startup complete. Portfolio: $%.2f, Pending: %s",
            portfolio_value,
            self.fulfillment_engine.pending["action"] if self.fulfillment_engine.pending else "none",
        )

    def _init_trade_logger(self):
        """Open the trade log CSV in append mode, creating with header if needed."""
        path = Path(self.trade_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0

        import csv
        self._trade_log_file = open(path, "a", newline="")
        self._trade_log_writer = csv.writer(self._trade_log_file, quoting=csv.QUOTE_MINIMAL)
        if write_header:
            self._trade_log_writer.writerow(TRADE_LOG_COLUMNS)
            self._trade_log_file.flush()

    def _log_trade(self, date: str, action: str, quantity: float, price: float,
                   details: dict | None = None):
        """Append a row to the trade log CSV."""
        portfolio_value = self._portfolio_value()
        details_str = json.dumps(details) if details else "{}"
        self._trade_log_writer.writerow([
            date, action, self.symbol,
            f"{quantity:.8f}", f"{price:.2f}",
            f"{self.strategy_runner.portfolio.cash:.2f}",
            f"{portfolio_value:.2f}",
            details_str,
        ])
        self._trade_log_file.flush()
        logger.info(
            "Trade logged: %s %.8f %s @ $%.2f (cash=$%.2f, value=$%.2f)",
            action, quantity, self.symbol, price,
            self.strategy_runner.portfolio.cash, portfolio_value,
        )

    def _portfolio_value(self) -> float:
        """Get current portfolio value using latest local price."""
        if self._df_5m is not None and not self._df_5m.empty:
            price = float(self._df_5m.iloc[-1]["close"])
        else:
            try:
                price = self.exchange.get_current_price(self.symbol)
            except Exception:
                price = 0.0
        return self.strategy_runner.portfolio.total_value({self.symbol: price})

    def _acquire_lock(self):
        """Acquire a file lock to prevent multiple instances."""
        lock_path = Path(self.config["paper_trading"]["data_dir"]) / "paper_trader.lock"
        self._lock_file = open(lock_path, "w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            logger.error("Another paper_trader instance is already running (lock: %s)", lock_path)
            sys.exit(1)
        self._lock_file.write(str(os.getpid()))
        self._lock_file.flush()
        atexit.register(self._release_lock)

    def _release_lock(self):
        """Release the file lock."""
        if hasattr(self, "_lock_file") and self._lock_file and not self._lock_file.closed:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()

    def run(self):
        """Main event loop — runs until SIGTERM/SIGINT or error."""
        self._acquire_lock()
        self.running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info("Entering main loop.")

        try:
            while self.running:
                try:
                    self._tick()
                except Exception as e:
                    logger.error("Error in main loop tick: %s", e, exc_info=True)

                self._sleep_until_next_event()

        except Exception as e:
            logger.error("Unhandled exception in main loop: %s", e, exc_info=True)
            self._save_state()
            sys.exit(1)
        finally:
            self._shutdown()

    def _tick(self):
        """One iteration of the main loop."""
        now = datetime.now(timezone.utc)

        if self.fulfillment_engine.pending is not None:
            self._check_fulfillment(now)

        self._try_fetch_5m(now)

    def _try_fetch_5m(self, now: datetime):
        """Attempt to fetch the latest closed 5m bar. Tight-polls if close was recent."""
        from paper_trading.data_manager import FIVE_MIN_MS

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

        if self.fulfillment_engine.pending is None:
            self._evaluate_strategy(now)
        else:
            logger.debug("5m bar received but fulfillment pending — skipping strategy eval.")

    def _evaluate_strategy(self, now: datetime):
        """Run strategy on_bar and start fulfillment if a trade signal fires."""
        action = self.strategy_runner.on_5m_bar(self._df_5m)

        if action.action != ActionType.HOLD:
            action_str = action.action.value
            quantity = action.quantity
            logger.info("Strategy signal: %s %.8f %s", action_str, quantity, self.symbol)
            self.fulfillment_engine.start(action_str, quantity)

        # Save state on every bar for correct crash recovery
        self._save_state()

    def _check_fulfillment(self, now: datetime):
        """Poll fulfillment engine and execute if filled."""
        result, details = self.fulfillment_engine.check()

        if result == FulfillmentResult.WAITING:
            return

        if result in (FulfillmentResult.FILLED, FulfillmentResult.ABORTED_MARKET):
            self._execute_trade(
                details["action"], details["quantity"],
                details["fill_price"], details, now,
            )
        elif result == FulfillmentResult.ABORTED_CANCEL:
            logger.info("Fulfillment cancelled — no trade executed.")

        self._save_state()
        self._regenerate_report()

    def _execute_trade(self, action_type: str, quantity: float, price: float,
                       fulfillment_details: dict, now: datetime):
        """Execute a trade on the portfolio and log it.

        If the portfolio operation fails (e.g., position already closed due to
        state desync), we still log the trade with an error flag so we have a
        record of what the fulfillment engine attempted.
        """
        portfolio = self.strategy_runner.portfolio
        error_msg = None

        try:
            if action_type == "BUY":
                portfolio.buy(self.symbol, quantity, price)
            elif action_type == "SELL":
                portfolio.sell(self.symbol, quantity, price)
            elif action_type == "SHORT":
                portfolio.short_sell(self.symbol, quantity, price)
            elif action_type == "COVER":
                portfolio.cover(self.symbol, quantity, price)
        except ValueError as e:
            # Position doesn't exist or insufficient quantity — state desync.
            # Log the error but still record the trade attempt.
            error_msg = str(e)
            logger.error("Portfolio operation failed: %s (logging trade anyway)", e)

        details = fulfillment_details.copy()
        if error_msg:
            details["portfolio_error"] = error_msg

        self._log_trade(
            date=now.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            action=action_type,
            quantity=quantity,
            price=price,
            details=details,
        )

    def _save_state(self):
        """Persist current state (pending order + strategy state)."""
        pending = self.fulfillment_engine.get_pending_for_state()
        strategy_state = self.strategy_runner.get_strategy_state()
        self.state_manager.save(
            pending_order=pending,
            strategy_state=strategy_state,
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

    def _sleep_until_next_event(self):
        """Sleep until the next 5m bar close (+ small delay), or 1 minute if fulfillment pending."""
        from datetime import timedelta
        from paper_trading.data_manager import FIVE_MIN_MS

        now = datetime.now(timezone.utc)
        has_pending = self.fulfillment_engine.pending is not None

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
            end_time = time.time() + sleep_seconds
            while self.running and time.time() < end_time:
                time.sleep(min(30.0, end_time - time.time()))

    def _handle_signal(self, signum, frame):
        """Handle SIGTERM/SIGINT for clean shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — shutting down gracefully.", sig_name)
        self.running = False

    def _shutdown(self):
        """Clean shutdown: save state, close files, release lock, log summary."""
        logger.info("Shutting down...")
        self._save_state()

        if hasattr(self, "_trade_log_file") and self._trade_log_file:
            self._trade_log_file.close()

        self._release_lock()

        portfolio_value = self._portfolio_value()
        logger.info(
            "Shutdown complete. Final portfolio value: $%.2f", portfolio_value,
        )
        logger.info("=" * 60)


def load_config(config_path: str) -> dict:
    """Load and validate paper trading config from YAML."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    required = ["paper_trading", "strategy"]
    for key in required:
        if key not in config:
            raise ValueError(f"Missing required config section: '{key}'")

    pt = config["paper_trading"]
    for key in ("symbol", "initial_cash", "data_dir", "state_file"):
        if key not in pt:
            raise ValueError(f"Missing paper_trading.{key}")

    return config


def main():
    """Entry point for paper_trader daemon."""
    if len(sys.argv) < 2:
        print("Usage: python -m paper_trading.paper_trader <config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    config = load_config(config_path)

    log_cfg = config.get("logging", {})
    setup_logging(
        log_file=log_cfg.get("file", "data/live/paper_trader.log"),
        level=log_cfg.get("level", "INFO"),
        max_days=log_cfg.get("max_days", 30),
    )

    trader = PaperTrader(config)
    trader.startup()
    trader.run()


if __name__ == "__main__":
    main()
