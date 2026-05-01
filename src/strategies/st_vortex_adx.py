"""Supertrend-led strategy filtered by Vortex EMA and ADX."""

from __future__ import annotations

import math
from datetime import time

import pandas as pd

from .base import Action, ActionType, PortfolioView, StrategyBase
from .intraday_indicators import compute_realised_vol, compute_supertrend, compute_vortex
from .macd_rsi_advanced import compute_adx


class SupertrendVortexADXStrategy(StrategyBase):
    """Use Supertrend as the direction state, with Vortex/ADX flip confirmation."""

    display_name = "Supertrend Vortex ADX"

    def __init__(self, config: dict):
        super().__init__(config)
        self.symbol = config.get("symbol", "QQQ")
        self.resample_interval = str(config.get("resample_interval", "30min"))

        self.st_atr_period = int(config.get("supertrend_atr_period", 12))
        self.st_multiplier = float(config.get("supertrend_multiplier", 1.5))

        self.vortex_period = int(config.get("vortex_period", 21))
        self.vortex_ema_period = int(config.get("vortex_ema_period", 3))
        self.vortex_predict = bool(config.get("vortex_predict", True))
        self.vortex_margin = float(config.get("vortex_margin", 0.0))

        self.adx_period = int(config.get("adx_period", 12))
        self.adx_floor = float(config.get("adx_floor", 20.0))
        self.adx_near_floor = float(config.get("adx_near_floor", 18.0))
        self.adx_slope_min = float(config.get("adx_slope_min", 0.6))

        self.enable_short = bool(config.get("enable_short", False))
        self.short_entry_cooldown_signal_bars = int(
            config.get("short_entry_cooldown_signal_bars", 0)
        )
        self.rth_only_flips = bool(config.get("rth_only_flips", True))
        self.exit_outside_rth_on_core_flip = bool(
            config.get("exit_outside_rth_on_core_flip", False)
        )
        self.equity_session_timezone = str(
            config.get("equity_session_timezone", "America/New_York")
        )
        self.equity_regular_session_start = self._parse_session_time(
            config.get("equity_regular_session_start", "09:30")
        )
        self.equity_regular_session_end = self._parse_session_time(
            config.get("equity_regular_session_end", "16:00")
        )

        self.vol_ratio_enabled = bool(config.get("vol_ratio_enabled", False))
        self.long_vol_ratio_enabled = bool(
            config.get("long_vol_ratio_enabled", self.vol_ratio_enabled)
        )
        self.short_vol_ratio_enabled = bool(
            config.get("short_vol_ratio_enabled", self.vol_ratio_enabled)
        )
        self.vol_ratio_short_period = int(config.get("vol_ratio_short_period", 4))
        self.vol_ratio_long_period = int(config.get("vol_ratio_long_period", 48))
        self.vol_ratio_min = float(config.get("vol_ratio_min", 0.8))
        self.long_vol_ratio_min = float(
            config.get("long_vol_ratio_min", self.vol_ratio_min)
        )
        self.short_vol_ratio_min = float(
            config.get("short_vol_ratio_min", self.vol_ratio_min)
        )

        self.short_context_interval = str(config.get("short_context_interval", "4h"))
        self.short_require_context_bearish = bool(
            config.get("short_require_context_bearish", False)
        )
        self.short_context_st_atr_period = int(
            config.get("short_context_supertrend_atr_period", self.st_atr_period)
        )
        self.short_context_st_multiplier = float(
            config.get("short_context_supertrend_multiplier", self.st_multiplier)
        )
        self.short_context_adx_period = int(
            config.get("short_context_adx_period", self.adx_period)
        )
        self.short_context_adx_floor = float(
            config.get("short_context_adx_floor", 20.0)
        )

        self.reject_late_contradiction_bars = int(
            config.get("reject_late_contradiction_bars", 8)
        )
        self.entry_cooldown_signal_bars = int(
            config.get("entry_cooldown_signal_bars", 0)
        )

        self._resample_freq = pd.tseries.frequencies.to_offset(self.resample_interval)
        warmup_bars = max(
            self.st_atr_period * 2,
            self.vortex_period + self.vortex_ema_period + 3,
            self.adx_period + 3,
            (
                self.vol_ratio_long_period + self.vol_ratio_short_period + 3
                if self.vol_ratio_enabled
                else 0
            ),
        )
        interval_hours = pd.Timedelta(self.resample_interval).total_seconds() / 3600.0
        self.min_warmup_hours = max(1, math.ceil(warmup_bars * interval_hours))

        self._reset_runtime_state()
        self._resampled: pd.DataFrame | None = None
        self._indicators: pd.DataFrame | None = None
        self._5m_to_signal: dict[pd.Timestamp, int] = {}
        self._context: pd.DataFrame | None = None
        self._5m_to_context: dict[pd.Timestamp, int] = {}

    @staticmethod
    def _parse_session_time(value) -> time:
        if isinstance(value, time):
            return value
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute))

    def _reset_runtime_state(self) -> None:
        self._prev_signal_idx = -1
        self._prev_st_bullish: bool | None = None
        self._contradiction_direction: str | None = None
        self._contradiction_signal_idx = -1
        self._last_exit_signal_idx = -10_000

    def reset_position(self) -> None:
        self._reset_runtime_state()

    def export_state(self) -> dict:
        return {
            "prev_signal_idx": self._prev_signal_idx,
            "prev_st_bullish": self._prev_st_bullish,
            "contradiction_direction": self._contradiction_direction,
            "contradiction_signal_idx": self._contradiction_signal_idx,
            "last_exit_signal_idx": self._last_exit_signal_idx,
        }

    def import_state(self, state: dict) -> None:
        if not state:
            return
        self._prev_signal_idx = int(state.get("prev_signal_idx", -1))
        self._prev_st_bullish = state.get("prev_st_bullish")
        self._contradiction_direction = state.get("contradiction_direction")
        self._contradiction_signal_idx = int(state.get("contradiction_signal_idx", -1))
        self._last_exit_signal_idx = int(state.get("last_exit_signal_idx", -10_000))

    def prepare(self, full_data: pd.DataFrame) -> None:
        resampled = (
            full_data.resample(self.resample_interval)
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )

        if len(resampled) > 0 and len(full_data) > 0:
            last_5m_ts = full_data.index[-1]
            last_resample_start = last_5m_ts.floor(self._resample_freq)
            last_resample_end = (
                last_resample_start + self._resample_freq - pd.Timedelta(minutes=5)
            )
            if last_5m_ts < last_resample_end:
                resampled = resampled.iloc[:-1]

        self._resampled = resampled
        if resampled.empty:
            self._indicators = pd.DataFrame(index=resampled.index)
            self._5m_to_signal = {}
            self._context = pd.DataFrame()
            self._5m_to_context = {}
            return

        highs = resampled["high"]
        lows = resampled["low"]
        closes = resampled["close"]

        st_line, st_bull = compute_supertrend(
            highs, lows, closes, self.st_atr_period, self.st_multiplier
        )
        vi_plus, vi_minus = compute_vortex(highs, lows, closes, self.vortex_period)
        vi_plus_ema = vi_plus.ewm(span=self.vortex_ema_period, adjust=False).mean()
        vi_minus_ema = vi_minus.ewm(span=self.vortex_ema_period, adjust=False).mean()
        vortex_diff = vi_plus_ema - vi_minus_ema
        adx = compute_adx(highs, lows, closes, self.adx_period)
        vol_short = compute_realised_vol(
            closes, self.vol_ratio_short_period, annualize=False
        )
        vol_long = vol_short.shift(1).rolling(
            self.vol_ratio_long_period,
            min_periods=self.vol_ratio_long_period,
        ).mean()

        self._indicators = pd.DataFrame(
            {
                "st_line": st_line,
                "st_bull": st_bull.astype(bool),
                "vi_plus": vi_plus,
                "vi_minus": vi_minus,
                "vi_plus_ema": vi_plus_ema,
                "vi_minus_ema": vi_minus_ema,
                "vortex_diff": vortex_diff,
                "vortex_diff_slope": vortex_diff.diff(),
                "adx": adx,
                "adx_slope": adx.diff(),
                "vol_ratio": vol_short / vol_long.replace(0.0, pd.NA),
            },
            index=resampled.index,
        )

        resampled_ts = resampled.index
        mapping: dict[pd.Timestamp, int] = {}
        for ts_5m in full_data.index:
            target = ts_5m + pd.Timedelta(minutes=5) - self._resample_freq
            idx = resampled_ts.get_indexer([target], method="ffill")[0]
            if idx >= 0:
                mapping[ts_5m] = int(idx)
        self._5m_to_signal = mapping

        context = (
            full_data.resample(self.short_context_interval)
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna()
        )
        if not context.empty and len(full_data) > 0:
            context_freq = pd.tseries.frequencies.to_offset(self.short_context_interval)
            last_5m_ts = full_data.index[-1]
            last_context_start = last_5m_ts.floor(self.short_context_interval)
            last_context_end = last_context_start + context_freq - pd.Timedelta(minutes=5)
            if last_5m_ts < last_context_end:
                context = context.iloc[:-1]
        if context.empty:
            self._context = pd.DataFrame(index=context.index)
            self._5m_to_context = {}
        else:
            context_st, context_bull = compute_supertrend(
                context["high"],
                context["low"],
                context["close"],
                self.short_context_st_atr_period,
                self.short_context_st_multiplier,
            )
            context_adx = compute_adx(
                context["high"],
                context["low"],
                context["close"],
                self.short_context_adx_period,
            )
            self._context = pd.DataFrame(
                {
                    "st_line": context_st,
                    "st_bull": context_bull.astype(bool),
                    "adx": context_adx,
                },
                index=context.index,
            )
            context_ts = context.index
            context_mapping: dict[pd.Timestamp, int] = {}
            context_freq = pd.tseries.frequencies.to_offset(self.short_context_interval)
            for ts_5m in full_data.index:
                target = ts_5m + pd.Timedelta(minutes=5) - context_freq
                idx = context_ts.get_indexer([target], method="ffill")[0]
                if idx >= 0:
                    context_mapping[ts_5m] = int(idx)
            self._5m_to_context = context_mapping

    def _entry_quantity(self, pv: PortfolioView, price: float) -> float:
        return math.floor(pv.cash / price * 1e8) / 1e8 if price > 0 else 0.0

    def _is_rth(self, date: pd.Timestamp) -> bool:
        if not self.rth_only_flips:
            return True
        stamp = pd.Timestamp(date)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        local = stamp.tz_convert(self.equity_session_timezone)
        current = local.time()
        return (
            self.equity_regular_session_start
            <= current
            <= self.equity_regular_session_end
        )

    def _vortex_allows(self, signal_idx: int, direction: str) -> bool:
        row = self._indicators.iloc[signal_idx]
        diff = row["vortex_diff"]
        slope = row["vortex_diff_slope"]
        if pd.isna(diff):
            return False
        if direction == "long" and float(diff) >= self.vortex_margin:
            return True
        if direction == "short" and float(diff) <= -self.vortex_margin:
            return True
        if not self.vortex_predict or pd.isna(slope):
            return False
        projected = float(diff) + float(slope)
        if direction == "long":
            return bool(projected >= self.vortex_margin and float(slope) > 0)
        return bool(projected <= -self.vortex_margin and float(slope) < 0)

    def _adx_allows(self, signal_idx: int) -> bool:
        row = self._indicators.iloc[signal_idx]
        adx = row["adx"]
        slope = row["adx_slope"]
        if pd.isna(adx):
            return False
        if float(adx) >= self.adx_floor:
            return True
        return bool(
            not pd.isna(slope)
            and float(adx) >= self.adx_near_floor
            and float(slope) >= self.adx_slope_min
        )

    def _vol_allows(self, signal_idx: int, direction: str) -> bool:
        enabled = (
            self.long_vol_ratio_enabled
            if direction == "long"
            else self.short_vol_ratio_enabled
        )
        if not enabled:
            return True
        ratio = self._indicators["vol_ratio"].iloc[signal_idx]
        minimum = (
            self.long_vol_ratio_min
            if direction == "long"
            else self.short_vol_ratio_min
        )
        return bool(not pd.isna(ratio) and float(ratio) >= minimum)

    def _short_context_allows(self, date: pd.Timestamp) -> bool:
        if not self.short_require_context_bearish:
            return True
        if self._context is None or self._context.empty:
            return False
        context_idx = self._5m_to_context.get(date)
        if context_idx is None:
            return False
        row = self._context.iloc[int(context_idx)]
        adx = row["adx"]
        return bool(
            not bool(row["st_bull"])
            and not pd.isna(adx)
            and float(adx) >= self.short_context_adx_floor
        )

    def _filters_allow(self, signal_idx: int, direction: str, date: pd.Timestamp) -> bool:
        return (
            self._core_filters_allow(signal_idx, direction)
            and self._vol_allows(signal_idx, direction)
            and (direction != "short" or self._short_context_allows(date))
        )

    def _core_filters_allow(self, signal_idx: int, direction: str) -> bool:
        return self._vortex_allows(signal_idx, direction) and self._adx_allows(
            signal_idx
        )

    def _indicator_details(self, signal_idx: int, reason: str) -> dict:
        row = self._indicators.iloc[signal_idx]
        return {
            "reason": reason,
            "signal_time": str(self._resampled.index[signal_idx]),
            "st_bull": bool(row["st_bull"]),
            "st_line": None if pd.isna(row["st_line"]) else round(float(row["st_line"]), 4),
            "vi_plus": None if pd.isna(row["vi_plus"]) else round(float(row["vi_plus"]), 6),
            "vi_minus": None if pd.isna(row["vi_minus"]) else round(float(row["vi_minus"]), 6),
            "vi_plus_ema": None
            if pd.isna(row["vi_plus_ema"])
            else round(float(row["vi_plus_ema"]), 6),
            "vi_minus_ema": None
            if pd.isna(row["vi_minus_ema"])
            else round(float(row["vi_minus_ema"]), 6),
            "vortex_diff": None
            if pd.isna(row["vortex_diff"])
            else round(float(row["vortex_diff"]), 6),
            "adx": None if pd.isna(row["adx"]) else round(float(row["adx"]), 4),
            "adx_slope": None
            if pd.isna(row["adx_slope"])
            else round(float(row["adx_slope"]), 4),
            "vol_ratio": None
            if pd.isna(row["vol_ratio"])
            else round(float(row["vol_ratio"]), 6),
            "contradiction_direction": self._contradiction_direction,
        }

    def _clear_contradiction(self) -> None:
        self._contradiction_direction = None
        self._contradiction_signal_idx = -1

    def _set_contradiction(self, direction: str, signal_idx: int) -> None:
        self._contradiction_direction = direction
        self._contradiction_signal_idx = int(signal_idx)

    def _entry_cooldown_active(self, signal_idx: int, direction: str) -> bool:
        cooldown = (
            self.entry_cooldown_signal_bars
            if direction == "long"
            else self.short_entry_cooldown_signal_bars
        )
        return cooldown > 0 and signal_idx - self._last_exit_signal_idx <= cooldown

    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
        pv: PortfolioView,
    ) -> Action:
        if self._indicators is None:
            self.prepare(data_so_far)

        signal_idx = self._5m_to_signal.get(date)
        if (
            signal_idx is None
            or self._resampled is None
            or self._indicators is None
            or self._resampled.empty
        ):
            return Action(ActionType.HOLD, details={"reason": "Warming up indicators"})

        signal_idx = int(signal_idx)
        new_signal_bar = signal_idx != self._prev_signal_idx
        self._prev_signal_idx = signal_idx

        if is_last_bar:
            if pv.position_qty > 0:
                self._clear_contradiction()
                return Action(
                    ActionType.SELL,
                    pv.position_qty,
                    self._indicator_details(signal_idx, "Final bar - liquidate long"),
                )
            if pv.short_qty > 0:
                self._clear_contradiction()
                return Action(
                    ActionType.COVER,
                    pv.short_qty,
                    self._indicator_details(signal_idx, "Final bar - cover short"),
                )

        if not new_signal_bar:
            return Action(ActionType.HOLD, details=self._indicator_details(signal_idx, "No signal"))

        st_bull = bool(self._indicators["st_bull"].iloc[signal_idx])
        desired = "long" if st_bull else "short"
        st_flip = self._prev_st_bullish is not None and st_bull != self._prev_st_bullish

        has_long = pv.position_qty > 0
        has_short = pv.short_qty > 0

        if not self._is_rth(date):
            if (
                self.exit_outside_rth_on_core_flip
                and st_flip
                and self._core_filters_allow(signal_idx, desired)
            ):
                self._clear_contradiction()
                if desired == "long" and has_short:
                    self._prev_st_bullish = st_bull
                    return Action(
                        ActionType.COVER,
                        pv.short_qty,
                        self._indicator_details(
                            signal_idx, "outside_rth_core_bull_exit"
                        ),
                    )
                if desired == "short" and has_long:
                    self._last_exit_signal_idx = signal_idx
                    self._prev_st_bullish = st_bull
                    return Action(
                        ActionType.SELL,
                        pv.position_qty,
                        self._indicator_details(
                            signal_idx, "outside_rth_core_bear_exit"
                        ),
                    )
            return Action(
                ActionType.HOLD,
                details=self._indicator_details(signal_idx, "Outside RTH"),
            )

        self._prev_st_bullish = st_bull

        if self._contradiction_direction and desired != self._contradiction_direction:
            self._clear_contradiction()

        price = float(row["close"])
        details = self._indicator_details(signal_idx, "Holding")

        if not has_long and not has_short:
            if self._entry_cooldown_active(signal_idx, desired):
                return Action(
                    ActionType.HOLD,
                    details=self._indicator_details(signal_idx, "Entry cooldown"),
                )
            if desired == "long" and self._filters_allow(signal_idx, "long", date):
                qty = self._entry_quantity(pv, price)
                if qty > 0:
                    self._clear_contradiction()
                    return Action(
                        ActionType.BUY,
                        qty,
                        self._indicator_details(signal_idx, "flat_st_long_confirmed"),
                    )
            if (
                desired == "short"
                and self.enable_short
                and self._filters_allow(signal_idx, "short", date)
            ):
                qty = self._entry_quantity(pv, price)
                if qty > 0:
                    self._clear_contradiction()
                    return Action(
                        ActionType.SHORT,
                        qty,
                        self._indicator_details(signal_idx, "flat_st_short_confirmed"),
                    )
            return Action(ActionType.HOLD, details=self._indicator_details(signal_idx, "Flat waiting"))

        if st_flip:
            if desired == "long":
                if has_long:
                    self._clear_contradiction()
                    return Action(ActionType.HOLD, details=details)
                if self._filters_allow(signal_idx, "long", date):
                    self._clear_contradiction()
                    if has_short:
                        return Action(
                            ActionType.COVER,
                            pv.short_qty,
                            self._indicator_details(signal_idx, "st_bull_confirmed"),
                        )
                    qty = self._entry_quantity(pv, price)
                    if qty > 0:
                        return Action(
                            ActionType.BUY,
                            qty,
                            self._indicator_details(signal_idx, "st_bull_confirmed"),
                        )
                self._set_contradiction("long", signal_idx)
                return Action(ActionType.HOLD, details=self._indicator_details(signal_idx, "st_bull_unconfirmed"))

            if has_short:
                self._clear_contradiction()
                return Action(ActionType.HOLD, details=details)
            if self._filters_allow(signal_idx, "short", date):
                self._clear_contradiction()
                if has_long:
                    self._last_exit_signal_idx = signal_idx
                    return Action(
                        ActionType.SELL,
                        pv.position_qty,
                        self._indicator_details(signal_idx, "st_bear_confirmed"),
                    )
                if self.enable_short:
                    qty = self._entry_quantity(pv, price)
                    if qty > 0:
                        return Action(
                            ActionType.SHORT,
                            qty,
                            self._indicator_details(signal_idx, "st_bear_confirmed"),
                        )
            self._set_contradiction("short", signal_idx)
            return Action(ActionType.HOLD, details=self._indicator_details(signal_idx, "st_bear_unconfirmed"))

        if self._contradiction_direction:
            age = signal_idx - self._contradiction_signal_idx
            if age > self.reject_late_contradiction_bars:
                self._clear_contradiction()
                return Action(ActionType.HOLD, details=self._indicator_details(signal_idx, "Contradiction expired"))

            direction = self._contradiction_direction
            if self._filters_allow(signal_idx, direction, date):
                self._clear_contradiction()
                if direction == "long":
                    if has_short:
                        return Action(
                            ActionType.COVER,
                            pv.short_qty,
                            self._indicator_details(signal_idx, "delayed_bull_confirmed"),
                        )
                    if not has_long:
                        qty = self._entry_quantity(pv, price)
                        if qty > 0:
                            return Action(
                                ActionType.BUY,
                                qty,
                                self._indicator_details(signal_idx, "delayed_bull_confirmed"),
                            )
                else:
                    if has_long:
                        self._last_exit_signal_idx = signal_idx
                        return Action(
                            ActionType.SELL,
                            pv.position_qty,
                            self._indicator_details(signal_idx, "delayed_bear_confirmed"),
                        )
                    if self.enable_short and not has_short:
                        qty = self._entry_quantity(pv, price)
                        if qty > 0:
                            return Action(
                                ActionType.SHORT,
                                qty,
                                self._indicator_details(signal_idx, "delayed_bear_confirmed"),
                            )

        return Action(ActionType.HOLD, details=details)
