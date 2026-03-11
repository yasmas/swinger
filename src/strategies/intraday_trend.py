"""Intraday trend-following strategy for BTC/USDT on 5-minute bars.

Uses a 4-layer confluence model:
  1. Regime filter (TTM Squeeze + ADX)
  2. Directional bias (HMA + Supertrend agreement)
  3. Entry trigger (Keltner breakout / midline bounce + volume)
  4. Risk management (2% hard stop + Supertrend trailing stop)
"""

import math

import numpy as np
import pandas as pd

from .base import StrategyBase, Action, ActionType, PortfolioView
from .macd_rsi_advanced import compute_adx, compute_atr, compute_ema
from .intraday_indicators import (
    compute_hma,
    compute_supertrend,
    compute_keltner,
    compute_bollinger,
    compute_squeeze,
    compute_vwap_daily,
)


class IntradayTrendStrategy(StrategyBase):
    """Intraday trend strategy — HMA + Supertrend + Keltner confluence."""

    def __init__(self, config: dict):
        super().__init__(config)

        # HMA
        self.hma_period = config.get("hma_period", 21)

        # Supertrend
        self.supertrend_atr_period = config.get("supertrend_atr_period", 10)
        self.supertrend_multiplier = config.get("supertrend_multiplier", 3.0)

        # Keltner Channels
        self.keltner_ema_period = config.get("keltner_ema_period", 15)
        self.keltner_atr_period = config.get("keltner_atr_period", 10)
        self.keltner_atr_multiplier = config.get("keltner_atr_multiplier", 2.0)

        # Bollinger Bands (squeeze detection)
        self.bb_period = config.get("bb_period", 20)
        self.bb_stddev = config.get("bb_stddev", 2.0)

        # ADX
        self.adx_period = config.get("adx_period", 14)
        self.adx_threshold = config.get("adx_threshold", 25)

        # Volume
        self.volume_avg_period = config.get("volume_avg_period", 20)
        self.volume_confirm_multiplier = config.get("volume_confirm_multiplier", 1.5)

        # Risk management
        self.stop_loss_pct = config.get("stop_loss_pct", 2.0) / 100.0
        self.daily_max_drawdown_pct = config.get("daily_max_drawdown_pct", 6.0) / 100.0
        self.max_supertrend_stop_pct = config.get("max_supertrend_stop_pct", 2.0) / 100.0

        # Breakeven stop: move stop to entry when this % profit is reached (0 = disabled)
        self.breakeven_trigger_pct = config.get("breakeven_trigger_pct", 0.0) / 100.0

        # Tighter trailing: use a separate multiplier for exit trailing (0 = use same as entry)
        self.trailing_supertrend_multiplier = config.get("trailing_supertrend_multiplier", 0.0)

        # Entry
        self.enable_keltner_bounce = config.get("enable_keltner_bounce", True)
        self.min_trend_bars_for_bounce = config.get("min_trend_bars_for_bounce", 6)

        # Costs
        self.cost_per_trade_pct = config.get("cost_per_trade_pct", 0.05)

        # Shorts
        self.enable_short = config.get("enable_short", True)
        self.short_adx_threshold = config.get("short_adx_threshold", self.adx_threshold)

        # Cooldown: minimum bars between trades to reduce overtrading
        self.cooldown_bars = config.get("cooldown_bars", 0)

        # Minimum HMA slope magnitude (filters weak signals)
        self.min_hma_slope_bps = config.get("min_hma_slope_bps", 0.0)

        # Breakout confirmation: require N consecutive bars outside Keltner
        self.breakout_confirm_bars = config.get("breakout_confirm_bars", 1)

        # Volatility floor: skip entries when ATR% is below this threshold (0 = disabled)
        self.min_atr_pct = config.get("min_atr_pct", 0.0) / 100.0

        # Warm-up bars required before trading
        self._warmup_bars = 50

        # --- Precomputed indicator arrays (set in prepare()) ---
        self._hma = None
        self._hma_slope = None
        self._st_line = None
        self._st_bullish = None
        self._kc_upper = None
        self._kc_mid = None
        self._kc_lower = None
        self._squeeze_on = None
        self._adx = None
        self._vwap = None
        self._vol_avg = None
        self._atr_pct = None  # ATR as % of close (for volatility floor)

        # --- Position tracking ---
        self._in_position = False
        self._direction = None  # "LONG" or "SHORT"
        self._entry_price = None
        self._entry_bar_idx = None
        self._hard_stop = None
        self._peak_price = None  # For tracking MFE
        self._trough_price = None  # For tracking MAE

        # --- Daily tracking ---
        self._day_start_value = None
        self._daily_stop_hit = False
        self._current_day = None

        # --- Supertrend direction counter ---
        self._supertrend_direction_bars = 0
        self._prev_st_bullish = None

        # --- Cooldown tracking ---
        self._last_exit_bar_idx = -999

        # --- Breakout confirmation state ---
        self._consec_above_kc = 0  # consecutive bars closing above KC upper
        self._consec_below_kc = 0  # consecutive bars closing below KC lower

        # --- Bar counter for indexing into precomputed arrays ---
        self._bar_idx = 0
        self._dates = None

    def prepare(self, full_data: pd.DataFrame) -> None:
        """Precompute all indicators on the full 5m DataFrame."""
        closes = full_data["close"]
        highs = full_data["high"]
        lows = full_data["low"]
        volumes = full_data["volume"]

        # HMA + slope
        self._hma = compute_hma(closes, self.hma_period)
        self._hma_slope = self._hma.diff()

        # Supertrend (direction filter)
        self._st_line, self._st_bullish = compute_supertrend(
            highs, lows, closes,
            self.supertrend_atr_period, self.supertrend_multiplier,
        )

        # Tighter Supertrend for trailing exits (if configured)
        if self.trailing_supertrend_multiplier > 0:
            self._trail_st_line, _ = compute_supertrend(
                highs, lows, closes,
                self.supertrend_atr_period, self.trailing_supertrend_multiplier,
            )
        else:
            self._trail_st_line = self._st_line

        # Keltner Channels
        self._kc_upper, self._kc_mid, self._kc_lower = compute_keltner(
            highs, lows, closes,
            self.keltner_ema_period, self.keltner_atr_period, self.keltner_atr_multiplier,
        )

        # Bollinger Bands + Squeeze
        bb_upper, bb_mid, bb_lower = compute_bollinger(
            closes, self.bb_period, self.bb_stddev,
        )
        self._squeeze_on = compute_squeeze(bb_upper, bb_lower, self._kc_upper, self._kc_lower)

        # ADX
        self._adx = compute_adx(highs, lows, closes, self.adx_period)

        # VWAP (daily reset)
        self._vwap = compute_vwap_daily(highs, lows, closes, volumes, full_data.index)

        # Volume average
        self._vol_avg = volumes.rolling(window=self.volume_avg_period).mean()

        # ATR% for volatility floor (using same ATR period as Supertrend)
        if self.min_atr_pct > 0:
            atr_raw = compute_atr(highs, lows, closes, self.supertrend_atr_period)
            self._atr_pct = atr_raw / closes
        else:
            self._atr_pct = pd.Series(1.0, index=closes.index)  # always pass

        # Store dates for day tracking
        self._dates = full_data.index

    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
        pv: PortfolioView,
    ) -> Action:
        if self._hma is None:
            self.prepare(data_so_far)

        price = row["close"]
        idx = self._bar_idx
        self._bar_idx += 1

        # Clamp index to array bounds
        if idx >= len(self._hma):
            idx = len(self._hma) - 1

        # --- Read precomputed indicators ---
        hma_slope = self._hma_slope.iloc[idx]
        st_line = self._st_line.iloc[idx]
        trail_st_line = self._trail_st_line.iloc[idx]
        st_bullish = bool(self._st_bullish.iloc[idx])
        kc_upper = self._kc_upper.iloc[idx]
        kc_mid = self._kc_mid.iloc[idx]
        kc_lower = self._kc_lower.iloc[idx]
        squeeze_on = bool(self._squeeze_on.iloc[idx]) if not pd.isna(self._squeeze_on.iloc[idx]) else True
        adx_val = self._adx.iloc[idx]
        vwap_val = self._vwap.iloc[idx]
        vol_avg = self._vol_avg.iloc[idx]
        volume = row["volume"]
        atr_pct = self._atr_pct.iloc[idx]

        # --- Track Supertrend direction duration ---
        if self._prev_st_bullish is not None and st_bullish == self._prev_st_bullish:
            self._supertrend_direction_bars += 1
        else:
            self._supertrend_direction_bars = 1
        self._prev_st_bullish = st_bullish

        # --- Track consecutive bars outside Keltner (breakout confirmation) ---
        if not pd.isna(kc_upper) and price > kc_upper:
            self._consec_above_kc += 1
        else:
            self._consec_above_kc = 0
        if not pd.isna(kc_lower) and price < kc_lower:
            self._consec_below_kc += 1
        else:
            self._consec_below_kc = 0

        # --- Daily tracking ---
        bar_day = date.date() if hasattr(date, 'date') else date
        if self._current_day is None or bar_day != self._current_day:
            self._current_day = bar_day
            self._day_start_value = pv.cash + pv.position_qty * price - pv.short_qty * price
            self._daily_stop_hit = False

        # Check daily circuit breaker
        current_value = pv.cash + pv.position_qty * price - pv.short_qty * price
        if self._day_start_value and self._day_start_value > 0:
            daily_pnl = (current_value - self._day_start_value) / self._day_start_value
            if daily_pnl <= -self.daily_max_drawdown_pct:
                self._daily_stop_hit = True

        # --- Warmup check ---
        if idx < self._warmup_bars or pd.isna(hma_slope) or pd.isna(adx_val) or pd.isna(st_line):
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "warmup"})

        has_long = pv.position_qty > 0
        has_short = pv.short_qty > 0

        # --- Force liquidate on last bar ---
        if is_last_bar:
            if has_long:
                return Action(
                    action=ActionType.SELL, quantity=pv.position_qty,
                    details={"reason": "last_bar", "exit_reason": "last_bar"},
                )
            if has_short:
                return Action(
                    action=ActionType.COVER, quantity=pv.short_qty,
                    details={"reason": "last_bar", "exit_reason": "last_bar"},
                )

        # --- If in position: check exits ---
        if has_long or has_short:
            exit_action = self._check_exit(
                price, row, pv, st_line, trail_st_line, st_bullish, hma_slope, idx,
            )
            if exit_action is not None:
                return exit_action
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "holding"})

        # --- Not in position: check entry ---
        # Daily circuit breaker blocks new entries
        if self._daily_stop_hit:
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "daily_stop_hit"})

        entry_action = self._check_entry(
            price, row, pv, hma_slope, st_line, st_bullish,
            kc_upper, kc_mid, kc_lower, squeeze_on, adx_val,
            vwap_val, volume, vol_avg, atr_pct, idx,
        )
        if entry_action is not None:
            return entry_action

        return Action(action=ActionType.HOLD, quantity=0, details={"reason": "no_signal"})

    def _check_entry(
        self, price, row, pv, hma_slope, st_line, st_bullish,
        kc_upper, kc_mid, kc_lower, squeeze_on, adx_val,
        vwap_val, volume, vol_avg, atr_pct, idx,
    ) -> Action | None:
        """Check all 4 layers for entry signal."""

        # Cooldown check
        if self.cooldown_bars > 0 and (idx - self._last_exit_bar_idx) < self.cooldown_bars:
            return None

        # Volatility floor: skip low-volatility environments
        if self.min_atr_pct > 0 and (pd.isna(atr_pct) or atr_pct < self.min_atr_pct):
            return None

        # Layer 1: Regime filter
        if squeeze_on:
            return None
        if pd.isna(adx_val) or adx_val < self.adx_threshold:
            return None

        # Layer 2: Directional bias
        # Check minimum HMA slope magnitude
        if self.min_hma_slope_bps > 0 and price > 0:
            slope_bps = abs(hma_slope) / price * 10000
            if slope_bps < self.min_hma_slope_bps:
                return None

        if hma_slope > 0 and st_bullish:
            direction = "LONG"
        elif hma_slope < 0 and not st_bullish and self.enable_short:
            # Apply higher ADX threshold for shorts if configured
            if adx_val < self.short_adx_threshold:
                return None
            direction = "SHORT"
        else:
            return None

        # Volume confirmation
        vol_confirm = (not pd.isna(vol_avg)) and vol_avg > 0 and volume > self.volume_confirm_multiplier * vol_avg

        # Layer 3: Entry trigger
        trigger = None

        # Trigger A: Keltner Breakout (with confirmation)
        if direction == "LONG" and price > kc_upper and vol_confirm:
            if self._consec_above_kc >= self.breakout_confirm_bars:
                trigger = "keltner_breakout"
        elif direction == "SHORT" and price < kc_lower and vol_confirm:
            if self._consec_below_kc >= self.breakout_confirm_bars:
                trigger = "keltner_breakout"

        # Trigger B: Keltner Midline Bounce
        if trigger is None and self.enable_keltner_bounce and vol_confirm:
            if self._supertrend_direction_bars >= self.min_trend_bars_for_bounce:
                low = row["low"]
                high = row["high"]
                if direction == "LONG" and low <= kc_mid and price > kc_mid:
                    trigger = "keltner_bounce"
                elif direction == "SHORT" and high >= kc_mid and price < kc_mid:
                    trigger = "keltner_bounce"

        if trigger is None:
            return None

        # Entry filter: Supertrend stop distance
        if direction == "LONG":
            stop_distance_pct = (price - st_line) / price if price > 0 else 0
        else:
            stop_distance_pct = (st_line - price) / price if price > 0 else 0

        if stop_distance_pct > self.max_supertrend_stop_pct:
            return None
        if stop_distance_pct < 0:
            return None  # Supertrend disagrees with direction

        # --- All checks pass: enter ---
        vwap_aligned = False
        if not pd.isna(vwap_val):
            vwap_aligned = (direction == "LONG" and price > vwap_val) or \
                           (direction == "SHORT" and price < vwap_val)

        details = {
            "trigger": trigger,
            "direction": direction,
            "hma_slope": round(float(hma_slope), 4),
            "supertrend_bullish": bool(st_bullish),
            "supertrend_line": round(float(st_line), 2),
            "adx": round(float(adx_val), 1),
            "squeeze_on": False,
            "vwap": round(float(vwap_val), 2) if not pd.isna(vwap_val) else None,
            "vwap_aligned": bool(vwap_aligned),
            "kc_upper": round(float(kc_upper), 2),
            "kc_mid": round(float(kc_mid), 2),
            "volume_ratio": round(float(volume / vol_avg), 2) if vol_avg and vol_avg > 0 else 0,
            "stop_distance_pct": round(float(stop_distance_pct * 100), 2),
        }

        if direction == "LONG":
            quantity = math.floor(pv.cash / price * 1e8) / 1e8
            if quantity <= 0:
                return None
            hard_stop = price * (1 - self.stop_loss_pct)
            details["hard_stop"] = round(hard_stop, 2)
            details["entry_price"] = round(price, 2)

            self._in_position = True
            self._direction = "LONG"
            self._entry_price = price
            self._entry_bar_idx = idx
            self._hard_stop = hard_stop
            self._peak_price = price
            self._trough_price = price

            return Action(action=ActionType.BUY, quantity=quantity, details=details)
        else:
            quantity = math.floor(pv.cash / price * 1e8) / 1e8
            if quantity <= 0:
                return None
            hard_stop = price * (1 + self.stop_loss_pct)
            details["hard_stop"] = round(hard_stop, 2)
            details["entry_price"] = round(price, 2)

            self._in_position = True
            self._direction = "SHORT"
            self._entry_price = price
            self._entry_bar_idx = idx
            self._hard_stop = hard_stop
            self._peak_price = price
            self._trough_price = price

            return Action(action=ActionType.SHORT, quantity=quantity, details=details)

    def _check_exit(
        self, price, row, pv, st_line, trail_st_line, st_bullish, hma_slope, idx,
    ) -> Action | None:
        """Check exit conditions for current position."""

        has_long = pv.position_qty > 0
        has_short = pv.short_qty > 0
        bars_held = idx - self._entry_bar_idx if self._entry_bar_idx is not None else 0

        # Track MFE/MAE
        if has_long:
            self._peak_price = max(self._peak_price or price, price)
            self._trough_price = min(self._trough_price or price, price)
        elif has_short:
            self._peak_price = max(self._peak_price or price, price)
            self._trough_price = min(self._trough_price or price, price)

        # Breakeven stop adjustment
        if self.breakeven_trigger_pct > 0 and self._entry_price:
            if has_long:
                unrealized_pct = (price - self._entry_price) / self._entry_price
                if unrealized_pct >= self.breakeven_trigger_pct:
                    self._hard_stop = max(self._hard_stop, self._entry_price)
            elif has_short:
                unrealized_pct = (self._entry_price - price) / self._entry_price
                if unrealized_pct >= self.breakeven_trigger_pct:
                    self._hard_stop = min(self._hard_stop, self._entry_price)

        exit_reason = None

        if has_long:
            quantity = pv.position_qty

            # Use tighter trailing Supertrend for stop, but main ST for direction
            trail_val = trail_st_line if not pd.isna(trail_st_line) else st_line
            active_stop = max(self._hard_stop, trail_val) if not pd.isna(trail_val) else self._hard_stop

            # 1. Stop hit
            if row["low"] <= active_stop:
                exit_reason = "hard_stop" if active_stop == self._hard_stop else "supertrend_trailing"

            # 2. Supertrend flip
            elif not st_bullish:
                exit_reason = "supertrend_flip"

            # 3. Daily circuit breaker
            elif self._daily_stop_hit:
                exit_reason = "circuit_breaker"

            if exit_reason:
                exit_price = min(price, active_stop) if exit_reason in ("hard_stop", "supertrend_trailing") else price
                pnl_pct = (exit_price - self._entry_price) / self._entry_price * 100 if self._entry_price else 0
                mfe_pct = (self._peak_price - self._entry_price) / self._entry_price * 100 if self._entry_price else 0
                mae_pct = (self._trough_price - self._entry_price) / self._entry_price * 100 if self._entry_price else 0

                details = {
                    "exit_reason": exit_reason,
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "max_favorable_excursion_pct": round(mfe_pct, 2),
                    "max_adverse_excursion_pct": round(mae_pct, 2),
                }
                self._reset_position(idx)
                return Action(action=ActionType.SELL, quantity=quantity, details=details)

        elif has_short:
            quantity = pv.short_qty

            # Active stop = tighter of hard stop and trailing supertrend (for short: take lower)
            trail_val = trail_st_line if not pd.isna(trail_st_line) else st_line
            active_stop = min(self._hard_stop, trail_val) if not pd.isna(trail_val) else self._hard_stop

            # 1. Stop hit
            if row["high"] >= active_stop:
                exit_reason = "hard_stop" if active_stop == self._hard_stop else "supertrend_trailing"

            # 2. Supertrend flip
            elif st_bullish:
                exit_reason = "supertrend_flip"

            # 3. Daily circuit breaker
            elif self._daily_stop_hit:
                exit_reason = "circuit_breaker"

            if exit_reason:
                exit_price = max(price, active_stop) if exit_reason in ("hard_stop", "supertrend_trailing") else price
                pnl_pct = (self._entry_price - exit_price) / self._entry_price * 100 if self._entry_price else 0
                mfe_pct = (self._entry_price - self._trough_price) / self._entry_price * 100 if self._entry_price else 0
                mae_pct = (self._entry_price - self._peak_price) / self._entry_price * 100 if self._entry_price else 0

                details = {
                    "exit_reason": exit_reason,
                    "bars_held": bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "max_favorable_excursion_pct": round(mfe_pct, 2),
                    "max_adverse_excursion_pct": round(mae_pct, 2),
                }
                self._reset_position(idx)
                return Action(action=ActionType.COVER, quantity=quantity, details=details)

        return None

    def _reset_position(self, exit_bar_idx: int = 0):
        """Clear position state after exit."""
        self._in_position = False
        self._direction = None
        self._entry_price = None
        self._entry_bar_idx = None
        self._hard_stop = None
        self._peak_price = None
        self._trough_price = None
        self._last_exit_bar_idx = exit_bar_idx
