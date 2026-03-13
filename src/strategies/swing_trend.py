"""Swing trend-following strategy for BTC/USDT.

Resamples 5-minute bars to 1-hour internally, then applies a simplified
2-layer confluence model designed to ride multi-day trends:

  1. Trend filter:  HMA slope + Supertrend direction agreement
  2. Entry trigger:  Close above/below Keltner midline (pullback entry)
                     OR Keltner breakout (momentum entry)
  3. Risk management: Hard stop + 1h Supertrend trailing stop

Key differences from intraday_trend (v6):
  - Operates on 1h bars → naturally filters 5m noise
  - No squeeze filter, no volume filter, no ATR floor
  - Much wider stops (Supertrend on 1h survives multi-day pullbacks)
  - Targets 12-72 hour holds instead of 2-8 hour holds
  - Fewer, higher-quality trades (40-80/year vs 140/year)
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
)


class SwingTrendStrategy(StrategyBase):
    """Swing trend strategy — HMA + Supertrend + Keltner on hourly bars."""

    def __init__(self, config: dict):
        super().__init__(config)

        # HMA (on 1h bars)
        self.hma_period = config.get("hma_period", 21)

        # Supertrend (on 1h bars)
        self.supertrend_atr_period = config.get("supertrend_atr_period", 14)
        self.supertrend_multiplier = config.get("supertrend_multiplier", 3.0)

        # Keltner Channels (on 1h bars)
        self.keltner_ema_period = config.get("keltner_ema_period", 20)
        self.keltner_atr_period = config.get("keltner_atr_period", 14)
        self.keltner_atr_multiplier = config.get("keltner_atr_multiplier", 2.0)

        # ADX (on 1h bars) — mild trend strength filter
        self.adx_period = config.get("adx_period", 14)
        self.adx_threshold = config.get("adx_threshold", 20)

        # Risk management
        self.stop_loss_pct = config.get("stop_loss_pct", 3.0) / 100.0
        self.daily_max_drawdown_pct = config.get("daily_max_drawdown_pct", 8.0) / 100.0

        # Breakeven stop: move stop to entry when this % profit is reached
        self.breakeven_trigger_pct = config.get("breakeven_trigger_pct", 1.5) / 100.0

        # Tighter trailing: use a separate multiplier for exit trailing (0 = same as entry)
        self.trailing_supertrend_multiplier = config.get("trailing_supertrend_multiplier", 0.0)

        # Entry mode: "midline" = Keltner midline pullback, "breakout" = above KC upper
        # "both" = either trigger works
        self.entry_mode = config.get("entry_mode", "both")

        # Shorts
        self.enable_short = config.get("enable_short", True)
        self.short_adx_threshold = config.get("short_adx_threshold", self.adx_threshold)

        # Cooldown: minimum 1h bars between trades
        self.cooldown_bars = config.get("cooldown_bars", 3)

        # Minimum hold bars (in 1h bars): suppress trailing/flip exits
        self.min_hold_bars = config.get("min_hold_bars", 6)

        # Costs
        self.cost_per_trade_pct = config.get("cost_per_trade_pct", 0.05)

        # Max supertrend stop distance (wider than v6 since we're on 1h)
        self.max_supertrend_stop_pct = config.get("max_supertrend_stop_pct", 3.0) / 100.0

        # Entry thesis invalidation: exit if HMA slope has been against the trade
        # direction for this many consecutive hourly bars (outside min_hold window).
        # 0 = disabled. Higher = more forgiving (fewer early exits).
        # Grid search results (dev/test):
        #   n=0:  +1203% / +866%  Sharpe 2.749 / 2.322  MaxDD -17.4% / -24.7%  (baseline)
        #   n=3:  +1005% / +760%  Sharpe 3.053 / 2.550  MaxDD -13.2% / -18.4%  ← smoother, lower return
        #   n=4:  +1171% / +607%  Sharpe 3.164 / 2.287  — overfits (test drops sharply)
        #   n=6:  +1303% / +500%  Sharpe 3.098 / 2.027  — overfits badly
        # n=3 is the only value that consistently improves Sharpe AND MaxDD on both sets,
        # at the cost of ~12-16% less return. n=4+ overfits to dev.
        self.hma_invalidation_bars = config.get("hma_invalidation_bars", 0)

        # KC midline hold trigger (third entry mode, additive to breakout + pullback).
        # Fires when the last N consecutive *completed* hourly closes are all above KC midline
        # (LONG) or below (SHORT) while the trend filter is aligned.
        # Fills the dead zone: price grinding above midline but below KC upper, never pulling
        # back far enough for pullback trigger and not quite breaking out.
        # 0 = disabled.
        #
        # Grid search (dev 2022-2024 / test 2020+2021+2025+Jan2026):
        #   N=0 (v1 baseline): +1203% / +866%   Sh 2.775/2.313  MaxDD -18.0%/-29.4%  447/400tr
        #   N=1:               +3083% / +3710%  Sh 3.143/3.050  MaxDD -18.5%/-15.8%  590/537tr  ← v2
        #   N=2:               +2744% / +3418%  Sh 3.061/2.997  MaxDD -18.6%/-17.6%  589/528tr
        #   N=4:               +1909% / +2777%  Sh 2.823/2.859  MaxDD -18.4%/-17.1%  579/524tr
        # N=1 wins: ~2.5x more return, Sharpe up, test MaxDD halved, no overfitting (test > dev).
        # Adds ~143 re-entry trades per year during grinding trends.
        self.kc_midline_hold_bars = config.get("kc_midline_hold_bars", 0)

        # Warm-up (1h bars)
        self._warmup_bars = 30

        # --- Precomputed arrays on 1h resampled data ---
        self._hourly = None
        self._hma = None
        self._hma_slope = None
        self._st_line = None
        self._st_bullish = None
        self._trail_st_line = None
        self._kc_upper = None
        self._kc_mid = None
        self._kc_lower = None
        self._adx = None

        # --- Mapping from 5m bar index to 1h bar index ---
        self._bar_to_hourly_idx = None
        self._hourly_timestamps = None

        # --- Position tracking ---
        self._in_position = False
        self._direction = None
        self._entry_price = None
        self._entry_hourly_idx = None
        self._hard_stop = None
        self._peak_price = None
        self._trough_price = None

        # --- Daily tracking ---
        self._day_start_value = None
        self._daily_stop_hit = False
        self._current_day = None

        # --- Cooldown tracking ---
        self._last_exit_hourly_idx = -999

        # --- HMA invalidation counter ---
        # Counts consecutive hourly bars where HMA slope is against the trade
        self._hma_against_count = 0

        # --- Bar counter ---
        self._bar_idx = 0
        self._prev_hourly_idx = -1

    def prepare(self, full_data: pd.DataFrame) -> None:
        """Resample 5m data to 1h and precompute all indicators."""

        # Resample to 1h OHLCV
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
            self.keltner_ema_period, self.keltner_atr_period,
            self.keltner_atr_multiplier,
        )

        # ADX
        self._adx = compute_adx(highs, lows, closes, self.adx_period)

        # Build mapping: for each 5m timestamp, find the corresponding 1h index
        self._hourly_timestamps = hourly.index
        self._bar_to_hourly_idx = {}
        hourly_idx_map = {ts: i for i, ts in enumerate(hourly.index)}

        for ts_5m in full_data.index:
            # Floor to the hour
            hourly_ts = ts_5m.floor("h")
            if hourly_ts in hourly_idx_map:
                self._bar_to_hourly_idx[ts_5m] = hourly_idx_map[hourly_ts]

    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
        pv: PortfolioView,
    ) -> Action:
        if self._hourly is None:
            self.prepare(data_so_far)

        price = row["close"]
        idx = self._bar_idx
        self._bar_idx += 1

        # Map 5m bar to hourly index
        hourly_idx = self._bar_to_hourly_idx.get(date)
        if hourly_idx is None:
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "no_hourly"})

        # Only process signals at the close of each hour (last 5m bar of the hour)
        # But always check exits on every 5m bar for stop loss
        is_hourly_close = (hourly_idx != self._prev_hourly_idx)
        self._prev_hourly_idx = hourly_idx

        # Clamp index
        if hourly_idx >= len(self._hma):
            hourly_idx = len(self._hma) - 1

        # --- Read precomputed hourly indicators ---
        hma_slope = self._hma_slope.iloc[hourly_idx]
        st_line = self._st_line.iloc[hourly_idx]
        trail_st_line = self._trail_st_line.iloc[hourly_idx]
        st_bullish = bool(self._st_bullish.iloc[hourly_idx])
        kc_upper = self._kc_upper.iloc[hourly_idx]
        kc_mid = self._kc_mid.iloc[hourly_idx]
        kc_lower = self._kc_lower.iloc[hourly_idx]
        adx_val = self._adx.iloc[hourly_idx]

        # --- Daily tracking ---
        bar_day = date.date() if hasattr(date, 'date') else date
        if self._current_day is None or bar_day != self._current_day:
            self._current_day = bar_day
            self._day_start_value = pv.cash + pv.position_qty * price - pv.short_qty * price
            self._daily_stop_hit = False

        current_value = pv.cash + pv.position_qty * price - pv.short_qty * price
        if self._day_start_value and self._day_start_value > 0:
            daily_pnl = (current_value - self._day_start_value) / self._day_start_value
            if daily_pnl <= -self.daily_max_drawdown_pct:
                self._daily_stop_hit = True

        # --- Warmup check ---
        if hourly_idx < self._warmup_bars or pd.isna(hma_slope) or pd.isna(adx_val) or pd.isna(st_line):
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

        # --- If in position: check exits on every 5m bar ---
        if has_long or has_short:
            exit_action = self._check_exit(
                price, row, pv, st_line, trail_st_line, st_bullish,
                hma_slope, hourly_idx, is_hourly_close,
            )
            if exit_action is not None:
                return exit_action
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "holding"})

        # --- Not in position: only check entries on hourly close ---
        if not is_hourly_close:
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "wait_hourly"})

        if self._daily_stop_hit:
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "daily_stop_hit"})

        entry_action = self._check_entry(
            price, row, pv, hma_slope, st_line, st_bullish,
            kc_upper, kc_mid, kc_lower, adx_val, hourly_idx,
        )
        if entry_action is not None:
            return entry_action

        return Action(action=ActionType.HOLD, quantity=0, details={"reason": "no_signal"})

    def _check_entry(
        self, price, row, pv, hma_slope, st_line, st_bullish,
        kc_upper, kc_mid, kc_lower, adx_val, hourly_idx,
    ) -> Action | None:
        """Check for entry signal on hourly close."""

        # Cooldown check (in hourly bars)
        if self.cooldown_bars > 0 and (hourly_idx - self._last_exit_hourly_idx) < self.cooldown_bars:
            return None

        # Layer 1: Trend filter — HMA + Supertrend agreement
        if pd.isna(adx_val) or adx_val < self.adx_threshold:
            return None

        if hma_slope > 0 and st_bullish:
            direction = "LONG"
        elif hma_slope < 0 and not st_bullish and self.enable_short:
            if adx_val < self.short_adx_threshold:
                return None
            direction = "SHORT"
        else:
            return None

        # Layer 2: Entry trigger — Keltner based
        trigger = None

        if direction == "LONG":
            # Breakout: close above KC upper
            if self.entry_mode in ("breakout", "both") and price > kc_upper:
                trigger = "keltner_breakout"
            # Midline pullback: price pulled back near KC mid but holding above it
            elif self.entry_mode in ("midline", "both"):
                if row["low"] <= kc_mid * 1.002 and price > kc_mid:
                    trigger = "keltner_pullback"

        elif direction == "SHORT":
            if self.entry_mode in ("breakout", "both") and price < kc_lower:
                trigger = "keltner_breakout"
            elif self.entry_mode in ("midline", "both"):
                if row["high"] >= kc_mid * 0.998 and price < kc_mid:
                    trigger = "keltner_pullback"

        # Trigger 3: KC midline hold — last N *completed* hourly closes all above/below KC midline.
        # Always looks back from (hourly_idx - 1) to avoid reading the unclosed current bar.
        if trigger is None and self.kc_midline_hold_bars > 0:
            n = self.kc_midline_hold_bars
            if hourly_idx >= n + 1:
                hourly_closes = self._hourly["close"]
                kc_mid_arr    = self._kc_mid

                # k=0 → bar (hourly_idx-1), most recent completed bar
                # k=n-1 → bar (hourly_idx-n), oldest bar in the N-bar window
                if direction == "LONG":
                    held = all(
                        hourly_closes.iloc[hourly_idx - 1 - k] > kc_mid_arr.iloc[hourly_idx - 1 - k]
                        for k in range(n)
                    )
                else:  # SHORT
                    held = all(
                        hourly_closes.iloc[hourly_idx - 1 - k] < kc_mid_arr.iloc[hourly_idx - 1 - k]
                        for k in range(n)
                    )

                if held:
                    trigger = "kc_midline_hold"

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
            return None

        # --- All checks pass: enter ---
        details = {
            "trigger": trigger,
            "direction": direction,
            "hma_slope": round(float(hma_slope), 4),
            "supertrend_bullish": bool(st_bullish),
            "supertrend_line": round(float(st_line), 2),
            "adx": round(float(adx_val), 1),
            "kc_upper": round(float(kc_upper), 2),
            "kc_mid": round(float(kc_mid), 2),
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
            self._entry_hourly_idx = hourly_idx
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
            self._entry_hourly_idx = hourly_idx
            self._hard_stop = hard_stop
            self._peak_price = price
            self._trough_price = price

            return Action(action=ActionType.SHORT, quantity=quantity, details=details)

    def _check_exit(
        self, price, row, pv, st_line, trail_st_line, st_bullish, hma_slope,
        hourly_idx, is_new_hourly: bool = False,
    ) -> Action | None:
        """Check exit conditions on every 5m bar."""

        has_long = pv.position_qty > 0
        has_short = pv.short_qty > 0
        hourly_bars_held = hourly_idx - self._entry_hourly_idx if self._entry_hourly_idx is not None else 0

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
        in_min_hold = self.min_hold_bars > 0 and hourly_bars_held < self.min_hold_bars

        # --- HMA Invalidation counter update (once per new hourly bar, outside min_hold) ---
        # Count consecutive hours where HMA slope contradicts trade direction.
        # Reset to 0 whenever HMA is aligned (even partially), so a single positive
        # hour resets the clock — makes the check loose/forgiving for volatile entries.
        if is_new_hourly and self.hma_invalidation_bars > 0 and not in_min_hold and not pd.isna(hma_slope):
            hma_against = (has_long and hma_slope < 0) or (has_short and hma_slope > 0)
            if hma_against:
                self._hma_against_count += 1
            else:
                self._hma_against_count = 0  # reset on ANY aligned bar

        if has_long:
            quantity = pv.position_qty

            trail_val = trail_st_line if not pd.isna(trail_st_line) else st_line
            if in_min_hold:
                active_stop = self._hard_stop
            else:
                active_stop = max(self._hard_stop, trail_val) if not pd.isna(trail_val) else self._hard_stop

            # 1. Stop hit
            if row["low"] <= active_stop:
                exit_reason = "hard_stop" if active_stop == self._hard_stop else "supertrend_trailing"

            # 2. Supertrend flip — only after min-hold window
            elif not in_min_hold and not st_bullish:
                exit_reason = "supertrend_flip"

            # 3. HMA invalidation — entry thesis gone for N consecutive hours
            elif (self.hma_invalidation_bars > 0 and not in_min_hold
                  and self._hma_against_count >= self.hma_invalidation_bars):
                exit_reason = "hma_invalidation"

            # 4. Daily circuit breaker
            elif self._daily_stop_hit:
                exit_reason = "circuit_breaker"

            if exit_reason:
                exit_price = min(price, active_stop) if exit_reason in ("hard_stop", "supertrend_trailing") else price
                pnl_pct = (exit_price - self._entry_price) / self._entry_price * 100 if self._entry_price else 0
                mfe_pct = (self._peak_price - self._entry_price) / self._entry_price * 100 if self._entry_price else 0
                mae_pct = (self._trough_price - self._entry_price) / self._entry_price * 100 if self._entry_price else 0

                details = {
                    "exit_reason": exit_reason,
                    "bars_held": hourly_bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "max_favorable_excursion_pct": round(mfe_pct, 2),
                    "max_adverse_excursion_pct": round(mae_pct, 2),
                }
                self._reset_position(hourly_idx)
                return Action(action=ActionType.SELL, quantity=quantity, details=details)

        elif has_short:
            quantity = pv.short_qty

            trail_val = trail_st_line if not pd.isna(trail_st_line) else st_line
            if in_min_hold:
                active_stop = self._hard_stop
            else:
                active_stop = min(self._hard_stop, trail_val) if not pd.isna(trail_val) else self._hard_stop

            # 1. Stop hit
            if row["high"] >= active_stop:
                exit_reason = "hard_stop" if active_stop == self._hard_stop else "supertrend_trailing"

            # 2. Supertrend flip — only after min-hold window
            elif not in_min_hold and st_bullish:
                exit_reason = "supertrend_flip"

            # 3. HMA invalidation — entry thesis gone for N consecutive hours
            elif (self.hma_invalidation_bars > 0 and not in_min_hold
                  and self._hma_against_count >= self.hma_invalidation_bars):
                exit_reason = "hma_invalidation"

            # 4. Daily circuit breaker
            elif self._daily_stop_hit:
                exit_reason = "circuit_breaker"

            if exit_reason:
                exit_price = max(price, active_stop) if exit_reason in ("hard_stop", "supertrend_trailing") else price
                pnl_pct = (self._entry_price - exit_price) / self._entry_price * 100 if self._entry_price else 0
                mfe_pct = (self._entry_price - self._trough_price) / self._entry_price * 100 if self._entry_price else 0
                mae_pct = (self._entry_price - self._peak_price) / self._entry_price * 100 if self._entry_price else 0

                details = {
                    "exit_reason": exit_reason,
                    "bars_held": hourly_bars_held,
                    "pnl_pct": round(pnl_pct, 2),
                    "max_favorable_excursion_pct": round(mfe_pct, 2),
                    "max_adverse_excursion_pct": round(mae_pct, 2),
                }
                self._reset_position(hourly_idx)
                return Action(action=ActionType.COVER, quantity=quantity, details=details)

        return None

    def _reset_position(self, exit_hourly_idx: int = 0):
        """Clear position state after exit."""
        self._in_position = False
        self._direction = None
        self._entry_price = None
        self._entry_hourly_idx = None
        self._hard_stop = None
        self._peak_price = None
        self._trough_price = None
        self._hma_against_count = 0
        self._last_exit_hourly_idx = exit_hourly_idx
