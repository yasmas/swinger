"""Wraps the existing strategy for paper trading: trade-log reconstruction + incremental feeding."""

import json
import logging
from pathlib import Path

import pandas as pd

from portfolio import Portfolio
from strategies.base import Action, ActionType
from strategies.registry import STRATEGY_REGISTRY
from trade_log import TradeLogReader

logger = logging.getLogger(__name__)

TRADE_ACTIONS = {"BUY", "SELL", "SHORT", "COVER"}


class StrategyRunner:
    """Manages strategy lifecycle for paper trading.

    On startup: reconstructs portfolio from trade log, derives strategy
    internal state from indicators + trade log, and prepares for incremental
    bar feeding. Version-safe — uses trade log as ground truth, not replay.
    """

    def __init__(self, strategy_type: str, strategy_params: dict,
                 initial_cash: float, symbol: str, trade_log_path: str):
        self.strategy_type = strategy_type
        self.strategy_params = {**strategy_params, "symbol": symbol}
        self.initial_cash = initial_cash
        self.symbol = symbol
        self.trade_log_path = Path(trade_log_path)
        self.portfolio: Portfolio | None = None
        self.strategy = None
        self._df_5m: pd.DataFrame | None = None

    def startup(self, df_5m: pd.DataFrame, df_1h: pd.DataFrame,
                exchange_price: float | None = None):
        """Full startup: prepare indicators, reconstruct portfolio, derive state.

        Args:
            df_5m: Full 5m DataFrame (current + previous month).
            df_1h: Full resampled 1h DataFrame.
            exchange_price: Current price from exchange for sanity check.
        """
        self._df_5m = df_5m

        self.portfolio = Portfolio(self.initial_cash)
        strat_cls = STRATEGY_REGISTRY[self.strategy_type]
        self.strategy = strat_cls(self.portfolio, self.strategy_params)

        logger.info("Running prepare() on %d 5m bars...", len(df_5m))
        self.strategy.prepare(df_5m)
        logger.info("Indicators precomputed: %d resampled bars.", len(self.strategy._indicators))

        trades = self._load_trades()
        if trades is not None and not trades.empty:
            self._reconstruct_portfolio(trades)
            self._derive_strategy_state(trades, df_5m)
        else:
            logger.info("No trade log found — starting with fresh portfolio ($%.2f).", self.initial_cash)

        self._cross_check_portfolio(trades)

        if exchange_price is not None:
            self._price_sanity_check(df_5m, exchange_price)

        portfolio_value = self.portfolio.total_value({self.symbol: df_5m.iloc[-1]["close"]})
        logger.info(
            "Startup complete. Cash: $%.2f, Portfolio value: $%.2f, "
            "Long: %s, Short: %s",
            self.portfolio.cash, portfolio_value,
            f"{self.portfolio.positions[self.symbol].quantity:.8f} @ ${self.portfolio.positions[self.symbol].avg_cost:.2f}"
            if self.symbol in self.portfolio.positions else "none",
            f"{self.portfolio.short_positions[self.symbol].quantity:.8f} @ ${self.portfolio.short_positions[self.symbol].avg_cost:.2f}"
            if self.symbol in self.portfolio.short_positions else "none",
        )

    def on_5m_bar(self, df_5m_updated: pd.DataFrame, is_hour_boundary: bool) -> Action:
        """Process a 5m bar. Called on every new 5m bar.

        On hour boundaries (XX:55 bar, complete 1h bar ready): entry logic is
        allowed to fire and _prev_* indicator values are updated for the next
        cross-detection cycle.

        On intra-hour bars: only stop-loss / trailing-stop logic runs. Entry is
        suppressed and the saved _prev_* values are preserved so that MACD cross
        detection at the next hour boundary compares two complete consecutive 1h
        bars (matching backtester behaviour).

        Args:
            df_5m_updated: Full 5m DataFrame including the latest bar.
            is_hour_boundary: True only for the XX:55 bar that completes a 1h bar.

        Returns:
            Action from the strategy (BUY/SELL/SHORT/COVER/HOLD).
        """
        self._df_5m = df_5m_updated
        self.strategy.prepare(df_5m_updated)

        last_date = df_5m_updated.index[-1]
        last_row = df_5m_updated.iloc[-1]

        current_idx = self.strategy._get_resampled_bar_idx(last_date)

        if is_hour_boundary:
            # Force new_resampled_bar = True so the strategy sees a completed 1h bar.
            # Subtract 1 so that on_bar's comparison (current_idx != _resampled_bar_count)
            # yields True, matching the backtester transition at each hour boundary.
            self.strategy._resampled_bar_count = current_idx - 1
            action = self.strategy.on_bar(last_date, last_row, df_5m_updated, is_last_bar=False)
        else:
            # Force new_resampled_bar = False so entries are suppressed.
            self.strategy._resampled_bar_count = current_idx
            # Preserve _prev_* so MACD cross detection at the next hour boundary
            # still compares two consecutive complete 1h bars.
            saved = (
                self.strategy._prev_macd,
                self.strategy._prev_signal,
                self.strategy._prev_rsi,
                self.strategy._prev_histogram,
            )
            action = self.strategy.on_bar(last_date, last_row, df_5m_updated, is_last_bar=False)
            self.strategy._prev_macd = saved[0]
            self.strategy._prev_signal = saved[1]
            self.strategy._prev_rsi = saved[2]
            self.strategy._prev_histogram = saved[3]

        reason = action.details.get("reason", "")
        if action.action.value != "HOLD":
            logger.info("on_bar() → %s | %s", action.action.value, reason)
        else:
            logger.debug("on_bar() → HOLD | %s", reason)

        return action

    def _load_trades(self) -> pd.DataFrame | None:
        """Load the trade log CSV. Returns None if file doesn't exist."""
        if not self.trade_log_path.exists():
            return None
        try:
            df = TradeLogReader.read(str(self.trade_log_path))
            trade_count = len(df[df["action"].isin(TRADE_ACTIONS)])
            logger.info("Loaded trade log: %d rows, %d trades.", len(df), trade_count)
            return df
        except Exception as e:
            logger.warning("Failed to read trade log %s: %s", self.trade_log_path, e)
            return None

    def _reconstruct_portfolio(self, trades: pd.DataFrame):
        """Replay BUY/SELL/SHORT/COVER from the trade log onto a fresh portfolio."""
        actions = trades[trades["action"].isin(TRADE_ACTIONS)]

        for _, row in actions.iterrows():
            action = row["action"]
            qty = float(row["quantity"])
            price = float(row["price"])

            if action == "BUY":
                self.portfolio.buy(self.symbol, qty, price)
            elif action == "SELL":
                self.portfolio.sell(self.symbol, qty, price)
            elif action == "SHORT":
                self.portfolio.short_sell(self.symbol, qty, price)
            elif action == "COVER":
                self.portfolio.cover(self.symbol, qty, price)

        logger.info(
            "Portfolio reconstructed from %d trades. Cash: $%.2f",
            len(actions), self.portfolio.cash,
        )

    def _derive_strategy_state(self, trades: pd.DataFrame, df_5m: pd.DataFrame):
        """Set strategy tracking fields from trade log + precomputed indicators."""
        actions = trades[trades["action"].isin(TRADE_ACTIONS)]
        if actions.empty:
            return

        last_trade = actions.iloc[-1]
        last_action = last_trade["action"]
        last_price = float(last_trade["price"])
        last_date = last_trade["date"]

        has_long = self.symbol in self.portfolio.positions
        has_short = self.symbol in self.portfolio.short_positions

        # _prev_macd, _prev_signal, _prev_rsi, _prev_histogram:
        # Look up indicator values at the last 1h bar
        if self.strategy._indicators is not None and len(self.strategy._indicators) > 0:
            last_ind = self.strategy._indicators.iloc[-1]
            self.strategy._prev_macd = float(last_ind["macd"])
            self.strategy._prev_signal = float(last_ind["macd_signal"])
            self.strategy._prev_rsi = float(last_ind["rsi"])
            self.strategy._prev_histogram = float(last_ind["histogram"])

        # _resampled_bar_count: set to last index in indicators
        if self.strategy._resampled_index is not None:
            self.strategy._resampled_bar_count = len(self.strategy._resampled_index) - 1

        # _pending_cross_bars: reset — any pending cross from before shutdown is expired
        self.strategy._pending_cross_bars = 0

        if has_long:
            # _entry_price: the last BUY price
            buys = actions[actions["action"] == "BUY"]
            if not buys.empty:
                self.strategy._entry_price = float(buys.iloc[-1]["price"])

            # _peak_since_entry: max high of 5m bars since entry
            entry_time = buys.iloc[-1]["date"] if not buys.empty else last_date
            bars_since = df_5m[df_5m.index >= entry_time]
            if not bars_since.empty:
                self.strategy._peak_since_entry = float(bars_since["high"].max())
            else:
                self.strategy._peak_since_entry = last_price

            self.strategy._bars_since_exit = self.strategy.cooldown_bars
            self.strategy._last_exit_profitable = False

        elif has_short:
            # _short_entry_price: the last SHORT price
            shorts = actions[actions["action"] == "SHORT"]
            if not shorts.empty:
                self.strategy._short_entry_price = float(shorts.iloc[-1]["price"])

            # _trough_since_entry: min low of 5m bars since entry
            entry_time = shorts.iloc[-1]["date"] if not shorts.empty else last_date
            bars_since = df_5m[df_5m.index >= entry_time]
            if not bars_since.empty:
                self.strategy._trough_since_entry = float(bars_since["low"].min())
            else:
                self.strategy._trough_since_entry = last_price

            self.strategy._bars_since_exit = self.strategy.cooldown_bars
            self.strategy._last_exit_profitable = False

        else:
            # Flat — compute bars since last exit and whether it was profitable
            sells = actions[actions["action"] == "SELL"]
            covers = actions[actions["action"] == "COVER"]
            last_exit = None
            last_exit_action = None
            if not sells.empty and not covers.empty:
                if sells.iloc[-1]["date"] > covers.iloc[-1]["date"]:
                    last_exit = sells.iloc[-1]
                    last_exit_action = "SELL"
                else:
                    last_exit = covers.iloc[-1]
                    last_exit_action = "COVER"
            elif not sells.empty:
                last_exit = sells.iloc[-1]
                last_exit_action = "SELL"
            elif not covers.empty:
                last_exit = covers.iloc[-1]
                last_exit_action = "COVER"

            if last_exit is not None:
                exit_time = last_exit["date"]
                exit_price = float(last_exit["price"])

                # Count 1h bars since exit
                if self.strategy._resampled_index is not None:
                    hourly_since = self.strategy._resampled_index[
                        self.strategy._resampled_index > exit_time
                    ]
                    self.strategy._bars_since_exit = len(hourly_since)
                else:
                    self.strategy._bars_since_exit = self.strategy.cooldown_bars

                # Was last exit profitable?
                if last_exit_action == "SELL":
                    buys_before = actions[
                        (actions["action"] == "BUY") & (actions["date"] < exit_time)
                    ]
                    if not buys_before.empty:
                        entry_price = float(buys_before.iloc[-1]["price"])
                        self.strategy._last_exit_profitable = exit_price > entry_price
                elif last_exit_action == "COVER":
                    self.strategy._last_exit_profitable = False
            else:
                self.strategy._bars_since_exit = self.strategy.cooldown_bars
                self.strategy._last_exit_profitable = False

            self.strategy._entry_price = None
            self.strategy._peak_since_entry = None
            self.strategy._short_entry_price = None
            self.strategy._trough_since_entry = None

        logger.info(
            "Strategy state derived: entry=%.2f, peak=%.2f, trough=%s, "
            "bars_since_exit=%d, last_profitable=%s, pending_cross=%d",
            self.strategy._entry_price or 0,
            self.strategy._peak_since_entry or 0,
            f"{self.strategy._trough_since_entry:.2f}" if self.strategy._trough_since_entry else "none",
            self.strategy._bars_since_exit,
            self.strategy._last_exit_profitable,
            self.strategy._pending_cross_bars,
        )

    def _cross_check_portfolio(self, trades: pd.DataFrame | None):
        """Independently verify portfolio state matches trade log expectations."""
        if trades is None or trades.empty:
            return

        # The trade log already has cash_balance and portfolio_value columns.
        # Compare our reconstructed cash against the last logged cash_balance.
        last_logged_cash = float(trades.iloc[-1]["cash_balance"])
        actual_cash = self.portfolio.cash
        diff = abs(actual_cash - last_logged_cash)

        if diff > 0.02:
            logger.warning(
                "Portfolio cross-check MISMATCH: reconstructed cash=$%.2f, "
                "trade log says $%.2f (diff=$%.2f)",
                actual_cash, last_logged_cash, diff,
            )
        else:
            logger.info("Portfolio cross-check passed (cash diff: $%.4f).", diff)

    def _price_sanity_check(self, df_5m: pd.DataFrame, exchange_price: float):
        """Warn if local data price diverges significantly from exchange."""
        if df_5m.empty:
            return
        local_price = float(df_5m.iloc[-1]["close"])
        pct_diff = abs(exchange_price - local_price) / local_price * 100

        if pct_diff > 1.0:
            logger.warning(
                "Price sanity check WARNING: local=%.2f, exchange=%.2f (%.2f%% diff)",
                local_price, exchange_price, pct_diff,
            )
        else:
            logger.info(
                "Price sanity check passed: local=%.2f, exchange=%.2f (%.4f%% diff)",
                local_price, exchange_price, pct_diff,
            )
