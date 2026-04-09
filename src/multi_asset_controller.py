"""Multi-asset backtest controller for SwingParty.

Loads N DataFrames (one per asset), iterates in lockstep across a union
timestamp index, feeds the SwingPartyCoordinator, and executes returned
actions on a shared portfolio.
"""

import logging
from pathlib import Path

import pandas as pd

from portfolio import Portfolio
from execution.backtest_executor import BacktestExecutor
from trade_log import TradeLogger
from strategies.base import ActionType
from strategies.swing_party import SwingPartyCoordinator
from data_sources.registry import DATA_SOURCE_REGISTRY, PARSER_REGISTRY

logger = logging.getLogger(__name__)


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
    ):
        self.strategy_name = strategy_name
        self.assets = assets
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash
        self.final_value = final_value
        self.trade_log_path = trade_log_path
        self.eviction_stats = eviction_stats or {}

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


class MultiAssetController:
    """Orchestrates a multi-asset backtest with SwingPartyCoordinator."""

    def __init__(self, config: dict, output_dir: str = "reports"):
        self.config = config
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.backtest = config["backtest"]
        self.data_config = config["data_source"]
        self.strategy_config = config["strategy"]

    def _load_datasets(self) -> dict[str, pd.DataFrame]:
        """Load one DataFrame per asset using the config's file pattern."""
        parser_cls = PARSER_REGISTRY[self.data_config["parser"]]
        source_cls = DATA_SOURCE_REGISTRY[self.data_config["type"]]
        parser = parser_cls()

        params = self.data_config.get("params", {})
        data_dir = params.get("data_dir", "data")
        file_pattern = params.get("file_pattern", "{symbol}-5m-{start_year}-{end_year}-combined.csv")

        start_year = str(self.backtest["start_date"])[:4]
        end_year = str(self.backtest["end_date"])[:4]

        assets = self.strategy_config.get("assets", [])
        datasets = {}

        for symbol in assets:
            filename = file_pattern.format(
                symbol=symbol, start_year=start_year, end_year=end_year
            )
            file_path = str(Path(data_dir) / filename)

            source_params = {**params, "file_path": file_path, "symbol": symbol}
            source = source_cls(parser, source_params)

            start_date = str(self.backtest["start_date"])
            end_date = str(self.backtest["end_date"])
            data = source.get_data(symbol, start_date, end_date)

            if data.empty:
                logger.warning(f"No data for {symbol} at {file_path}, skipping")
                continue

            datasets[symbol] = data
            logger.info(f"Loaded {symbol}: {len(data)} bars "
                        f"({data.index[0]} to {data.index[-1]})")

        return datasets

    def run(self) -> MultiAssetBacktestResult:
        """Run the multi-asset backtest."""
        datasets = self._load_datasets()
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

        with TradeLogger(str(log_path)) as trade_logger:
            for i, date in enumerate(all_timestamps):
                is_last_bar = i == num_bars - 1

                # Gather rows for symbols that have data at this timestamp
                rows = {}
                datasets_so_far = {}
                for symbol, df in datasets.items():
                    if date in df.index:
                        row = df.loc[date]
                        # Handle duplicate timestamps (take first)
                        if isinstance(row, pd.DataFrame):
                            row = row.iloc[0]
                        rows[symbol] = row
                    # Always provide data_so_far for held symbols
                    datasets_so_far[symbol] = df.loc[:date]

                # Per-symbol data gap detection
                for symbol in list(rows.keys()):
                    if symbol in prev_date_per_symbol:
                        gap = (date - prev_date_per_symbol[symbol]).total_seconds()
                        if gap > 86400:  # >24h gap
                            logger.info(f"[SwingParty] Data gap for {symbol}: "
                                        f"{prev_date_per_symbol[symbol]} -> {date} ({gap/3600:.0f}h)")
                            gap_actions = coordinator.force_close_symbol(symbol, portfolio)
                            prev_price = datasets[symbol].loc[:prev_date_per_symbol[symbol]].iloc[-1]["close"]
                            for sym, action in gap_actions:
                                if action.action != ActionType.HOLD:
                                    executor.execute(action, sym, prev_price, portfolio)
                                prices = self._current_prices(datasets, date, portfolio)
                                trade_logger.log(
                                    date=str(prev_date_per_symbol[symbol]),
                                    action=action.action.value,
                                    symbol=sym,
                                    quantity=action.quantity,
                                    price=prev_price,
                                    cash_balance=portfolio.cash,
                                    portfolio_value=portfolio.total_value(prices),
                                    details=action.details,
                                    **_position_snapshot(portfolio, sym),
                                )
                    prev_date_per_symbol[symbol] = date

                if not rows:
                    continue

                # Get coordinator actions
                actions = coordinator.on_bar(date, rows, datasets_so_far,
                                             is_last_bar, portfolio)

                # Execute actions
                for symbol, action in actions:
                    if action.action == ActionType.HOLD:
                        continue
                    price = rows[symbol]["close"] if symbol in rows else 0
                    if price <= 0:
                        continue

                    try:
                        executor.execute(action, symbol, price, portfolio)
                    except ValueError as e:
                        logger.warning(f"Execution failed for {symbol}: {e}")
                        continue

                    prices = self._current_prices(datasets, date, portfolio)
                    trade_logger.log(
                        date=str(date),
                        action=action.action.value,
                        symbol=symbol,
                        quantity=action.quantity,
                        price=price,
                        cash_balance=portfolio.cash,
                        portfolio_value=portfolio.total_value(prices),
                        details=action.details,
                        **_position_snapshot(portfolio, symbol),
                    )

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
        )

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
