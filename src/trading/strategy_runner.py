"""Manages strategy lifecycle: indicator preparation, incremental bar feeding, diagnostics."""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from strategies.base import Action, ActionType, PortfolioView
from strategies.registry import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

DIAG_COLUMNS = [
    "datetime_local", "datetime_utc",
    "open", "high", "low", "close",
    "action", "reason",
    "is_hourly_close", "hourly_idx",
    "hma_slope", "st_bullish", "st_line", "trail_st",
    "adx", "short_adx",
    "kc_upper", "kc_mid", "kc_lower",
    "macd", "macd_signal", "macd_hist", "rsi",
]


class StrategyRunner:
    """Manages strategy lifecycle: indicator preparation, bar feeding, diagnostics.

    The StrategyRunner does NOT own portfolio state. Portfolio state is
    managed by the Broker and passed in as a PortfolioView on each bar.

    Strategy-agnostic — works with any strategy that implements the
    export_state/import_state interface from StrategyBase.
    """

    @staticmethod
    def get_min_warmup_hours(strategy_type: str, strategy_params: dict) -> int:
        """Instantiate a strategy to read its min_warmup_hours without loading data."""
        strat_cls = STRATEGY_REGISTRY.get(strategy_type)
        if not strat_cls:
            return 0
        strat = strat_cls(strategy_params)
        return getattr(strat, "min_warmup_hours", 0)

    def __init__(self, strategy_type: str, strategy_params: dict,
                 symbol: str, diagnostics_path: str | None = None):
        self.strategy_type = strategy_type
        self.strategy_params = {**strategy_params, "symbol": symbol}
        self.symbol = symbol
        self.strategy = None
        self._df_5m: pd.DataFrame | None = None
        self._diag_path = Path(diagnostics_path) if diagnostics_path else None
        self._diag_initialized = False

    def startup(self, df_5m: pd.DataFrame, df_1h: pd.DataFrame,
                exchange_price: float | None = None,
                strategy_state: dict | None = None):
        """Full startup: prepare indicators, restore strategy state.

        Args:
            df_5m: Full 5m DataFrame (current + previous month).
            df_1h: Full resampled 1h DataFrame.
            exchange_price: Current price from exchange for sanity check.
            strategy_state: Persisted strategy state dict (from export_state).
        """
        self._df_5m = df_5m

        strat_cls = STRATEGY_REGISTRY[self.strategy_type]
        self.strategy = strat_cls(self.strategy_params)

        logger.info("Running prepare() on %d 5m bars...", len(df_5m))
        self.strategy.prepare(df_5m)

        # Log indicator count (strategy-agnostic)
        hourly_count = 0
        if hasattr(self.strategy, '_hourly') and self.strategy._hourly is not None:
            hourly_count = len(self.strategy._hourly)
        elif hasattr(self.strategy, '_indicators') and self.strategy._indicators is not None:
            hourly_count = len(self.strategy._indicators)
        logger.info("Indicators precomputed: %d resampled bars.", hourly_count)

        # Restore strategy state from persisted dict
        if strategy_state is not None:
            self.strategy.import_state(strategy_state)
            logger.info("Strategy state restored from persisted file.")

            # Warn if position contradicts current indicators
            strat = self.strategy
            if hasattr(strat, '_st_bullish') and strat._st_bullish is not None and len(strat._st_bullish) > 0:
                current_st_bull = bool(strat._st_bullish.iloc[-1])
                in_long = getattr(strat, '_in_long', False)
                in_short = getattr(strat, '_in_short', False)
                if in_short and current_st_bull:
                    logger.warning(
                        "Position/indicator mismatch: SHORT but ST is BULLISH. "
                        "Will correct on next hourly close."
                    )
                elif in_long and not current_st_bull:
                    logger.warning(
                        "Position/indicator mismatch: LONG but ST is BEARISH. "
                        "Will correct on next hourly close."
                    )
        else:
            logger.info("No persisted strategy state — starting fresh.")

        if exchange_price is not None:
            self._price_sanity_check(df_5m, exchange_price)

    def on_5m_bar(self, df_5m_updated: pd.DataFrame,
                  portfolio_view: PortfolioView) -> Action:
        """Process a 5m bar. Called on every new 5m bar.

        The strategy handles hourly bar detection internally (e.g.,
        swing_trend uses _bar_to_hourly_idx + _prev_hourly_idx).
        Entry logic fires only on hourly boundaries; exits (stops)
        are checked on every 5m bar.

        The strategy's on_bar() is a pure signal generator — it reads the
        PortfolioView but never mutates the portfolio.

        Args:
            df_5m_updated: Full 5m DataFrame including the latest bar.
            portfolio_view: Frozen snapshot of portfolio state from the broker.

        Returns:
            Action from the strategy (BUY/SELL/SHORT/COVER/HOLD).
        """
        self._df_5m = df_5m_updated
        if hasattr(self.strategy, 'update'):
            self.strategy.update(df_5m_updated)
        else:
            self.strategy.prepare(df_5m_updated)

        last_date = df_5m_updated.index[-1]
        last_row = df_5m_updated.iloc[-1]

        action = self.strategy.on_bar(
            last_date, last_row, df_5m_updated, is_last_bar=False, pv=portfolio_view
        )

        reason = action.details.get("reason", "")
        if action.action.value != "HOLD":
            logger.info("on_bar() → %s | %s", action.action.value, reason)
        else:
            logger.debug("on_bar() → HOLD | %s", reason)

        self._write_diagnostics(last_date, last_row, action)

        return action

    def _write_diagnostics(self, date: pd.Timestamp, row: pd.Series, action: Action):
        """Append a row to the diagnostics CSV with prices, decision, and indicators."""
        if self._diag_path is None:
            return

        ind = action.details.get("indicators", {})
        reason = action.details.get("reason", "")
        if action.action.value != "HOLD":
            reason = action.details.get("entry_trigger", action.details.get("exit_reason", reason))

        utc_dt = date.tz_localize("UTC") if date.tzinfo is None else date.tz_convert("UTC")
        local_dt = utc_dt.to_pydatetime().astimezone()

        row_dict = {
            "datetime_local": local_dt.strftime("%Y-%m-%d %H:%M"),
            "datetime_utc": utc_dt.strftime("%Y-%m-%d %H:%M"),
            "open": f"{row['open']:.2f}",
            "high": f"{row['high']:.2f}",
            "low": f"{row['low']:.2f}",
            "close": f"{row['close']:.2f}",
            "action": action.action.value,
            "reason": reason,
            "is_hourly_close": ind.get("is_hourly_close", ""),
            "hourly_idx": ind.get("hourly_idx", ""),
            "hma_slope": f"{ind['hma_slope']:.4f}" if ind.get("hma_slope") is not None else "",
            "st_bullish": ind.get("st_bullish", ""),
            "st_line": f"{ind['st_line']:.2f}" if ind.get("st_line") is not None else "",
            "trail_st": f"{ind['trail_st']:.2f}" if ind.get("trail_st") is not None else "",
            "adx": f"{ind['adx']:.2f}" if ind.get("adx") is not None else "",
            "short_adx": f"{ind['short_adx']:.2f}" if ind.get("short_adx") is not None else "",
            "kc_upper": f"{ind['kc_upper']:.2f}" if ind.get("kc_upper") is not None else "",
            "kc_mid": f"{ind['kc_mid']:.2f}" if ind.get("kc_mid") is not None else "",
            "kc_lower": f"{ind['kc_lower']:.2f}" if ind.get("kc_lower") is not None else "",
            "macd": f"{ind['macd']:.4f}" if ind.get("macd") is not None else "",
            "macd_signal": f"{ind['macd_signal']:.4f}" if ind.get("macd_signal") is not None else "",
            "macd_hist": f"{ind['macd_hist']:.4f}" if ind.get("macd_hist") is not None else "",
            "rsi": f"{ind['rsi']:.2f}" if ind.get("rsi") is not None else "",
        }

        write_header = not self._diag_initialized and not self._diag_path.exists()
        try:
            with open(self._diag_path, "a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=DIAG_COLUMNS)
                if write_header:
                    writer.writeheader()
                writer.writerow(row_dict)
            self._diag_initialized = True
        except Exception as e:
            logger.warning("Failed to write diagnostics: %s", e)

    def get_strategy_state(self) -> dict:
        """Get serializable strategy state for persistence."""
        return self.strategy.export_state()

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
