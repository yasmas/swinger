"""Multi-asset backtest controller for SwingParty.

Loads N DataFrames (one per asset), iterates in lockstep across a union
timestamp index, feeds the SwingPartyCoordinator, and executes returned
actions on a shared portfolio.
"""

import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from portfolio import Portfolio
from execution.backtest_executor import BacktestExecutor
from trade_log import TradeLogger
from strategies.base import Action, ActionType
from strategies.swing_party import SwingPartyCoordinator
from data_sources.registry import DATA_SOURCE_REGISTRY, PARSER_REGISTRY

logger = logging.getLogger(__name__)


def load_multi_asset_datasets(config: dict) -> dict[str, pd.DataFrame]:
    """Load one OHLCV DataFrame per asset from config (backtest + data_source + strategy)."""
    backtest = config["backtest"]
    data_config = config["data_source"]
    strategy_config = config["strategy"]

    parser_cls = PARSER_REGISTRY[data_config["parser"]]
    source_cls = DATA_SOURCE_REGISTRY[data_config["type"]]
    parser = parser_cls()

    params = data_config.get("params", {})
    data_dir = params.get("data_dir", "data")
    file_pattern = params.get(
        "file_pattern", "{symbol}-5m-{start_year}-{end_year}-combined.csv"
    )

    start_year = str(backtest["start_date"])[:4]
    end_year = str(backtest["end_date"])[:4]

    assets = strategy_config.get("assets", [])
    datasets: dict[str, pd.DataFrame] = {}

    for symbol in assets:
        filename = file_pattern.format(
            symbol=symbol, start_year=start_year, end_year=end_year
        )
        file_path = str(Path(data_dir) / filename)

        source_params = {**params, "file_path": file_path, "symbol": symbol}
        source = source_cls(parser, source_params)

        start_date = str(backtest["start_date"])
        end_date = str(backtest["end_date"])
        data = source.get_data(symbol, start_date, end_date)

        if data.empty:
            logger.warning("No data for %s at %s, skipping", symbol, file_path)
            continue

        datasets[symbol] = data
        logger.info(
            "Loaded %s: %d bars (%s to %s)",
            symbol,
            len(data),
            data.index[0],
            data.index[-1],
        )

    return datasets


class MultiAssetBacktestResult:
    """Summary of a multi-asset backtest run."""

    def __init__(
        self,
        strategy_name: str,
        assets: list[str],
        start_date: str,
        end_date: str,
        initial_cash: float,
        final_value: float,
        trade_log_path: str,
        eviction_stats: dict = None,
        execution_errors: list[str] | None = None,
    ):
        self.strategy_name = strategy_name
        self.assets = assets
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.final_value = final_value
        self.trade_log_path = trade_log_path
        self.eviction_stats = eviction_stats or {}
        self.execution_errors = execution_errors or []

    @property
    def total_return_pct(self) -> float:
        return (self.final_value / self.initial_cash - 1) * 100


def _position_snapshot(portfolio: Portfolio, symbol: str) -> dict:
    """Extract position state from a Portfolio for a given symbol."""
    pos = portfolio.positions.get(symbol)
    short = portfolio.short_positions.get(symbol)
    return {
        "position_qty": pos.quantity if pos else 0.0,
        "position_avg_cost": pos.avg_cost if pos else 0.0,
        "short_qty": short.quantity if short else 0.0,
        "short_avg_cost": short.avg_cost if short else 0.0,
    }


_REVERSE_ACTION = {
    ActionType.BUY: ActionType.SELL,
    ActionType.SELL: ActionType.BUY,
    ActionType.SHORT: ActionType.COVER,
    ActionType.COVER: ActionType.SHORT,
}


def _reverse_execute(
    executor: BacktestExecutor,
    symbol: str,
    action: Action,
    price: float,
    portfolio: Portfolio,
) -> None:
    """Undo a trade already applied to the portfolio (same qty & price, opposite direction)."""
    rev = Action(_REVERSE_ACTION[action.action], action.quantity, {"reason": "rollback"})
    executor.execute(rev, symbol, price, portfolio)


class MultiAssetController:
    """Orchestrates a multi-asset backtest with SwingPartyCoordinator."""

    def __init__(
        self,
        config: dict,
        output_dir: str = "reports",
        on_execution_error: Callable[[str | None], None] | None = None,
    ):
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.backtest = config["backtest"]
        self.data_config = config["data_source"]
        self.strategy_config = config["strategy"]
        self._on_execution_error = on_execution_error

    @staticmethod
    def _snapshot(coordinator: SwingPartyCoordinator):
        """Capture coordinator slots + every strategy's internal state."""
        return (
            {k: dict(v) for k, v in coordinator.slots.items()},
            {sym: strat.export_state() for sym, strat in coordinator.strategies.items()},
        )

    @staticmethod
    def _restore(coordinator: SwingPartyCoordinator, snapshot):
        """Roll coordinator back to a previous snapshot."""
        slots_snap, strats_snap = snapshot
        coordinator.slots = slots_snap
        for sym, state in strats_snap.items():
            coordinator.strategies[sym].import_state(state)

    def run(self) -> MultiAssetBacktestResult:
        """Run the multi-asset backtest."""
        datasets = load_multi_asset_datasets(self.config)
        if not datasets:
            raise ValueError("No data loaded for any asset")

        assets = list(datasets.keys())
        print(f"  Assets loaded: {', '.join(assets)} ({len(assets)} total)")

        # Create coordinator
        coordinator = SwingPartyCoordinator(self.strategy_config)
        coordinator.prepare(datasets)

        # Portfolio
        initial_cash = float(self.backtest["initial_cash"])
        portfolio = Portfolio(initial_cash)
        executor = BacktestExecutor()

        # Build union timestamp index
        all_timestamps = sorted(set().union(*(df.index for df in datasets.values())))
        num_bars = len(all_timestamps)
        print(f"  Union timestamps: {num_bars} bars")

        # Trade log
        version = self.backtest.get("version", "")
        version_suffix = f"_{version}" if version else ""
        log_filename = f"{self.backtest['name']}_swing_party{version_suffix}.csv".replace(" ", "_")
        log_path = self.output_dir / "swing_party" / log_filename
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Track previous timestamp per symbol for gap detection
        prev_date_per_symbol: dict[str, pd.Timestamp] = {}

        execution_errors: list[str] = []

        with TradeLogger(str(log_path)) as trade_logger:
            for i, date in enumerate(all_timestamps):
                is_last_bar = i == num_bars - 1

                # Gather rows for symbols that have data at this timestamp
                rows = {}
                datasets_so_far = {}
                for symbol, df in datasets.items():
                    if date in df.index:
                        row = df.loc[date]
                        if isinstance(row, pd.DataFrame):
                            row = row.iloc[0]
                        rows[symbol] = row
                    datasets_so_far[symbol] = df.loc[:date]

                # ── Per-symbol data gap detection ──
                for symbol in list(rows.keys()):
                    if symbol in prev_date_per_symbol:
                        gap = (date - prev_date_per_symbol[symbol]).total_seconds()
                        if gap > 86400:
                            logger.info(
                                "[SwingParty] Data gap for %s: %s -> %s (%.0fh)",
                                symbol, prev_date_per_symbol[symbol], date, gap / 3600,
                            )
                            snap = self._snapshot(coordinator)
                            gap_actions = coordinator.force_close_symbol(symbol, portfolio)
                            prev_price = float(
                                datasets[symbol].loc[:prev_date_per_symbol[symbol]].iloc[-1]["close"]
                            )

                            gap_executed: list[tuple[str, Action, float]] = []
                            gap_log: list[dict] = []
                            gap_ok = True

                            for sym, action in gap_actions:
                                if action.action == ActionType.HOLD:
                                    continue
                                try:
                                    executor.execute(action, sym, prev_price, portfolio)
                                except ValueError as e:
                                    gap_ok = False
                                    msg = f"Gap-close {sym} {action.action.value} failed at {prev_date_per_symbol[symbol]}: {e}"
                                    logger.error(msg)
                                    execution_errors.append(msg)
                                    break
                                gap_executed.append((sym, action, prev_price))
                                prices = self._current_prices(datasets, date, portfolio)
                                gap_log.append(dict(
                                    date=str(prev_date_per_symbol[symbol]),
                                    action=action.action.value,
                                    symbol=sym,
                                    quantity=action.quantity,
                                    price=prev_price,
                                    cash_balance=portfolio.cash,
                                    portfolio_value=portfolio.total_value(prices),
                                    details=action.details,
                                    **_position_snapshot(portfolio, sym),
                                ))

                            if gap_ok:
                                for entry in gap_log:
                                    trade_logger.log(**entry)
                            else:
                                for s, a, p in reversed(gap_executed):
                                    _reverse_execute(executor, s, a, p, portfolio)
                                self._restore(coordinator, snap)
                                if self._on_execution_error:
                                    self._on_execution_error(msg)

                    prev_date_per_symbol[symbol] = date

                if not rows:
                    continue

                # ── Main bar: snapshot → on_bar → execute → commit or rollback ──
                snap = self._snapshot(coordinator)
                actions = coordinator.on_bar(date, rows, datasets_so_far,
                                             is_last_bar, portfolio)

                executed: list[tuple[str, Action, float]] = []
                log_buffer: list[dict] = []
                bar_ok = True
                fail_msg = ""

                for symbol, action in actions:
                    if action.action == ActionType.HOLD:
                        continue
                    price = self._execution_price(datasets, date, symbol, rows)
                    if price <= 0:
                        bar_ok = False
                        fail_msg = (
                            f"{date} {symbol} {action.action.value}: "
                            f"no execution price (missing bar or bad data)"
                        )
                        logger.error(fail_msg)
                        execution_errors.append(fail_msg)
                        break

                    try:
                        executor.execute(action, symbol, price, portfolio)
                    except ValueError as e:
                        bar_ok = False
                        fail_msg = f"{date} {symbol} {action.action.value} failed: {e}"
                        logger.error(fail_msg)
                        execution_errors.append(fail_msg)
                        break

                    executed.append((symbol, action, price))
                    prices = self._current_prices(datasets, date, portfolio)
                    log_buffer.append(dict(
                        date=str(date),
                        action=action.action.value,
                        symbol=symbol,
                        quantity=action.quantity,
                        price=price,
                        cash_balance=portfolio.cash,
                        portfolio_value=portfolio.total_value(prices),
                        details=action.details,
                        **_position_snapshot(portfolio, symbol),
                    ))

                if bar_ok:
                    for entry in log_buffer:
                        trade_logger.log(**entry)
                    if self._on_execution_error:
                        self._on_execution_error(None)
                else:
                    for sym, act, p in reversed(executed):
                        _reverse_execute(executor, sym, act, p, portfolio)
                    self._restore(coordinator, snap)
                    if self._on_execution_error:
                        self._on_execution_error(fail_msg)

                # Log periodic progress
                if i > 0 and i % 50000 == 0:
                    prices = self._current_prices(datasets, date, portfolio)
                    pv = portfolio.total_value(prices)
                    print(f"  Bar {i}/{num_bars} ({date}): PV=${pv:,.2f}")

        # Final value
        final_prices = self._current_prices(datasets, all_timestamps[-1], portfolio)
        # Include all assets in final prices (not just those in portfolio)
        for sym, df in datasets.items():
            if sym not in final_prices and not df.empty:
                final_prices[sym] = df.iloc[-1]["close"]
        final_value = portfolio.total_value(final_prices)

        # Resolve eviction events post-hoc using ST flips from the data
        st_atr = self.strategy_config.get("supertrend_atr_period", 10)
        st_mult = self.strategy_config.get("supertrend_multiplier", 2.0)
        resample = self.strategy_config.get("resample_interval", "1h")
        coordinator.eviction_tracker.resolve_from_data(
            datasets, resample, st_atr, st_mult
        )
        # Fallback: resolve any still-unresolved with final prices
        coordinator.eviction_tracker.force_resolve_at_end(
            final_prices, all_timestamps[-1]
        )

        return MultiAssetBacktestResult(
            strategy_name="swing_party",
            assets=assets,
            start_date=str(self.backtest["start_date"]),
            end_date=str(self.backtest["end_date"]),
            initial_cash=initial_cash,
            final_value=final_value,
            trade_log_path=str(log_path),
            eviction_stats=coordinator.eviction_tracker.compute_compound_pnl(),
            execution_errors=execution_errors,
        )

    def _execution_price(
        self,
        datasets: dict[str, pd.DataFrame],
        date: pd.Timestamp,
        symbol: str,
        rows: dict[str, pd.Series],
    ) -> float:
        """Bar close for execution: this bar if present, else last known close ≤ date (ffill).

        Union timestamps can exist on one asset but not another (missing 5m bar, shorter
        history, misaligned indices). Eviction/closes for symbol B must still execute when
        only symbol A has `rows` at this instant.
        """
        if symbol in rows:
            return float(rows[symbol]["close"])
        df = datasets.get(symbol)
        if df is None or df.empty:
            return 0.0
        loc = df.index.get_indexer([date], method="ffill")[0]
        if loc < 0:
            return 0.0
        return float(df.iloc[loc]["close"])

    def _current_prices(self, datasets: dict[str, pd.DataFrame],
                        date: pd.Timestamp, portfolio: Portfolio) -> dict[str, float]:
        """Get latest known prices for all symbols in the portfolio."""
        prices = {}
        for sym in list(portfolio.positions.keys()) + list(portfolio.short_positions.keys()):
            if sym in datasets:
                df = datasets[sym]
                loc = df.index.get_indexer([date], method="ffill")[0]
                if loc >= 0:
                    prices[sym] = df.iloc[loc]["close"]
        return prices
