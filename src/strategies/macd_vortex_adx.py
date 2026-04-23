import math
from datetime import time

import numpy as np
import pandas as pd

from .base import StrategyBase, Action, ActionType, PortfolioView
from .intraday_indicators import compute_vortex
from .macd_rsi_advanced import compute_adx, compute_atr, compute_macd


class MACDVortexADXStrategy(StrategyBase):
    """Intraday breakout strategy driven by MACD alert + Vortex/ADX confirmation."""

    display_name = "MACD Vortex ADX"

    def __init__(self, config: dict):
        super().__init__(config)

        self.symbol = config.get("symbol", "BTCUSDT")
        self.asset_class = str(config.get("asset_class", "auto")).lower()
        self.resample_interval = config.get("resample_interval", "30min")

        self.macd_fast = int(config.get("macd_fast", 12))
        self.macd_slow = int(config.get("macd_slow", 26))
        self.macd_signal = int(config.get("macd_signal", 9))
        self.use_histogram_flip = bool(config.get("use_histogram_flip", True))
        self.macd_fresh_bars = int(config.get("macd_fresh_bars", 2))
        self.require_macd_above_zero_for_long = bool(
            config.get("require_macd_above_zero_for_long", False)
        )

        self.vortex_period = int(config.get("vortex_period", 14))
        self.vortex_baseline_bars = int(config.get("vortex_baseline_bars", 3))
        self.vortex_strong_spread_mult = float(
            config.get("vortex_strong_spread_mult", 1.25)
        )
        self.vortex_hugging_spread_mult = float(
            config.get("vortex_hugging_spread_mult", 1.05)
        )
        self.vortex_weave_lookback = int(config.get("vortex_weave_lookback", 2))

        self.adx_period = int(config.get("adx_period", 14))
        self.adx_floor = float(config.get("adx_floor", 20.0))
        self.require_adx_rising = bool(config.get("require_adx_rising", True))

        self.breakout_lookback_bars = int(config.get("breakout_lookback_bars", 3))
        self.armed_breakout_expiry_bars = int(
            config.get("armed_breakout_expiry_bars", 2)
        )

        self.atr_period = int(config.get("atr_period", 14))
        self.atr_stop_multiplier = float(config.get("atr_stop_multiplier", 2.0))
        self.atr_trailing_multiplier = float(
            config.get("atr_trailing_multiplier", 1.5)
        )
        self.trailing_stop_rth_only_for_equities = bool(
            config.get("trailing_stop_rth_only_for_equities", False)
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
        self.enable_short = bool(config.get("enable_short", True))

        self._resample_freq = pd.tseries.frequencies.to_offset(self.resample_interval)

        warmup_bars = max(
            self.macd_slow + self.macd_signal + 5,
            self.vortex_period + self.vortex_baseline_bars + 3,
            self.adx_period + 3,
            self.atr_period + 3,
            self.breakout_lookback_bars + 3,
        )
        interval_hours = (
            pd.Timedelta(self._resample_freq).total_seconds() / 3600.0
            if hasattr(self._resample_freq, "delta")
            else pd.Timedelta(self.resample_interval).total_seconds() / 3600.0
        )
        self.min_warmup_hours = max(1, math.ceil(warmup_bars * interval_hours))

        self._reset_runtime_state()
        self._indicators = None
        self._resampled = None
        self._5m_to_signal = {}
        self._last_resampled_ts = None

    def _reset_runtime_state(self) -> None:
        self._prev_signal_idx = -1
        self._bar_count = 0

        self._in_long = False
        self._in_short = False
        self._entry_price = None
        self._entry_signal_idx = -1
        self._initial_stop_price = None
        self._peak_since_entry = None
        self._trough_since_entry = None

        self._alert_direction = None
        self._alert_signal_idx = -1
        self._alert_source = None

        self._armed_direction = None
        self._armed_trigger_price = None
        self._armed_signal_idx = -1
        self._armed_expiry_idx = -1
        self._armed_stop_ref = None
        self._armed_atr = None

    def save_state(self) -> dict:
        return {"prev_signal_idx": self._prev_signal_idx}

    def restore_state(self, state: dict) -> None:
        if not state:
            return
        self._prev_signal_idx = int(state.get("prev_signal_idx", -1))

    def export_state(self) -> dict:
        return {
            "prev_signal_idx": self._prev_signal_idx,
            "bar_count": self._bar_count,
            "in_long": self._in_long,
            "in_short": self._in_short,
            "entry_price": self._entry_price,
            "entry_signal_idx": self._entry_signal_idx,
            "initial_stop_price": self._initial_stop_price,
            "peak_since_entry": self._peak_since_entry,
            "trough_since_entry": self._trough_since_entry,
            "alert_direction": self._alert_direction,
            "alert_signal_idx": self._alert_signal_idx,
            "alert_source": self._alert_source,
            "armed_direction": self._armed_direction,
            "armed_trigger_price": self._armed_trigger_price,
            "armed_signal_idx": self._armed_signal_idx,
            "armed_expiry_idx": self._armed_expiry_idx,
            "armed_stop_ref": self._armed_stop_ref,
            "armed_atr": self._armed_atr,
        }

    def import_state(self, state: dict) -> None:
        if not state:
            return
        self._prev_signal_idx = int(state.get("prev_signal_idx", -1))
        self._bar_count = int(state.get("bar_count", 0))
        self._in_long = bool(state.get("in_long", False))
        self._in_short = bool(state.get("in_short", False))
        self._entry_price = state.get("entry_price")
        self._entry_signal_idx = int(state.get("entry_signal_idx", -1))
        self._initial_stop_price = state.get("initial_stop_price")
        self._peak_since_entry = state.get("peak_since_entry")
        self._trough_since_entry = state.get("trough_since_entry")
        self._alert_direction = state.get("alert_direction")
        self._alert_signal_idx = int(state.get("alert_signal_idx", -1))
        self._alert_source = state.get("alert_source")
        self._armed_direction = state.get("armed_direction")
        self._armed_trigger_price = state.get("armed_trigger_price")
        self._armed_signal_idx = int(state.get("armed_signal_idx", -1))
        self._armed_expiry_idx = int(state.get("armed_expiry_idx", -1))
        self._armed_stop_ref = state.get("armed_stop_ref")
        self._armed_atr = state.get("armed_atr")

    def reset_position(self) -> None:
        self._in_long = False
        self._in_short = False
        self._entry_price = None
        self._entry_signal_idx = -1
        self._initial_stop_price = None
        self._peak_since_entry = None
        self._trough_since_entry = None
        self._clear_armed_breakout()

    def _clear_alert(self) -> None:
        self._alert_direction = None
        self._alert_signal_idx = -1
        self._alert_source = None

    def _clear_armed_breakout(self) -> None:
        self._armed_direction = None
        self._armed_trigger_price = None
        self._armed_signal_idx = -1
        self._armed_expiry_idx = -1
        self._armed_stop_ref = None
        self._armed_atr = None

    def _set_alert(self, direction: str, signal_idx: int, source: str) -> None:
        self._alert_direction = direction
        self._alert_signal_idx = int(signal_idx)
        self._alert_source = source
        if self._armed_direction and self._armed_direction != direction:
            self._clear_armed_breakout()

    def _alert_is_fresh(self, signal_idx: int, direction: str | None = None) -> bool:
        if self._alert_direction is None or self._alert_signal_idx < 0:
            return False
        if direction is not None and self._alert_direction != direction:
            return False
        return (signal_idx - self._alert_signal_idx) <= self.macd_fresh_bars

    def _arm_breakout(
        self,
        direction: str,
        signal_idx: int,
        trigger_price: float,
        stop_ref: float,
        atr_value: float,
    ) -> None:
        self._armed_direction = direction
        self._armed_trigger_price = float(trigger_price)
        self._armed_signal_idx = int(signal_idx)
        self._armed_expiry_idx = int(signal_idx + self.armed_breakout_expiry_bars)
        self._armed_stop_ref = float(stop_ref)
        self._armed_atr = float(atr_value)

    def prepare(self, full_data: pd.DataFrame) -> None:
        resampled = full_data.resample(self.resample_interval).agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        ).dropna()

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
            self._last_resampled_ts = None
            return

        closes = resampled["close"]
        highs = resampled["high"]
        lows = resampled["low"]

        macd_line, macd_signal, histogram = compute_macd(
            closes, self.macd_fast, self.macd_slow, self.macd_signal
        )
        adx = compute_adx(highs, lows, closes, self.adx_period)
        atr = compute_atr(highs, lows, closes, self.atr_period)
        vi_plus, vi_minus = compute_vortex(highs, lows, closes, self.vortex_period)
        spread = (vi_plus - vi_minus).abs()

        self._indicators = pd.DataFrame(
            {
                "macd": macd_line,
                "macd_signal": macd_signal,
                "histogram": histogram,
                "adx": adx,
                "atr": atr,
                "vi_plus": vi_plus,
                "vi_minus": vi_minus,
                "vortex_spread": spread,
            },
            index=resampled.index,
        )

        resampled_ts = resampled.index
        self._5m_to_signal = {}
        for ts_5m in full_data.index:
            target = ts_5m + pd.Timedelta(minutes=5) - self._resample_freq
            idx = resampled_ts.get_indexer([target], method="ffill")[0]
            if idx >= 0:
                self._5m_to_signal[ts_5m] = idx
        self._last_resampled_ts = resampled.index[-1]

    def update(self, full_data: pd.DataFrame) -> None:
        if full_data.empty:
            return

        last_5m_ts = full_data.index[-1]
        last_resample_start = last_5m_ts.floor(self._resample_freq)
        last_resample_end = (
            last_resample_start + self._resample_freq - pd.Timedelta(minutes=5)
        )

        if last_5m_ts >= last_resample_end:
            if (
                self._last_resampled_ts is None
                or last_resample_start > self._last_resampled_ts
            ):
                self.prepare(full_data)
                return

        if self._resampled is not None and len(self._resampled) > 0:
            target = last_5m_ts + pd.Timedelta(minutes=5) - self._resample_freq
            idx = self._resampled.index.get_indexer([target], method="ffill")[0]
            if idx >= 0:
                self._5m_to_signal[last_5m_ts] = idx

    def warmup_bar(self, date, row, _data_so_far, _is_last_bar) -> None:
        self._bar_count += 1
        signal_idx = self._5m_to_signal.get(date)
        if signal_idx is not None:
            self._prev_signal_idx = int(signal_idx)

    def _signal_ready(self, signal_idx: int) -> bool:
        min_idx = max(
            self.macd_slow + self.macd_signal,
            self.vortex_period + self.vortex_baseline_bars,
            self.adx_period + 1,
            self.atr_period,
            self.breakout_lookback_bars,
        )
        return signal_idx >= min_idx

    @staticmethod
    def _py_float(value):
        return float(value) if value is not None and pd.notna(value) else None

    @staticmethod
    def _py_bool(value):
        return None if value is None else bool(value)

    @staticmethod
    def _parse_session_time(value: str) -> time:
        return pd.Timestamp(str(value)).time()

    def _is_equity_asset(self) -> bool:
        return self.asset_class in {"equity", "stock", "etf"}

    def _is_regular_equity_session(self, date: pd.Timestamp) -> bool:
        ts = pd.Timestamp(date)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        local_ts = ts.tz_convert(self.equity_session_timezone)
        local_time = local_ts.time()
        return (
            self.equity_regular_session_start
            <= local_time
            < self.equity_regular_session_end
        )

    def _trailing_stop_active(self, date: pd.Timestamp) -> bool:
        if not self.trailing_stop_rth_only_for_equities:
            return True
        if not self._is_equity_asset():
            return True
        return self._is_regular_equity_session(date)

    def _vortex_state(self, signal_idx: int) -> dict:
        spread_now = self._indicators["vortex_spread"].iloc[signal_idx]
        prev_spread = self._indicators["vortex_spread"].iloc[signal_idx - 1]
        baseline = self._indicators["vortex_spread"].iloc[
            signal_idx - self.vortex_baseline_bars: signal_idx
        ].mean()

        vi_plus = self._indicators["vi_plus"].iloc[signal_idx]
        vi_minus = self._indicators["vi_minus"].iloc[signal_idx]
        direction = "long" if vi_plus >= vi_minus else "short"

        winner = np.sign(
            self._indicators["vi_plus"] - self._indicators["vi_minus"]
        ).replace(0, np.nan).ffill().fillna(0.0)
        start = max(1, signal_idx - self.vortex_weave_lookback + 1)
        weave = False
        for i in range(start, signal_idx + 1):
            if winner.iloc[i] != 0 and winner.iloc[i - 1] != 0 and winner.iloc[i] != winner.iloc[i - 1]:
                weave = True
                break

        strong = (
            pd.notna(spread_now)
            and pd.notna(prev_spread)
            and pd.notna(baseline)
            and spread_now > prev_spread
            and spread_now >= baseline * self.vortex_strong_spread_mult
        )
        hugging = (
            weave
            or pd.isna(spread_now)
            or pd.isna(baseline)
            or spread_now <= baseline * self.vortex_hugging_spread_mult
        )

        if strong:
            classification = "strong"
        elif hugging:
            classification = "hugging"
        else:
            classification = "borderline"

        return {
            "direction": direction,
            "classification": classification,
            "spread": self._py_float(spread_now),
            "baseline": self._py_float(baseline),
            "weave": bool(weave),
            "vi_plus": self._py_float(vi_plus),
            "vi_minus": self._py_float(vi_minus),
        }

    def _adx_ok(self, signal_idx: int) -> tuple[bool, float | None, bool]:
        adx_now = self._indicators["adx"].iloc[signal_idx]
        adx_prev = self._indicators["adx"].iloc[signal_idx - 1]
        rising = pd.notna(adx_now) and pd.notna(adx_prev) and adx_now > adx_prev
        floor_ok = pd.notna(adx_now) and float(adx_now) >= self.adx_floor
        if self.require_adx_rising:
            ok = floor_ok and rising
        else:
            ok = floor_ok
        return ok, (float(adx_now) if pd.notna(adx_now) else None), rising

    def _price_breakout_ok(self, signal_idx: int, direction: str) -> bool:
        lookback_start = signal_idx - self.breakout_lookback_bars
        if lookback_start < 0:
            return False
        signal_row = self._resampled.iloc[signal_idx]
        prior = self._resampled.iloc[lookback_start:signal_idx]
        if prior.empty:
            return False
        if direction == "long":
            return bool(signal_row["high"] > float(prior["high"].max()))
        return bool(signal_row["low"] < float(prior["low"].min()))

    def _macd_zero_line_ok(
        self, signal_idx: int, direction: str | None
    ) -> tuple[bool, float | None]:
        if direction is None:
            return False, None
        macd = self._indicators["macd"].iloc[signal_idx]
        macd_value = self._py_float(macd)
        if macd_value is None:
            return False, None
        if direction == "long" and self.require_macd_above_zero_for_long:
            return macd_value > 0.0, macd_value
        return True, macd_value

    def _alert_signal(self, signal_idx: int) -> tuple[str | None, str | None]:
        prev_macd = self._indicators["macd"].iloc[signal_idx - 1]
        prev_signal = self._indicators["macd_signal"].iloc[signal_idx - 1]
        macd = self._indicators["macd"].iloc[signal_idx]
        signal = self._indicators["macd_signal"].iloc[signal_idx]
        prev_hist = self._indicators["histogram"].iloc[signal_idx - 1]
        hist = self._indicators["histogram"].iloc[signal_idx]

        bullish_cross = prev_macd <= prev_signal and macd > signal
        bearish_cross = prev_macd >= prev_signal and macd < signal
        bullish_hist = self.use_histogram_flip and prev_hist <= 0 and hist > 0
        bearish_hist = self.use_histogram_flip and prev_hist >= 0 and hist < 0

        if bullish_cross or bullish_hist:
            return "long", "macd_cross" if bullish_cross else "histogram_flip"
        if bearish_cross or bearish_hist:
            return "short", "macd_cross" if bearish_cross else "histogram_flip"
        return None, None

    def _indicator_details(
        self,
        signal_idx: int,
        *,
        new_signal_bar: bool,
        vortex: dict | None = None,
        adx_now: float | None = None,
        adx_rising: bool | None = None,
    ) -> dict:
        ind = self._indicators.iloc[signal_idx]
        details = {
            "signal_idx": int(signal_idx),
            "is_signal_close": bool(new_signal_bar),
            "macd": round(float(ind["macd"]), 6) if pd.notna(ind["macd"]) else None,
            "macd_signal": round(float(ind["macd_signal"]), 6)
            if pd.notna(ind["macd_signal"])
            else None,
            "macd_hist": round(float(ind["histogram"]), 6)
            if pd.notna(ind["histogram"])
            else None,
            "adx": round(float(adx_now if adx_now is not None else ind["adx"]), 6)
            if pd.notna(ind["adx"])
            else None,
            "adx_rising": self._py_bool(adx_rising),
            "atr": round(float(ind["atr"]), 6) if pd.notna(ind["atr"]) else None,
            "alert_direction": self._alert_direction,
            "alert_age": (
                int(signal_idx - self._alert_signal_idx)
                if self._alert_signal_idx >= 0
                else None
            ),
            "armed_direction": self._armed_direction,
            "armed_trigger_price": self._armed_trigger_price,
            "require_macd_above_zero_for_long": bool(
                self.require_macd_above_zero_for_long
            ),
        }
        if vortex is not None:
            details.update(
                {
                    "vortex_plus": vortex["vi_plus"],
                    "vortex_minus": vortex["vi_minus"],
                    "vortex_spread": (
                        round(float(vortex["spread"]), 6)
                        if vortex["spread"] is not None
                        else None
                    ),
                    "vortex_baseline": (
                        round(float(vortex["baseline"]), 6)
                        if vortex["baseline"] is not None
                        else None
                    ),
                    "vortex_state": vortex["classification"],
                    "vortex_direction": vortex["direction"],
                    "vortex_weave": self._py_bool(vortex["weave"]),
                }
            )
        return details

    def _set_position_state(
        self,
        direction: str,
        entry_price: float,
        signal_idx: int,
        stop_ref: float,
        atr_value: float,
    ) -> None:
        stop_distance = max(
            abs(entry_price - stop_ref),
            self.atr_stop_multiplier * atr_value,
        )
        if direction == "long":
            self._in_long = True
            self._in_short = False
            self._peak_since_entry = entry_price
            self._trough_since_entry = None
            self._initial_stop_price = entry_price - stop_distance
        else:
            self._in_long = False
            self._in_short = True
            self._peak_since_entry = None
            self._trough_since_entry = entry_price
            self._initial_stop_price = entry_price + stop_distance
        self._entry_price = entry_price
        self._entry_signal_idx = int(signal_idx)
        self._clear_alert()
        self._clear_armed_breakout()

    def _clear_position_state(self) -> None:
        self._in_long = False
        self._in_short = False
        self._entry_price = None
        self._entry_signal_idx = -1
        self._initial_stop_price = None
        self._peak_since_entry = None
        self._trough_since_entry = None

    def _entry_quantity(self, pv: PortfolioView, price: float) -> float:
        return math.floor(pv.cash / price * 1e8) / 1e8 if price > 0 else 0.0

    def _check_intrabar_trigger(
        self,
        row: pd.Series,
        price: float,
        pv: PortfolioView,
        signal_idx: int,
        details: dict,
    ) -> Action | None:
        if self._armed_direction is None:
            return None
        if signal_idx > self._armed_expiry_idx:
            self._clear_armed_breakout()
            return Action(ActionType.HOLD, details={**details, "reason": "Armed breakout expired"})

        if self._armed_direction == "long":
            breached = row["high"] >= self._armed_trigger_price
            action_type = ActionType.BUY
            entry_reason = "armed_breakout_long"
        else:
            breached = row["low"] <= self._armed_trigger_price
            action_type = ActionType.SHORT
            entry_reason = "armed_breakout_short"

        if not breached:
            return None

        qty = self._entry_quantity(pv, price)
        if qty <= 0:
            return Action(ActionType.HOLD, details={**details, "reason": "No cash for breakout"})

        self._set_position_state(
            self._armed_direction,
            price,
            self._armed_signal_idx,
            self._armed_stop_ref,
            self._armed_atr,
        )
        return Action(
            action_type,
            qty,
            {
                **details,
                "reason": "Armed breakout filled",
                "entry_reason": entry_reason,
                "trigger_price": self._armed_trigger_price,
            },
        )

    def _check_position_exit(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        price: float,
        pv: PortfolioView,
        signal_idx: int,
        new_signal_bar: bool,
        details: dict,
    ) -> Action | None:
        atr_now = self._indicators["atr"].iloc[signal_idx]
        if pd.isna(atr_now):
            return None
        atr_now = float(atr_now)
        trailing_stop_active = self._trailing_stop_active(date)

        if self._in_long and pv.position_qty > 0:
            self._peak_since_entry = max(self._peak_since_entry or price, float(row["close"]))
            if row["low"] <= self._initial_stop_price:
                qty = pv.position_qty
                self._clear_position_state()
                return Action(
                    ActionType.SELL,
                    qty,
                    {**details, "reason": "Initial stop hit", "exit_reason": "initial_stop"},
                )
            trail_price = self._peak_since_entry - self.atr_trailing_multiplier * atr_now
            if trailing_stop_active and row["low"] <= trail_price:
                qty = pv.position_qty
                self._clear_position_state()
                return Action(
                    ActionType.SELL,
                    qty,
                    {**details, "reason": "Trailing stop hit", "exit_reason": "trailing_stop"},
                )
            if new_signal_bar:
                setup = self._evaluate_setup(signal_idx)
                if setup["confirmed"] and setup["direction"] == "short":
                    qty = pv.position_qty
                    self._set_alert("short", signal_idx, setup["source"])
                    if setup["vortex"]["classification"] == "borderline":
                        signal_row = self._resampled.iloc[signal_idx]
                        self._arm_breakout(
                            "short",
                            signal_idx,
                            float(signal_row["low"]),
                            float(signal_row["high"]),
                            float(self._indicators["atr"].iloc[signal_idx]),
                        )
                    else:
                        self._clear_armed_breakout()
                    self._clear_position_state()
                    return Action(
                        ActionType.SELL,
                        qty,
                        {**details, "reason": "Opposite confirmed setup", "exit_reason": "opposite_setup"},
                    )
            return None

        if self._in_short and pv.short_qty > 0:
            self._trough_since_entry = min(self._trough_since_entry or price, float(row["close"]))
            if row["high"] >= self._initial_stop_price:
                qty = pv.short_qty
                self._clear_position_state()
                return Action(
                    ActionType.COVER,
                    qty,
                    {**details, "reason": "Initial stop hit", "exit_reason": "initial_stop"},
                )
            trail_price = self._trough_since_entry + self.atr_trailing_multiplier * atr_now
            if trailing_stop_active and row["high"] >= trail_price:
                qty = pv.short_qty
                self._clear_position_state()
                return Action(
                    ActionType.COVER,
                    qty,
                    {**details, "reason": "Trailing stop hit", "exit_reason": "trailing_stop"},
                )
            if new_signal_bar:
                setup = self._evaluate_setup(signal_idx)
                if setup["confirmed"] and setup["direction"] == "long":
                    qty = pv.short_qty
                    self._set_alert("long", signal_idx, setup["source"])
                    if setup["vortex"]["classification"] == "borderline":
                        signal_row = self._resampled.iloc[signal_idx]
                        self._arm_breakout(
                            "long",
                            signal_idx,
                            float(signal_row["high"]),
                            float(signal_row["low"]),
                            float(self._indicators["atr"].iloc[signal_idx]),
                        )
                    else:
                        self._clear_armed_breakout()
                    self._clear_position_state()
                    return Action(
                        ActionType.COVER,
                        qty,
                        {**details, "reason": "Opposite confirmed setup", "exit_reason": "opposite_setup"},
                    )
            return None

        return None

    def _evaluate_setup(self, signal_idx: int) -> dict:
        direction, source = self._alert_signal(signal_idx)
        if direction is not None:
            self._set_alert(direction, signal_idx, source)

        if not self._signal_ready(signal_idx):
            return {
                "direction": direction,
                "source": source,
                "confirmed": False,
                "vortex": None,
                "adx_ok": False,
                "adx_now": None,
                "adx_rising": None,
                "price_breakout": False,
                "fresh": False,
            }

        # Cancel stale alert on MACD bias reversal.
        macd = self._indicators["macd"].iloc[signal_idx]
        signal = self._indicators["macd_signal"].iloc[signal_idx]
        if self._alert_direction == "long" and macd <= signal:
            self._clear_alert()
            self._clear_armed_breakout()
        elif self._alert_direction == "short" and macd >= signal:
            self._clear_alert()
            self._clear_armed_breakout()

        if self._alert_direction is None:
            return {
                "direction": None,
                "source": None,
                "confirmed": False,
                "vortex": None,
                "adx_ok": False,
                "adx_now": None,
                "adx_rising": None,
                "price_breakout": False,
                "fresh": False,
                "macd_zero_line_ok": False,
                "macd_value": None,
            }

        fresh = self._alert_is_fresh(signal_idx)
        if not fresh:
            self._clear_alert()
            self._clear_armed_breakout()
            return {
                "direction": None,
                "source": None,
                "confirmed": False,
                "vortex": None,
                "adx_ok": False,
                "adx_now": None,
                "adx_rising": None,
                "price_breakout": False,
                "fresh": False,
                "macd_zero_line_ok": False,
                "macd_value": None,
            }

        vortex = self._vortex_state(signal_idx)
        adx_ok, adx_now, adx_rising = self._adx_ok(signal_idx)
        macd_zero_line_ok, macd_value = self._macd_zero_line_ok(
            signal_idx, self._alert_direction
        )
        price_breakout = self._price_breakout_ok(signal_idx, self._alert_direction)
        confirmed = (
            vortex["direction"] == self._alert_direction
            and vortex["classification"] in {"strong", "borderline"}
            and adx_ok
            and macd_zero_line_ok
        )

        if vortex["classification"] == "hugging":
            self._clear_armed_breakout()
        elif (
            confirmed
            and vortex["classification"] == "borderline"
            and self._alert_signal_idx == signal_idx
        ):
            signal_row = self._resampled.iloc[signal_idx]
            if self._alert_direction == "long":
                self._arm_breakout(
                    "long",
                    signal_idx,
                    float(signal_row["high"]),
                    float(signal_row["low"]),
                    float(self._indicators["atr"].iloc[signal_idx]),
                )
            else:
                self._arm_breakout(
                    "short",
                    signal_idx,
                    float(signal_row["low"]),
                    float(signal_row["high"]),
                    float(self._indicators["atr"].iloc[signal_idx]),
                )

        if self._armed_direction and signal_idx > self._armed_expiry_idx:
            self._clear_armed_breakout()

        return {
            "direction": self._alert_direction,
            "source": self._alert_source,
            "confirmed": confirmed,
            "vortex": vortex,
            "adx_ok": adx_ok,
            "adx_now": adx_now,
            "adx_rising": adx_rising,
            "price_breakout": price_breakout,
            "fresh": fresh,
            "macd_zero_line_ok": macd_zero_line_ok,
            "macd_value": macd_value,
        }

    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
        pv: PortfolioView,
    ) -> Action:
        self._bar_count += 1

        if self._indicators is None:
            self.prepare(data_so_far)

        signal_idx = self._5m_to_signal.get(date)
        if signal_idx is None or self._resampled is None or self._resampled.empty:
            return Action(ActionType.HOLD, details={"reason": "Warming up indicators"})

        signal_idx = int(signal_idx)
        new_signal_bar = signal_idx != self._prev_signal_idx
        self._prev_signal_idx = signal_idx

        if not self._signal_ready(signal_idx):
            return Action(ActionType.HOLD, details={"reason": "Warming up indicators"})

        price = float(row["close"])
        has_long = pv.position_qty > 0
        has_short = pv.short_qty > 0

        details = self._indicator_details(signal_idx, new_signal_bar=new_signal_bar)

        if is_last_bar:
            if has_long:
                self._clear_position_state()
                return Action(
                    ActionType.SELL,
                    pv.position_qty,
                    {**details, "reason": "Final bar - liquidate long"},
                )
            if has_short:
                self._clear_position_state()
                return Action(
                    ActionType.COVER,
                    pv.short_qty,
                    {**details, "reason": "Final bar - cover short"},
                )

        exit_action = self._check_position_exit(
            date, row, price, pv, signal_idx, new_signal_bar, details
        )
        if exit_action is not None:
            return exit_action

        if has_long or has_short:
            return Action(ActionType.HOLD, details={**details, "reason": "Holding"})

        intrabar_action = self._check_intrabar_trigger(
            row, price, pv, signal_idx, details
        )
        if intrabar_action is not None:
            return intrabar_action

        if not new_signal_bar:
            return Action(ActionType.HOLD, details={**details, "reason": "No signal (intra-bar)"})

        setup = self._evaluate_setup(signal_idx)
        details = self._indicator_details(
            signal_idx,
            new_signal_bar=new_signal_bar,
            vortex=setup["vortex"],
            adx_now=setup["adx_now"],
            adx_rising=setup["adx_rising"],
        )
        details["macd_zero_line_ok"] = self._py_bool(setup["macd_zero_line_ok"])

        if setup["direction"] is None:
            return Action(ActionType.HOLD, details={**details, "reason": "No fresh MACD alert"})
        if setup["direction"] == "short" and not self.enable_short:
            return Action(ActionType.HOLD, details={**details, "reason": "Shorts disabled"})
        if not setup["fresh"]:
            return Action(ActionType.HOLD, details={**details, "reason": "Alert expired"})
        if setup["vortex"]["classification"] == "hugging":
            return Action(ActionType.HOLD, details={**details, "reason": "Vortex hugging"})
        if not setup["macd_zero_line_ok"]:
            return Action(
                ActionType.HOLD,
                details={**details, "reason": "MACD below zero line"},
            )
        if not setup["adx_ok"]:
            return Action(ActionType.HOLD, details={**details, "reason": "ADX not confirmed"})

        qty = self._entry_quantity(pv, price)
        if qty <= 0:
            return Action(ActionType.HOLD, details={**details, "reason": "No cash to enter"})

        if (
            setup["confirmed"]
            and setup["vortex"]["classification"] == "strong"
            and setup["price_breakout"]
        ):
            signal_row = self._resampled.iloc[signal_idx]
            atr_value = float(self._indicators["atr"].iloc[signal_idx])
            if setup["direction"] == "long":
                self._set_position_state(
                    "long",
                    price,
                    signal_idx,
                    float(signal_row["low"]),
                    atr_value,
                )
                return Action(
                    ActionType.BUY,
                    qty,
                    {
                        **details,
                        "reason": "Immediate long entry",
                        "entry_reason": "macd_vortex_adx_immediate_long",
                    },
                )
            self._set_position_state(
                "short",
                price,
                signal_idx,
                float(signal_row["high"]),
                atr_value,
            )
            return Action(
                ActionType.SHORT,
                qty,
                {
                    **details,
                    "reason": "Immediate short entry",
                    "entry_reason": "macd_vortex_adx_immediate_short",
                },
            )

        if self._armed_direction is not None:
            return Action(
                ActionType.HOLD,
                details={**details, "reason": "Armed breakout waiting"},
            )

        return Action(
            ActionType.HOLD,
            details={**details, "reason": "Setup not actionable"},
        )
