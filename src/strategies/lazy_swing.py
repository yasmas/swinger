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

    def __init__(self, config: dict):
        super().__init__(config)

        self.symbol = config.get("symbol", "BTCUSDT")

        # Supertrend
        self.st_atr_period = config.get("supertrend_atr_period", 13)
        self.st_multiplier = config.get("supertrend_multiplier", 2.5)

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

        # --- PENDING FLIP ENTRY (enter opposite side immediately after exit) ---

        if self._pending_long and not self._in_long and not self._in_short:
            qty = pv.cash * 0.9999 / close
            if qty > 0:
                self._pending_long = False
                self._in_long = True
                self._entry_price = close
                self._entry_bar = self._bar_count
                return Action(ActionType.BUY, qty, {
                    "entry_reason": "st_flip_bullish",
                    "immediate_flip": True,
                    "indicators": indicators,
                })

        if self._pending_short and not self._in_long and not self._in_short:
            qty = pv.cash * 0.9999 / close
            if qty > 0:
                self._pending_short = False
                self._in_short = True
                self._entry_price = close
                self._entry_bar = self._bar_count
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
                pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                self._in_long = False
                self._parked_long = False
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
                pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                self._in_short = False
                self._parked_short = False
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
                        return Action(ActionType.SHORT, qty, {
                            "entry_reason": "reentry_short",
                            "indicators": indicators,
                        })

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
            self._parked_short = False  # cancel any short park
            qty = pv.cash * 0.9999 / close
            if qty > 0:
                self._in_long = True
                self._entry_price = close
                self._entry_bar = self._bar_count
                return Action(ActionType.BUY, qty, {
                    "entry_reason": "st_flip_bullish",
                    "indicators": indicators,
                })

        # Short entry: ST flipped from bullish to bearish
        if not st_bullish and prev_bull:
            self._parked_long = False  # cancel any long park
            qty = pv.cash * 0.9999 / close
            if qty > 0:
                self._in_short = True
                self._entry_price = close
                self._entry_bar = self._bar_count
                return Action(ActionType.SHORT, qty, {
                    "entry_reason": "st_flip_bearish",
                    "indicators": indicators,
                })

        return Action(ActionType.HOLD, details={"reason": "no_signal", "indicators": indicators})
