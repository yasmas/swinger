"""LazySwing v1 — dead-simple Supertrend trend follower.

Computes indicators on 1h resampled bars, trades on 5m bars.

Entry:  Supertrend flip (bearish→bullish = LONG, bullish→bearish = SHORT)
Exit:   Price approaches ST line within exit_atr_fraction * ATR
        → temporary exit only; re-enter if price recovers while ST holds
        ST flip = definitive exit (no re-entry on same side)

Indicators (computed on 1h bars):
  - Supertrend(atr_period=10, multiplier=3.0) — entry/exit signals
  - HMACD(24, 51, 12) — trend confirmation (future use)
"""

import math
import numpy as np
import pandas as pd

from .base import StrategyBase, Action, ActionType, PortfolioView
from .intraday_indicators import compute_hma, compute_hmacd, compute_supertrend
from .macd_rsi_advanced import compute_atr


class LazySwingStrategy(StrategyBase):
    """LazySwing — ride Supertrend flips, exit before the flip back."""

    display_name = "LazySwing"

    def __init__(self, config: dict):
        super().__init__(config)

        self.symbol = config.get("symbol", "BTCUSDT")

        # Supertrend
        self.st_atr_period = config.get("supertrend_atr_period", 13)
        self.st_multiplier = config.get("supertrend_multiplier", 2.5)

        # Strategy needs enough hourly bars for ATR warmup + band tightening to stabilize.
        # 15x the ATR period (in hours) is a safe minimum.
        self.min_warmup_hours = self.st_atr_period * 15

        # HMACD
        self.hmacd_fast = config.get("hmacd_fast", 24)
        self.hmacd_slow = config.get("hmacd_slow", 51)
        self.hmacd_signal = config.get("hmacd_signal", 12)

        # Exit mode: "st_flip_only" = hold until ST flips (simplest)
        #            "proximity" = exit early when price nears ST line, re-enter if recovers
        self.exit_mode = config.get("exit_mode", "st_flip_only")

        # Proximity exit params (only used if exit_mode == "proximity")
        self.exit_atr_fraction = config.get("exit_atr_fraction", 0.25)
        self.reentry_atr_fraction = config.get("reentry_atr_fraction", 0.75)

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

        # State
        self._in_long = False
        self._in_short = False
        self._entry_price = 0.0
        self._entry_bar = 0
        self._bar_count = 0
        self._prev_st_bullish = None
        # Track whether we're "parked" (exited on proximity, waiting for re-entry)
        self._parked_long = False   # ST still bullish, but we exited on proximity
        self._parked_short = False  # ST still bearish, but we exited on proximity
        # Pending flip: enter opposite side on the very next bar after exit
        self._pending_long = False
        self._pending_short = False
        # Delayed entry state: count consecutive hourly closes confirming direction
        self._delayed_direction = None   # "long" or "short" or None
        self._delayed_confirm_count = 0
        # Minimum hold: hourly close count since entry
        self._hourly_closes_since_entry = 0

    def prepare(self, full_data: pd.DataFrame) -> None:
        """Resample to 1h and precompute indicators."""
        # Resample 5m → 1h
        hourly = full_data.resample("1h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        self._hourly = hourly

        closes = hourly["close"]
        highs = hourly["high"]
        lows = hourly["low"]

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

        # Map 5m timestamps → hourly index
        hourly_ts = hourly.index
        self._5m_to_hourly = {}
        for ts_5m in full_data.index:
            floored = ts_5m.floor("h")
            idx = hourly_ts.get_indexer([floored], method="ffill")[0]
            if idx >= 0:
                self._5m_to_hourly[ts_5m] = idx

    def reset_position(self) -> None:
        self._in_long = False
        self._in_short = False
        self._entry_price = 0.0
        self._entry_bar = 0
        self._prev_st_bullish = None
        self._parked_long = False
        self._parked_short = False
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

    def on_bar(self, date, row, data_so_far, is_last_bar, pv) -> Action:
        self._bar_count += 1

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

        # --- EXIT LOGIC (check every 5m bar) ---

        if self._in_long:
            dist = close - st_line  # positive when price above support
            bars_held = self._bar_count - self._entry_bar

            # Proximity exit (only in proximity mode)
            if self.exit_mode == "proximity" and dist < self.exit_atr_fraction * atr:
                pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                self._in_long = False
                self._parked_long = True
                return Action(ActionType.SELL, pv.position_qty, {
                    "exit_reason": "st_proximity",
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "dist_atr": round(dist / atr, 3),
                    "indicators": indicators,
                })

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
                self._parked_long = False
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
            dist = st_line - close  # positive when price below resistance
            bars_held = self._bar_count - self._entry_bar

            # Proximity exit (only in proximity mode)
            if self.exit_mode == "proximity" and dist < self.exit_atr_fraction * atr:
                pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                self._in_short = False
                self._parked_short = True
                return Action(ActionType.COVER, pv.short_qty, {
                    "exit_reason": "st_proximity",
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "dist_atr": round(dist / atr, 3),
                    "indicators": indicators,
                })

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
                self._parked_short = False
                self._hourly_closes_since_entry = 0
                self._pending_long = True  # flip to long on next bar
                return Action(ActionType.COVER, pv.short_qty, {
                    "exit_reason": "st_flip",
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "indicators": indicators,
                })

            return Action(ActionType.HOLD, details={"reason": "holding_short", "indicators": indicators})

        # --- RE-ENTRY LOGIC (proximity mode only) ---
        if self.exit_mode != "proximity":
            self._parked_long = False
            self._parked_short = False

        if self._parked_long:
            # ST flipped → cancel the park
            if not st_bullish:
                self._parked_long = False
            else:
                dist = close - st_line
                if dist > self.reentry_atr_fraction * atr:
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        self._parked_long = False
                        self._in_long = True
                        self._entry_price = close
                        self._entry_bar = self._bar_count
                        self._hourly_closes_since_entry = 0
                        return Action(ActionType.BUY, qty, {
                            "entry_reason": "reentry_long",
                            "indicators": indicators,
                        })

        if self._parked_short:
            if st_bullish:
                self._parked_short = False
            else:
                dist = st_line - close
                if dist > self.reentry_atr_fraction * atr:
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        self._parked_short = False
                        self._in_short = True
                        self._entry_price = close
                        self._entry_bar = self._bar_count
                        self._hourly_closes_since_entry = 0
                        return Action(ActionType.SHORT, qty, {
                            "entry_reason": "reentry_short",
                            "indicators": indicators,
                        })

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
            self._parked_short = False
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
            self._parked_long = False
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
                and not self._parked_long and not self._parked_short \
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
