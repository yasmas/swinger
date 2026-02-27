"""PaperTrader daemon — single-threaded main loop for simulated live trading."""

import json
import logging
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
        self._last_5m_fetch_minute = -1

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
        self.strategy_runner.startup(self._df_5m, self._df_1h, exchange_price)

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

    def run(self):
        """Main event loop — runs until SIGTERM/SIGINT or error."""
        self.running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info("Entering main loop.")

        try:
            while self.running:
                now = datetime.now(timezone.utc)
                minute = now.minute

                try:
                    self._tick(now, minute)
                except Exception as e:
                    logger.error("Error in main loop tick: %s", e, exc_info=True)

                self._sleep_until_next_minute(now)

        except Exception as e:
            logger.error("Unhandled exception in main loop: %s", e, exc_info=True)
            self._save_state()
            sys.exit(1)
        finally:
            self._shutdown()

    def _tick(self, now: datetime, minute: int):
        """One iteration of the main loop."""
        has_pending = self.fulfillment_engine.pending is not None

        # 1. Data collection on 5-min boundaries (minute % 5 == 1)
        if minute % 5 == 1 and minute != self._last_5m_fetch_minute:
            self._last_5m_fetch_minute = minute
            self._fetch_5m(now)

        # 2. Fulfillment check — every minute when pending
        if has_pending:
            self._check_fulfillment(now)

        # 3. On non-pending + idle minutes, just wait
        # Strategy evaluation happens inside _fetch_5m when an hour boundary is hit

    def _fetch_5m(self, now: datetime):
        """Fetch latest 5m bar; evaluate strategy on every bar, regenerate report on hour boundary."""
        new_bar = self.data_manager.fetch_and_append_5m()
        if new_bar is None:
            return

        # Reload the full dataset (cheap — only 2 months of files)
        self._df_5m = self.data_manager._load_recent("5m")

        is_hour = self.data_manager.is_hour_boundary(new_bar)

        # On hour boundaries, persist the completed 1h bar and refresh the report.
        if is_hour:
            hourly = self.data_manager.resample_latest_hour(self._df_5m)
            if hourly is not None:
                self.data_manager.append_1h(hourly)
                self._df_1h = self.data_manager._load_recent("1h")
            self._regenerate_report()

        # Evaluate strategy on every 5m bar so stop-losses are checked intra-hour.
        # Entry logic is only allowed at the hour boundary (is_hour=True).
        if self.fulfillment_engine.pending is None:
            self._evaluate_strategy(now, is_hour_boundary=is_hour)
        else:
            logger.debug("5m bar received but fulfillment pending — skipping strategy eval.")

    def _evaluate_strategy(self, now: datetime, is_hour_boundary: bool = False):
        """Run strategy on_bar and start fulfillment if a trade signal fires."""
        action = self.strategy_runner.on_5m_bar(self._df_5m, is_hour_boundary=is_hour_boundary)

        if action.action == ActionType.HOLD:
            return

        action_str = action.action.value
        quantity = action.quantity
        logger.info("Strategy signal: %s %.8f %s", action_str, quantity, self.symbol)

        # Start fulfillment
        self.fulfillment_engine.start(action_str, quantity)
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
        """Execute a trade on the portfolio and log it."""
        portfolio = self.strategy_runner.portfolio

        if action_type == "BUY":
            portfolio.buy(self.symbol, quantity, price)
        elif action_type == "SELL":
            portfolio.sell(self.symbol, quantity, price)
        elif action_type == "SHORT":
            portfolio.short_sell(self.symbol, quantity, price)
        elif action_type == "COVER":
            portfolio.cover(self.symbol, quantity, price)

        self._log_trade(
            date=now.isoformat(),
            action=action_type,
            quantity=quantity,
            price=price,
            details=fulfillment_details,
        )

    def _save_state(self):
        """Persist current state (pending order)."""
        pending = self.fulfillment_engine.get_pending_for_state()
        self.state_manager.save(pending_order=pending)

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

    def _sleep_until_next_minute(self, now: datetime):
        """Sleep until the next minute boundary, adapting to fulfillment state."""
        has_pending = self.fulfillment_engine.pending is not None

        if has_pending:
            # During fulfillment: wake every minute
            next_wake = now.replace(second=0, microsecond=0)
            from datetime import timedelta
            next_wake += timedelta(minutes=1)
        else:
            # Idle: wake on next 5-min boundary (minute % 5 == 1)
            from datetime import timedelta
            next_min = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
            while next_min.minute % 5 != 1:
                next_min += timedelta(minutes=1)
            next_wake = next_min

        sleep_seconds = max(0, (next_wake - datetime.now(timezone.utc)).total_seconds())

        if sleep_seconds > 0:
            logger.debug(
                "Sleeping %.0fs until %s (pending=%s)", sleep_seconds, next_wake.strftime("%H:%M"), has_pending,
            )
            # Sleep in 1s increments so signal handling is responsive
            end_time = time.time() + sleep_seconds
            while self.running and time.time() < end_time:
                time.sleep(min(1.0, end_time - time.time()))

    def _handle_signal(self, signum, frame):
        """Handle SIGTERM/SIGINT for clean shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info("Received %s — shutting down gracefully.", sig_name)
        self.running = False

    def _shutdown(self):
        """Clean shutdown: save state, close files, log summary."""
        logger.info("Shutting down...")
        self._save_state()

        if hasattr(self, "_trade_log_file") and self._trade_log_file:
            self._trade_log_file.close()

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
        level=log_cfg.get("level", "DEBUG"),
        max_days=log_cfg.get("max_days", 30),
    )

    trader = PaperTrader(config)
    trader.startup()
    trader.run()


if __name__ == "__main__":
    main()
