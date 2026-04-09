"""SwingPartyBot — multi-asset paper trading bot for the SwingParty strategy."""

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

import pandas as pd
import yaml

from exchange.registry import create_exchange
from portfolio import Portfolio
from execution.backtest_executor import BacktestExecutor
from strategies.base import Action, ActionType
from strategies.swing_party import SwingPartyCoordinator
from strategies.registry import get_display_name
from trade_log import TRADE_LOG_COLUMNS
from trading.data_manager import DataManager, FIVE_MIN_MS
from trading.logging_config import setup_logging
from trading.state_manager import StateManager
from trading.trader_base import TraderBase

logger = logging.getLogger(__name__)


def _position_snapshot(portfolio: Portfolio, symbol: str) -> dict:
    pos = portfolio.positions.get(symbol)
    short = portfolio.short_positions.get(symbol)
    return {
        "position_qty": pos.quantity if pos else 0.0,
        "position_avg_cost": pos.avg_cost if pos else 0.0,
        "short_qty": short.quantity if short else 0.0,
        "short_avg_cost": short.avg_cost if short else 0.0,
    }


class SwingPartyBot(TraderBase):
    """Multi-asset paper trading bot using SwingPartyCoordinator.

    Manages one DataManager per asset, fetches 5m bars from the exchange,
    resamples to the configured interval, and calls the coordinator to
    decide entries/exits/rotations across all assets.
    """

    def __init__(self, config: dict):
        super().__init__(config)

        bot_cfg = config.get("bot") or config.get("paper_trading", {})
        self.data_dir = bot_cfg["data_dir"]
        self.state_file = bot_cfg["state_file"]
        self.warm_up_hours = bot_cfg.get("warm_up_hours", 250)

        rpt = config.get("reporting", {})
        self.trade_log_path = rpt.get("trade_log", f"{self.data_dir}/trades.csv")

        self.assets = config["strategy"]["params"].get("assets", [])
        if not self.assets:
            raise ValueError("No assets configured in strategy.params.assets")

        fetch_cfg = bot_cfg.get("fetch", {})
        self._fetch_delay_seconds = fetch_cfg.get("delay_seconds", 5)
        self._fetch_poll_interval = fetch_cfg.get("poll_interval_seconds", 3.0)
        self._fetch_timeout = fetch_cfg.get("timeout_seconds", 30)

        self._data_managers: dict[str, DataManager] = {}
        self._dfs_5m: dict[str, pd.DataFrame] = {}
        self._dfs_1h: dict[str, pd.DataFrame] = {}
        self.coordinator: SwingPartyCoordinator | None = None
        self.portfolio: Portfolio | None = None
        self.executor: BacktestExecutor | None = None
        self.state_manager: StateManager | None = None
        self.exchange = None

        self._trade_log_file = None
        self._trade_log_writer = None
        self._last_bar_hour: dict[str, int] = {}

    def _startup_hook(self):
        broker_cfg = self.config.get("broker", {"type": "paper"})
        broker_type = broker_cfg.get("type", "paper")
        if broker_type != "paper":
            raise NotImplementedError(
                f"SwingPartyBot only supports paper trading (got broker.type={broker_type})"
            )

        logger.info("=" * 60)
        logger.info("SwingPartyBot starting up")
        logger.info("  Assets: %s", ", ".join(self.assets))
        logger.info("  Strategy: %s %s", self.strategy_type, self.strategy_version)
        logger.info("  Data dir: %s", self.data_dir)
        logger.info("=" * 60)

        ex_cfg = self.config.get("exchange", {})
        self.exchange = create_exchange(ex_cfg)

        # Per-asset data managers
        for sym in self.assets:
            sym_data_dir = os.path.join(self.data_dir, sym.lower())
            dm = DataManager(self.exchange, sym, sym_data_dir,
                             warm_up_hours=self.warm_up_hours)
            df_5m, df_1h = dm.startup()
            self._data_managers[sym] = dm
            self._dfs_5m[sym] = df_5m
            self._dfs_1h[sym] = df_1h
            logger.info("  %s: %d 5m bars, %d 1h bars", sym, len(df_5m), len(df_1h))

        # Coordinator
        strat_params = self.config["strategy"]["params"]
        self.coordinator = SwingPartyCoordinator(strat_params)
        self.coordinator.prepare(self._dfs_5m)

        # Portfolio + executor
        self.state_manager = StateManager(self.state_file)
        state = self.state_manager.load()

        self.portfolio = Portfolio(self.initial_cash)
        self.executor = BacktestExecutor()

        # Restore state if available
        if state.get("strategy_state"):
            coord_state = state["strategy_state"]
            if "slots" in coord_state:
                self.coordinator.slots = coord_state["slots"]
            if "strategies" in coord_state:
                for sym, sstate in coord_state["strategies"].items():
                    if sym in self.coordinator.strategies:
                        self.coordinator.strategies[sym].import_state(sstate)
            logger.info("Coordinator state restored.")
        if state.get("broker_state"):
            bs = state["broker_state"]
            self.portfolio = Portfolio(bs.get("cash", self.initial_cash))
            for sym, pdata in bs.get("positions", {}).items():
                if pdata.get("quantity", 0) > 0:
                    self.portfolio.buy(sym, pdata["quantity"], pdata.get("avg_cost", 0))
            for sym, sdata in bs.get("short_positions", {}).items():
                if sdata.get("quantity", 0) > 0:
                    self.portfolio.short(sym, sdata["quantity"], sdata.get("avg_cost", 0))
            logger.info("Portfolio state restored: cash=$%.2f", self.portfolio.cash)

        self._init_trade_logger()

        # Seed hour tracking from existing data so the first boundary is detected
        for sym in self.assets:
            df = self._dfs_5m.get(sym)
            if df is not None and not df.empty:
                last_dt = df.index[-1]
                last_ts = int(pd.Timestamp(last_dt).timestamp() * 1000)
                self._last_bar_hour[sym] = last_ts // 3_600_000

        pv = self.portfolio.total_value(self._latest_prices())
        logger.info("Startup complete. Portfolio value: $%.2f", pv)

    def run(self):
        self._acquire_lock()
        super().run()

    def _tick(self):
        now = datetime.now(timezone.utc)
        self._try_fetch_all(now)

    def _sleep_until_next_event(self):
        now = datetime.now(timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        next_bar_close_ms = ((now_ms // FIVE_MIN_MS) + 1) * FIVE_MIN_MS
        next_wake = datetime.fromtimestamp(
            next_bar_close_ms / 1000 + self._fetch_delay_seconds,
            tz=timezone.utc,
        )
        sleep_seconds = max(0, (next_wake - datetime.now(timezone.utc)).total_seconds())
        if sleep_seconds > 0:
            logger.debug("Sleeping %.0fs until %s", sleep_seconds, next_wake.strftime("%H:%M:%S"))
            self._sleep_with_zmq_poll(sleep_seconds)

    def _get_portfolio_state(self) -> dict:
        prices = self._latest_prices()
        pv = self.portfolio.total_value(prices) if self.portfolio else self.initial_cash

        # Aggregate position display across assets
        position = "FLAT"
        total_long_qty = sum(
            p.quantity for p in self.portfolio.positions.values()
        ) if self.portfolio else 0
        total_short_qty = sum(
            p.quantity for p in self.portfolio.short_positions.values()
        ) if self.portfolio else 0
        if total_long_qty > 0:
            position = "LONG"
        elif total_short_qty > 0:
            position = "SHORT"

        return {
            "portfolio_value": pv,
            "cash": self.portfolio.cash if self.portfolio else self.initial_cash,
            "position": position,
            "position_qty": total_long_qty + total_short_qty,
            "position_avg_cost": 0.0,
            "last_price": 0.0,
        }

    def _force_close(self):
        if not self.coordinator or not self.portfolio:
            return
        prices = self._latest_prices()
        for sym in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[sym]
            if pos.quantity > 0:
                price = prices.get(sym, 0)
                if price > 0:
                    action = Action(ActionType.SELL, pos.quantity, {"reason": "force_close"})
                    self.executor.execute(action, sym, price, self.portfolio)
                    self._log_trade(sym, "SELL", pos.quantity, price, {"reason": "force_close"})
        for sym in list(self.portfolio.short_positions.keys()):
            pos = self.portfolio.short_positions[sym]
            if pos.quantity > 0:
                price = prices.get(sym, 0)
                if price > 0:
                    action = Action(ActionType.COVER, pos.quantity, {"reason": "force_close"})
                    self.executor.execute(action, sym, price, self.portfolio)
                    self._log_trade(sym, "COVER", pos.quantity, price, {"reason": "force_close"})
        self._save_state()

    def _shutdown_hook(self):
        self._save_state()
        if self._trade_log_file:
            self._trade_log_file.close()
        self._release_lock()
        pv = self.portfolio.total_value(self._latest_prices()) if self.portfolio else 0
        logger.info("Final portfolio value: $%.2f", pv)

    def _try_fetch_all(self, now: datetime):
        """Fetch new 5m bars for all assets, then evaluate coordinator on hourly boundaries."""
        any_new = False
        any_hourly = False

        for sym in self.assets:
            dm = self._data_managers[sym]
            new_bar = dm.fetch_and_append_5m()

            if new_bar is None:
                now_ms = int(now.timestamp() * 1000)
                current_bucket_start = (now_ms // FIVE_MIN_MS) * FIVE_MIN_MS
                ms_since_close = now_ms - current_bucket_start
                if ms_since_close < self._fetch_timeout * 1000:
                    deadline = time.time() + self._fetch_timeout - ms_since_close / 1000
                    while new_bar is None and self.running and time.time() < deadline:
                        time.sleep(self._fetch_poll_interval)
                        new_bar = dm.fetch_and_append_5m()

            if new_bar is not None:
                any_new = True
                if dm.has_gap:
                    df_5m, df_1h = dm.fill_gap()
                    self._dfs_5m[sym] = df_5m
                    self._dfs_1h[sym] = df_1h
                else:
                    self._dfs_5m[sym] = dm._load_recent("5m")

                bar_ts = int(new_bar.iloc[0]["open_time"])
                bar_hour = bar_ts // 3_600_000
                prev_hour = self._last_bar_hour.get(sym)
                self._last_bar_hour[sym] = bar_hour

                if prev_hour is not None and bar_hour != prev_hour:
                    hourly = dm.resample_latest_hour(self._dfs_5m[sym])
                    if hourly is not None:
                        dm.append_1h(hourly)
                        self._dfs_1h[sym] = dm._load_recent("1h")
                    any_hourly = True

        if not any_new:
            return

        # Re-prepare coordinator with updated data
        self.coordinator.prepare(self._dfs_5m)

        if any_hourly and not self.paused:
            self._evaluate_coordinator(now)

        self._save_state()

    def _evaluate_coordinator(self, now: datetime):
        """Call coordinator.on_bar with the latest hourly data and execute actions."""
        date = pd.Timestamp(now, tz=None)

        rows = {}
        datasets_so_far = {}
        for sym in self.assets:
            df = self._dfs_5m[sym]
            if not df.empty:
                rows[sym] = df.iloc[-1]
                datasets_so_far[sym] = df

        if not rows:
            return

        logger.info(
            "Evaluating coordinator: %d assets, date=%s",
            len(rows), date.strftime("%Y-%m-%d %H:%M"),
        )

        is_last_bar = False
        actions = self.coordinator.on_bar(date, rows, datasets_so_far, is_last_bar, self.portfolio)

        for symbol, action in actions:
            if action.action == ActionType.HOLD:
                continue

            price = float(rows[symbol]["close"]) if symbol in rows else 0.0
            if price <= 0:
                logger.warning("No price for %s, skipping %s", symbol, action.action.value)
                continue

            try:
                self.executor.execute(action, symbol, price, self.portfolio)
            except ValueError as e:
                logger.error("Execution failed for %s %s: %s", symbol, action.action.value, e)
                self.set_execution_error(f"{symbol} {action.action.value}: {e}")
                continue

            self._log_trade(symbol, action.action.value, action.quantity, price, action.details)

            is_entry = action.action in (ActionType.BUY, ActionType.SHORT)
            event_type = "trade_entry" if is_entry else "trade_exit"
            self._send_trade_event(event_type, action.action.value, price, action.quantity, action.details)

        self.set_execution_error(None)

    def _latest_prices(self) -> dict[str, float]:
        prices = {}
        for sym in self.assets:
            df = self._dfs_5m.get(sym)
            if df is not None and not df.empty:
                prices[sym] = float(df.iloc[-1]["close"])
        return prices

    def _init_trade_logger(self):
        path = Path(self.trade_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        self._trade_log_file = open(path, "a", newline="")
        self._trade_log_writer = csv.writer(self._trade_log_file, quoting=csv.QUOTE_MINIMAL)
        if write_header:
            self._trade_log_writer.writerow(TRADE_LOG_COLUMNS)
            self._trade_log_file.flush()

    def _log_trade(self, symbol: str, action: str, quantity: float, price: float,
                   details: dict | None = None):
        prices = self._latest_prices()
        pv = self.portfolio.total_value(prices)
        snap = _position_snapshot(self.portfolio, symbol)
        details_str = json.dumps(details) if details else "{}"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        self._trade_log_writer.writerow([
            now_str, action, symbol,
            f"{quantity:.8f}", f"{price:.2f}",
            f"{self.portfolio.cash:.2f}", f"{pv:.2f}",
            f"{snap['position_qty']:.8f}", f"{snap['position_avg_cost']:.2f}",
            f"{snap['short_qty']:.8f}", f"{snap['short_avg_cost']:.2f}",
            details_str,
        ])
        self._trade_log_file.flush()
        logger.info(
            "Trade: %s %.4f %s @ $%.2f (cash=$%.2f, PV=$%.2f)",
            action, quantity, symbol, price, self.portfolio.cash, pv,
        )

    def _save_state(self):
        if not self.state_manager or not self.coordinator or not self.portfolio:
            return
        coord_state = {
            "slots": {k: dict(v) for k, v in self.coordinator.slots.items()},
            "strategies": {
                sym: strat.export_state()
                for sym, strat in self.coordinator.strategies.items()
            },
        }
        portfolio_state = {
            "cash": self.portfolio.cash,
            "positions": {
                sym: {"quantity": p.quantity, "avg_cost": p.avg_cost}
                for sym, p in self.portfolio.positions.items()
                if p.quantity > 0
            },
            "short_positions": {
                sym: {"quantity": p.quantity, "avg_cost": p.avg_cost}
                for sym, p in self.portfolio.short_positions.items()
                if p.quantity > 0
            },
        }
        self.state_manager.save(
            strategy_state=coord_state,
            broker_state=portfolio_state,
        )

    def _acquire_lock(self):
        lock_path = Path(self.data_dir) / "swing_party_bot.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = open(lock_path, "w")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            logger.error("Another SwingPartyBot instance is already running (lock: %s)", lock_path)
            sys.exit(1)
        self._lock_file.write(str(os.getpid()))
        self._lock_file.flush()
        atexit.register(self._release_lock)

    def _release_lock(self):
        if hasattr(self, "_lock_file") and self._lock_file and not self._lock_file.closed:
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
            self._lock_file.close()


def load_config(config_path: str) -> dict:
    """Load and resolve the SwingPartyBot config (bot YAML + strategy YAML)."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    if "strategy" not in config:
        raise ValueError("Missing required config section: 'strategy'")

    strat = config["strategy"]
    if "config" in strat:
        strat_config_path = Path(strat["config"])
        if not strat_config_path.is_absolute():
            strat_config_path = Path(config_path).parent / strat_config_path
        if not strat_config_path.exists():
            strat_config_path = Path(strat["config"])
        with open(strat_config_path) as f:
            strat_file = yaml.safe_load(f)

        if "strategies" in strat_file:
            strat_entry = strat_file["strategies"][0]
            strat_type = strat_entry["type"]
            strat_params = strat_entry.get("params", {})
        elif "strategy" in strat_file:
            strat_entry = strat_file["strategy"]
            strat_type = strat_entry["type"]
            strat_params = {k: v for k, v in strat_entry.items() if k != "type"}
        else:
            raise ValueError(f"Strategy file {strat_config_path} has neither 'strategies' nor 'strategy' key")

        config["strategy"] = {
            "type": strat_type,
            "version": strat_file.get("backtest", {}).get("version", ""),
            "display_name": get_display_name(strat_type),
            "params": strat_params,
        }

    bot_cfg = config.get("bot") or config.get("paper_trading", {})
    for key in ("data_dir", "state_file"):
        if key not in bot_cfg:
            raise ValueError(f"Missing bot.{key}")

    return config


def _load_user_env(user: str | None) -> None:
    """Load .env and credential files for a dashboard user."""
    import json as _json
    project_root = Path(__file__).resolve().parent.parent.parent
    user_dir = project_root / "data" / user if user else None

    dotenv_path = None
    if user_dir and (user_dir / ".env").exists():
        dotenv_path = user_dir / ".env"
    elif (project_root / ".env").exists():
        dotenv_path = project_root / ".env"

    try:
        from dotenv import load_dotenv
        if dotenv_path:
            loaded = load_dotenv(dotenv_path)
            print(f"[startup] Loaded .env from {dotenv_path} (loaded={loaded})")
    except ImportError:
        pass

    key_file = os.environ.get("COINBASE_KEY_FILE")
    if key_file:
        key_path = Path(key_file) if Path(key_file).is_absolute() else project_root / key_file
        if key_path.exists():
            data = _json.loads(key_path.read_text())
            os.environ["COINBASE_ADV_API_KEY"] = data.get("name", "")
            os.environ["COINBASE_ADV_API_SECRET"] = data.get("privateKey", "")
            print(f"[startup] Loaded Coinbase API key from {key_path}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m trading.swing_party_bot <config.yaml>")
        sys.exit(1)

    config_path = sys.argv[1]
    user = sys.argv[2] if len(sys.argv) > 2 else None

    _load_user_env(user)
    config = load_config(config_path)

    if user and config.get("trader_name"):
        config["trader_name"] = f"{user}:{config['trader_name']}"

    log_cfg = config.get("logging", {})
    setup_logging(
        log_file=log_cfg.get("file", "data/live/swing_party_bot.log"),
        level=log_cfg.get("level", "INFO"),
        max_days=log_cfg.get("max_days", 30),
    )

    bot = SwingPartyBot(config)
    bot.startup()
    bot.run()


if __name__ == "__main__":
    main()
