"""LazySwing — dead-simple Supertrend trend follower.

Computes indicators on resampled bars (default 1h), trades on 5m bars.

Entry:  Supertrend flip (bearish→bullish = LONG, bullish→bearish = SHORT)
Exit:   ST flip = definitive exit, immediately flip to opposite side

Indicators (computed on resampled bars):
  - Supertrend(atr_period=10, multiplier=3.0) — entry/exit signals
  - HMACD(24, 51, 12) — trend confirmation (future use)
"""

import logging
import math
import numpy as np
import pandas as pd

from .base import StrategyBase, Action, ActionType, PortfolioView

logger = logging.getLogger(__name__)
from .intraday_indicators import compute_hma, compute_hmacd, compute_supertrend
from .macd_rsi_advanced import compute_atr


class LazySwingStrategy(StrategyBase):
    """LazySwing — ride Supertrend flips, exit on the flip back."""

    display_name = "LazySwing"

    def __init__(self, config: dict):
        super().__init__(config)

        self.symbol = config.get("symbol", "BTCUSDT")

        # Resample interval (default "1h"; set to "30min" for faster signals)
        self.resample_interval = config.get("resample_interval", "1h")

        # Supertrend
        self.st_atr_period = config.get("supertrend_atr_period", 13)
        self.st_multiplier = config.get("supertrend_multiplier", 2.5)

        # Strategy needs enough resampled bars for ATR warmup + band tightening.
        # 15x the ATR period (in resampled bars) is a safe minimum.
        self.min_warmup_hours = self.st_atr_period * 15

        # HMACD
        self.hmacd_fast = config.get("hmacd_fast", 24)
        self.hmacd_slow = config.get("hmacd_slow", 51)
        self.hmacd_signal = config.get("hmacd_signal", 12)

        # Cost per trade for PnL tracking
        self.cost_per_trade_pct = config.get("cost_per_trade_pct", 0.05)

        # Delayed entry: after ST flip, wait N hourly closes with consistent
        # direction before entering. 0 = enter immediately (current behavior).
        self.entry_delay_hours = config.get("entry_delay_hours", 0)

        # Minimum holding: suppress ST flip exits for the first N hourly bars
        # after entry. If ST flips back during hold, the whipsaw is absorbed.
        # 0 = exit immediately on any flip (current behavior).
        self.min_hold_hours = config.get("min_hold_hours", 0)

        # Confirmation ST: a wider/slower Supertrend used to filter entries.
        # When the primary ST flips, only enter if the confirmation ST agrees.
        # If it disagrees, go to cash until both align.
        # Set confirm_st_atr_period=0 to disable (default, current behavior).
        self.confirm_st_atr_period = config.get("confirm_st_atr_period", 0)
        self.confirm_st_multiplier = config.get("confirm_st_multiplier", 3.0)

        # Resample frequency offset (cached for boundary checks)
        self._resample_freq = pd.tseries.frequencies.to_offset(self.resample_interval)

        # State
        self._in_long = False
        self._in_short = False
        self._entry_price = 0.0
        self._entry_bar = 0
        self._bar_count = 0
        self._prev_st_bullish = None
        # Pending flip: enter opposite side on the very next bar after exit
        self._pending_long = False
        self._pending_short = False
        # Delayed entry state: count consecutive hourly closes confirming direction
        self._delayed_direction = None   # "long" or "short" or None
        self._delayed_confirm_count = 0
        # Minimum hold: hourly close count since entry
        self._hourly_closes_since_entry = 0

    def prepare(self, full_data: pd.DataFrame) -> None:
        """Resample to the configured interval and precompute indicators.

        Called once at startup and again when a resampled bar completes (via
        update()). Also called after gap recovery in live trading.
        """
        # Resample 5m → configured interval (e.g. "1h", "30min")
        resampled = full_data.resample(self.resample_interval).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()

        # Drop the last resampled bar if it's incomplete (partial bar).
        # This protects against unstable indicators when prepare() is called
        # mid-interval (e.g. after gap recovery at a non-boundary timestamp).
        if len(resampled) > 0 and len(full_data) > 0:
            last_5m_ts = full_data.index[-1]
            last_resample_start = last_5m_ts.floor(self._resample_freq)
            last_resample_end = last_resample_start + self._resample_freq - pd.Timedelta(minutes=5)
            if last_5m_ts < last_resample_end:
                resampled = resampled.iloc[:-1]
        self._hourly = resampled

        closes = resampled["close"]
        highs = resampled["high"]
        lows = resampled["low"]

        # Supertrend
        self._st_line, self._st_bullish = compute_supertrend(
            highs, lows, closes, self.st_atr_period, self.st_multiplier,
        )

        # ATR for exit distance calculation
        self._atr = compute_atr(highs, lows, closes, self.st_atr_period)

        # Confirmation Supertrend (wider, less reactive — filters whipsaw entries)
        if self.confirm_st_atr_period > 0:
            self._confirm_st_line, self._confirm_st_bullish = compute_supertrend(
                highs, lows, closes,
                self.confirm_st_atr_period, self.confirm_st_multiplier,
            )
        else:
            self._confirm_st_bullish = None

        # HMACD
        self._hmacd_line, self._hmacd_signal, self._hmacd_hist = compute_hmacd(
            closes, self.hmacd_fast, self.hmacd_slow, self.hmacd_signal,
        )

        # Map 5m timestamps → resampled bar index
        resampled_ts = resampled.index
        self._5m_to_hourly = {}
        for ts_5m in full_data.index:
            floored = ts_5m.floor(self._resample_freq)
            idx = resampled_ts.get_indexer([floored], method="ffill")[0]
            if idx >= 0:
                self._5m_to_hourly[ts_5m] = idx

        # Track the last completed resampled bar timestamp for update()
        self._last_resampled_ts = resampled.index[-1] if len(resampled) > 0 else None

    def update(self, full_data: pd.DataFrame) -> None:
        """Lightweight per-bar call. Only recomputes if a new resampled bar completed.

        Called by strategy_runner on every 5m bar in live trading. Avoids the
        cost of full resample + indicator recomputation on mid-interval bars.
        """
        if full_data.empty:
            return

        last_5m_ts = full_data.index[-1]

        # Check if the latest 5m bar completes a new resampled bar.
        # A bar at XX:55 completes the 1h bar starting at XX:00.
        # A bar at XX:25 or XX:55 completes a 30m bar.
        last_resample_start = last_5m_ts.floor(self._resample_freq)
        last_resample_end = last_resample_start + self._resample_freq - pd.Timedelta(minutes=5)

        if last_5m_ts >= last_resample_end:
            # New resampled bar just completed — full recompute
            if self._last_resampled_ts is None or last_resample_start > self._last_resampled_ts:
                self.prepare(full_data)
                return

        # Mid-interval: just map this 5m timestamp to the existing resampled bar
        if hasattr(self, '_hourly') and self._hourly is not None and len(self._hourly) > 0:
            floored = last_5m_ts.floor(self._resample_freq)
            resampled_ts = self._hourly.index
            idx = resampled_ts.get_indexer([floored], method="ffill")[0]
            if idx >= 0:
                self._5m_to_hourly[last_5m_ts] = idx

    def export_state(self) -> dict:
        return {
            "in_long": self._in_long,
            "in_short": self._in_short,
            "entry_price": self._entry_price,
            "entry_bar": self._entry_bar,
            "bar_count": self._bar_count,
            "prev_st_bullish": self._prev_st_bullish,
            "pending_long": self._pending_long,
            "pending_short": self._pending_short,
            "delayed_direction": self._delayed_direction,
            "delayed_confirm_count": self._delayed_confirm_count,
            "hourly_closes_since_entry": self._hourly_closes_since_entry,
            "prev_hourly_idx": getattr(self, "_prev_hourly_idx", -1),
        }

    def import_state(self, state: dict) -> None:
        if not state:
            return
        self._in_long = state.get("in_long", False)
        self._in_short = state.get("in_short", False)
        self._entry_price = state.get("entry_price", 0.0)
        self._entry_bar = state.get("entry_bar", 0)
        self._bar_count = state.get("bar_count", 0)
        self._prev_st_bullish = state.get("prev_st_bullish")
        self._pending_long = state.get("pending_long", False)
        self._pending_short = state.get("pending_short", False)
        self._delayed_direction = state.get("delayed_direction")
        self._delayed_confirm_count = state.get("delayed_confirm_count", 0)
        self._hourly_closes_since_entry = state.get("hourly_closes_since_entry", 0)
        self._prev_hourly_idx = state.get("prev_hourly_idx", -1)

    def reset_position(self) -> None:
        self._in_long = False
        self._in_short = False
        self._entry_price = 0.0
        self._entry_bar = 0
        self._prev_st_bullish = None
        self._pending_long = False
        self._pending_short = False
        self._delayed_direction = None
        self._delayed_confirm_count = 0
        self._hourly_closes_since_entry = 0

    def _confirm_agrees(self, hourly_idx, direction):
        """Check if the confirmation ST agrees with the proposed trade direction.
        Returns True if no confirmation ST is configured or if it agrees.
        """
        if self._confirm_st_bullish is None:
            return True
        if hourly_idx < 0 or pd.isna(self._confirm_st_bullish.iloc[hourly_idx]):
            return True
        confirm_bull = bool(self._confirm_st_bullish.iloc[hourly_idx])
        if direction == "long":
            return confirm_bull
        else:
            return not confirm_bull

    def warmup_bar(self, date, _row, _data_so_far, _is_last_bar) -> None:
        """Advance bar index and ST flip memory without trading (dataset starts before backtest).

        Indicators come from prepare(full_dataset); no per-bar update() in backtest mode.
        """
        self._bar_count += 1

        hourly_idx = self._5m_to_hourly.get(date)
        if hourly_idx is None or hourly_idx < 1:
            return

        st_line = self._st_line.iloc[hourly_idx]
        atr = self._atr.iloc[hourly_idx]

        if pd.isna(st_line) or pd.isna(atr) or atr == 0:
            return

        st_bullish = bool(self._st_bullish.iloc[hourly_idx])

        is_hourly_close = False
        if hourly_idx != getattr(self, "_prev_hourly_idx", -1):
            is_hourly_close = True
        self._prev_hourly_idx = hourly_idx

        if is_hourly_close:
            self._prev_st_bullish = st_bullish

    def on_bar(self, date, row, data_so_far, is_last_bar, pv) -> Action:
        self._bar_count += 1

        # Bi-directional reconciliation of internal position state against
        # actual broker portfolio.  Handles both:
        #   - Strategy thinks FLAT but broker has a position (state loss on restart)
        #   - Strategy thinks LONG/SHORT but broker is FLAT (order submission failed)
        broker_flat = pv.position_qty == 0 and pv.short_qty == 0
        if not self._in_long and not self._in_short:
            if pv.position_qty > 0:
                self._in_long = True
            elif pv.short_qty > 0:
                self._in_short = True
        elif broker_flat and (self._in_long or self._in_short):
            side = "LONG" if self._in_long else "SHORT"
            logger.warning(
                "Position state desync: strategy=%s but broker=FLAT — resetting to FLAT", side,
            )
            self._in_long = False
            self._in_short = False
            self._entry_price = 0.0

        hourly_idx = self._5m_to_hourly.get(date)
        if hourly_idx is None or hourly_idx < 1:
            return Action(ActionType.HOLD, details={"reason": "no_hourly_data"})

        close = row["close"]
        st_bullish = bool(self._st_bullish.iloc[hourly_idx])
        st_line = self._st_line.iloc[hourly_idx]
        atr = self._atr.iloc[hourly_idx]
        hmacd_hist = self._hmacd_hist.iloc[hourly_idx]

        # Need valid indicators
        if pd.isna(st_line) or pd.isna(atr) or atr == 0:
            return Action(ActionType.HOLD, details={"reason": "warmup"})

        # Check if this is an hourly close (new hourly bar)
        is_hourly_close = False
        if hourly_idx != getattr(self, "_prev_hourly_idx", -1):
            is_hourly_close = True
        self._prev_hourly_idx = hourly_idx

        # Track hourly closes since entry for min-hold logic
        if is_hourly_close and (self._in_long or self._in_short):
            self._hourly_closes_since_entry += 1

        indicators = {
            "is_hourly_close": is_hourly_close,
            "hourly_idx": int(hourly_idx),
            "close": float(close),
            "st_line": float(st_line),
            "st_bullish": st_bullish,
            "atr": float(atr),
            "hmacd_hist": float(hmacd_hist) if not pd.isna(hmacd_hist) else None,
            "dist_to_st_atr": float((close - st_line) / atr) if atr > 0 else 0,
        }

        # --- PENDING FLIP ENTRY (enter opposite side after exit) ---

        if self._pending_long and not self._in_long and not self._in_short:
            self._pending_long = False
            if not self._confirm_agrees(hourly_idx, "long"):
                pass  # stay flat — confirmation ST disagrees
            elif self.entry_delay_hours > 0:
                self._delayed_direction = "long"
                self._delayed_confirm_count = 0
            else:
                qty = pv.cash * 0.9999 / close
                if qty > 0:
                    self._in_long = True
                    self._entry_price = close
                    self._entry_bar = self._bar_count
                    self._hourly_closes_since_entry = 0
                    return Action(ActionType.BUY, qty, {
                        "entry_reason": "st_flip_bullish",
                        "immediate_flip": True,
                        "indicators": indicators,
                    })

        if self._pending_short and not self._in_long and not self._in_short:
            self._pending_short = False
            if not self._confirm_agrees(hourly_idx, "short"):
                pass  # stay flat — confirmation ST disagrees
            elif self.entry_delay_hours > 0:
                self._delayed_direction = "short"
                self._delayed_confirm_count = 0
            else:
                qty = pv.cash * 0.9999 / close
                if qty > 0:
                    self._in_short = True
                    self._entry_price = close
                    self._entry_bar = self._bar_count
                    self._hourly_closes_since_entry = 0
                    return Action(ActionType.SHORT, qty, {
                        "entry_reason": "st_flip_bearish",
                        "immediate_flip": True,
                        "indicators": indicators,
                    })

        # --- EXIT LOGIC ---

        if self._in_long:
            bars_held = self._bar_count - self._entry_bar

            # Definitive exit if ST flips bearish
            if not st_bullish and is_hourly_close:
                if self.min_hold_hours > 0 and self._hourly_closes_since_entry < self.min_hold_hours:
                    return Action(ActionType.HOLD, details={
                        "reason": "min_hold_suppressed",
                        "hourly_closes": self._hourly_closes_since_entry,
                        "indicators": indicators,
                    })
                pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                self._in_long = False
                self._hourly_closes_since_entry = 0
                self._pending_short = True  # flip to short on next bar
                return Action(ActionType.SELL, pv.position_qty, {
                    "exit_reason": "st_flip",
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "indicators": indicators,
                })

            return Action(ActionType.HOLD, details={"reason": "holding_long", "indicators": indicators})

        if self._in_short:
            bars_held = self._bar_count - self._entry_bar

            # Definitive exit if ST flips bullish
            if st_bullish and is_hourly_close:
                if self.min_hold_hours > 0 and self._hourly_closes_since_entry < self.min_hold_hours:
                    return Action(ActionType.HOLD, details={
                        "reason": "min_hold_suppressed",
                        "hourly_closes": self._hourly_closes_since_entry,
                        "indicators": indicators,
                    })
                pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                self._in_short = False
                self._hourly_closes_since_entry = 0
                self._pending_long = True  # flip to long on next bar
                return Action(ActionType.COVER, pv.short_qty, {
                    "exit_reason": "st_flip",
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "indicators": indicators,
                })

            return Action(ActionType.HOLD, details={"reason": "holding_short", "indicators": indicators})

        # --- DELAYED ENTRY LOGIC (count hourly confirmations) ---

        if self._delayed_direction is not None and is_hourly_close:
            if not self._in_long and not self._in_short:
                expected_bull = (self._delayed_direction == "long")
                if st_bullish == expected_bull:
                    self._delayed_confirm_count += 1
                    if self._delayed_confirm_count >= self.entry_delay_hours:
                        qty = pv.cash * 0.9999 / close
                        if qty > 0:
                            direction = self._delayed_direction
                            self._delayed_direction = None
                            self._delayed_confirm_count = 0
                            if direction == "long":
                                self._in_long = True
                                self._entry_price = close
                                self._entry_bar = self._bar_count
                                self._hourly_closes_since_entry = 0
                                return Action(ActionType.BUY, qty, {
                                    "entry_reason": "st_flip_bullish_delayed",
                                    "delay_hours": self.entry_delay_hours,
                                    "indicators": indicators,
                                })
                            else:
                                self._in_short = True
                                self._entry_price = close
                                self._entry_bar = self._bar_count
                                self._hourly_closes_since_entry = 0
                                return Action(ActionType.SHORT, qty, {
                                    "entry_reason": "st_flip_bearish_delayed",
                                    "delay_hours": self.entry_delay_hours,
                                    "indicators": indicators,
                                })
                else:
                    # ST flipped against pending direction — reset
                    self._delayed_direction = "long" if st_bullish else "short"
                    self._delayed_confirm_count = 1

        # --- ENTRY LOGIC (only on hourly close) ---

        if not is_hourly_close:
            return Action(ActionType.HOLD, details={"reason": "waiting_hourly", "indicators": indicators})

        # Detect ST flip
        prev_bull = self._prev_st_bullish
        self._prev_st_bullish = st_bullish

        if prev_bull is None:
            return Action(ActionType.HOLD, details={"reason": "first_bar", "indicators": indicators})

        # Long entry: ST flipped from bearish to bullish
        if st_bullish and not prev_bull:
            if self._confirm_agrees(hourly_idx, "long"):
                if self.entry_delay_hours > 0:
                    self._delayed_direction = "long"
                    self._delayed_confirm_count = 1
                else:
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        self._in_long = True
                        self._entry_price = close
                        self._entry_bar = self._bar_count
                        self._hourly_closes_since_entry = 0
                        return Action(ActionType.BUY, qty, {
                            "entry_reason": "st_flip_bullish",
                            "indicators": indicators,
                        })

        # Short entry: ST flipped from bullish to bearish
        if not st_bullish and prev_bull:
            if self._confirm_agrees(hourly_idx, "short"):
                if self.entry_delay_hours > 0:
                    self._delayed_direction = "short"
                    self._delayed_confirm_count = 1
                else:
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        self._in_short = True
                        self._entry_price = close
                        self._entry_bar = self._bar_count
                        self._hourly_closes_since_entry = 0
                        return Action(ActionType.SHORT, qty, {
                            "entry_reason": "st_flip_bearish",
                            "indicators": indicators,
                        })

        # When flat and confirmation ST now agrees with primary ST direction,
        # enter if not already in a position (catches deferred entries).
        if self.confirm_st_atr_period > 0 and not self._in_long and not self._in_short \
                and self._delayed_direction is None \
                and not self._pending_long and not self._pending_short:
            if st_bullish and self._confirm_agrees(hourly_idx, "long"):
                qty = pv.cash * 0.9999 / close
                if qty > 0:
                    self._in_long = True
                    self._entry_price = close
                    self._entry_bar = self._bar_count
                    self._hourly_closes_since_entry = 0
                    return Action(ActionType.BUY, qty, {
                        "entry_reason": "confirm_aligned_long",
                        "indicators": indicators,
                    })
            elif not st_bullish and self._confirm_agrees(hourly_idx, "short"):
                qty = pv.cash * 0.9999 / close
                if qty > 0:
                    self._in_short = True
                    self._entry_price = close
                    self._entry_bar = self._bar_count
                    self._hourly_closes_since_entry = 0
                    return Action(ActionType.SHORT, qty, {
                        "entry_reason": "confirm_aligned_short",
                        "indicators": indicators,
                    })

        return Action(ActionType.HOLD, details={"reason": "no_signal", "indicators": indicators})
