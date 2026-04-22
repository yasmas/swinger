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
from .intraday_indicators import (
    compute_hma,
    compute_hmacd,
    compute_realised_vol,
    compute_supertrend,
)
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

        # Slow volatility-regime detector used by the squared flip-vol gate.
        self.adaptive_st_vol_period = int(config.get("adaptive_st_vol_period", 4))
        self.adaptive_st_vol_long_period = int(
            config.get("adaptive_st_vol_long_period", 336)
        )
        self.adaptive_st_enter_ratio_threshold = float(
            config.get("adaptive_st_enter_ratio_threshold", 1.0)
        )
        self.adaptive_st_exit_ratio_threshold = float(
            config.get(
                "adaptive_st_exit_ratio_threshold",
                self.adaptive_st_enter_ratio_threshold,
            )
        )
        self.adaptive_st_min_high_bars = int(
            config.get("adaptive_st_min_high_bars", 0)
        )

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

        # Entry gap gating: if the gap (in resampled bars) between the previous
        # hourly evaluation and the current one exceeds nominal, apply tiered
        # staleness checks. In uninterrupted operation gap is always 1, so the
        # nominal fast path always runs and behavior is unchanged.
        self.entry_gap_nominal_bars = int(config.get("entry_gap_nominal_bars", 2))
        self.entry_gap_extended_bars = int(config.get("entry_gap_extended_bars", 8))
        self.entry_gap_price_drift_pct = float(config.get("entry_gap_price_drift_pct", 1.0))

        # After ST flip, keep trying for up to N resampled-bar closes while ROC
        # agrees and price stays within max drift of the flip-bar resampled close.
        # 0 = enter immediately on flip (when entry_delay_hours is also 0).
        self.entry_persist_max_bars = int(config.get("entry_persist_max_bars", 0))
        self.entry_persist_max_price_drift = float(
            config.get("entry_persist_max_price_drift", 0.01)
        )
        self.entry_persist_roc_lookback = int(config.get("entry_persist_roc_lookback", 1))

        # Flip-vol ratio gate: compare short realised vol to the prior 1-week
        # average of that same realised-vol series. If the ratio is too weak,
        # keep holding through the flip. A separate safety stop can force an
        # exit if price keeps moving against the held position after rejection.
        self.flip_vol_ratio_enabled = bool(config.get("flip_vol_ratio_enabled", False))
        self.flip_vol_ratio_short_period = int(config.get("flip_vol_ratio_short_period", 4))
        self.flip_vol_ratio_long_period = int(config.get("flip_vol_ratio_long_period", 336))
        self.flip_vol_ratio_min = float(config.get("flip_vol_ratio_min", 1.0))
        self.flip_vol_ratio_safety_stop_pct = (
            float(config.get("flip_vol_ratio_safety_stop_pct", 0.0)) / 100.0
        )
        self.flip_vol_ratio_regime_mode = str(
            config.get("flip_vol_ratio_regime_mode", "fixed")
        ).lower()
        self.flip_vol_ratio_regime_low_min = float(
            config.get("flip_vol_ratio_regime_low_min", self.flip_vol_ratio_min)
        )
        self.flip_vol_ratio_regime_high_min = float(
            config.get("flip_vol_ratio_regime_high_min", self.flip_vol_ratio_min)
        )
        self.flip_vol_ratio_regime_low_stop_pct = (
            float(
                config.get(
                    "flip_vol_ratio_regime_low_stop_pct",
                    self.flip_vol_ratio_safety_stop_pct * 100.0,
                )
            ) / 100.0
        )
        self.flip_vol_ratio_regime_high_stop_pct = (
            float(
                config.get(
                    "flip_vol_ratio_regime_high_stop_pct",
                    self.flip_vol_ratio_safety_stop_pct * 100.0,
                )
            ) / 100.0
        )
        self.flip_vol_ratio_regime_low_anchor = float(
            config.get(
                "flip_vol_ratio_regime_low_anchor",
                self.adaptive_st_exit_ratio_threshold,
            )
        )
        self.flip_vol_ratio_regime_high_anchor = float(
            config.get(
                "flip_vol_ratio_regime_high_anchor",
                self.adaptive_st_enter_ratio_threshold,
            )
        )
        self.flip_vol_ratio_regime_power = float(
            config.get("flip_vol_ratio_regime_power", 2.0)
        )

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

        # Entry persistence (see entry_persist_max_bars)
        self._persist_direction: str | None = None
        self._persist_flip_hourly_idx: int = -1
        self._persist_ref_price: float = 0.0

        # Ratio-gated hold state (reject flip, keep holding, exit only if the
        # same-side safety stop is breached before ST flips back).
        self._flip_vol_short = None
        self._flip_vol_long_mean = None
        self._flip_vol_ratio = None
        self._vol_regime_short = None
        self._vol_regime_long_mean = None
        self._vol_regime_ratio = None
        self._vol_regime_high = None
        self._held_flip_direction: str | None = None
        self._held_flip_price: float = 0.0
        self._held_flip_hourly_idx: int = -1
        self._held_flip_stop_pct: float = 0.0

    def _build_vol_regime(self, vol_ratio: pd.Series) -> pd.Series:
        """Build a hysteresis regime series for the slow volatility state."""
        regime = pd.Series(False, index=vol_ratio.index, dtype=bool)
        in_high = False
        high_bars = 0

        for idx, ratio_now in vol_ratio.items():
            ratio_ready = not pd.isna(ratio_now)
            ratio_value = float(ratio_now) if ratio_ready else None

            if not in_high:
                if ratio_ready and ratio_value >= self.adaptive_st_enter_ratio_threshold:
                    in_high = True
                    high_bars = 1
            else:
                high_bars += 1
                can_exit = high_bars >= self.adaptive_st_min_high_bars
                if (
                    can_exit
                    and ratio_ready
                    and ratio_value < self.adaptive_st_exit_ratio_threshold
                ):
                    in_high = False
                    high_bars = 0

            regime.loc[idx] = in_high

        return regime

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

        regime_series_enabled = (
            self.flip_vol_ratio_enabled and self.flip_vol_ratio_regime_mode != "fixed"
        )

        if regime_series_enabled:
            vol_short = compute_realised_vol(
                closes,
                period=self.adaptive_st_vol_period,
                annualize=False,
            )
            vol_long_mean = vol_short.shift(1).rolling(
                self.adaptive_st_vol_long_period,
                min_periods=self.adaptive_st_vol_long_period,
            ).mean()
            vol_ratio = vol_short / vol_long_mean.replace(0.0, np.nan)
            high_regime = self._build_vol_regime(vol_ratio)
            self._vol_regime_short = vol_short
            self._vol_regime_long_mean = vol_long_mean
            self._vol_regime_ratio = vol_ratio
            self._vol_regime_high = high_regime

        # Supertrend always remains fixed for the chosen volatility-regime path.
        self._st_line, self._st_bullish = compute_supertrend(
            highs, lows, closes, self.st_atr_period, self.st_multiplier,
        )

        # ATR for exit distance calculation
        self._atr = compute_atr(highs, lows, closes, self.st_atr_period)
        if not regime_series_enabled:
            self._vol_regime_short = None
            self._vol_regime_long_mean = None
            self._vol_regime_ratio = None
            self._vol_regime_high = None

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

        if self.flip_vol_ratio_enabled:
            vol_short = compute_realised_vol(
                closes,
                period=self.flip_vol_ratio_short_period,
                annualize=False,
            )
            vol_long_mean = vol_short.shift(1).rolling(
                self.flip_vol_ratio_long_period,
                min_periods=self.flip_vol_ratio_long_period,
            ).mean()
            self._flip_vol_short = vol_short
            self._flip_vol_long_mean = vol_long_mean
            self._flip_vol_ratio = vol_short / vol_long_mean.replace(0.0, np.nan)
        else:
            self._flip_vol_short = None
            self._flip_vol_long_mean = None
            self._flip_vol_ratio = None

        # Map each 5m timestamp → index of the bucket whose close is the most
        # recent one known as of that bar's close. A bar at ts_5m closes at
        # ts_5m + 5min; the latest bucket whose close <= that time is the one
        # whose start <= (ts_5m + 5min - freq). Using the bar's *own* bucket
        # would let on_bar peek at the bucket's not-yet-known close, which the
        # live bot can never do (it must wait for the bar that completes the
        # bucket). Mapping to the just-closed bucket eliminates that look-ahead
        # and aligns BT with live timing: signals fire on the bar at :25/:55.
        resampled_ts = resampled.index
        self._5m_to_hourly = {}
        for ts_5m in full_data.index:
            target = ts_5m + pd.Timedelta(minutes=5) - self._resample_freq
            idx = resampled_ts.get_indexer([target], method="ffill")[0]
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

        # Mid-interval: map this 5m timestamp to the most recently completed
        # bucket (same rule as in prepare()). Mid-bucket bars carry the prior
        # bucket's signal so on_bar reads no future data.
        if hasattr(self, '_hourly') and self._hourly is not None and len(self._hourly) > 0:
            target = last_5m_ts + pd.Timedelta(minutes=5) - self._resample_freq
            resampled_ts = self._hourly.index
            idx = resampled_ts.get_indexer([target], method="ffill")[0]
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
            "persist_direction": self._persist_direction,
            "persist_flip_hourly_idx": self._persist_flip_hourly_idx,
            "persist_ref_price": self._persist_ref_price,
            "held_flip_direction": self._held_flip_direction,
            "held_flip_price": self._held_flip_price,
            "held_flip_hourly_idx": self._held_flip_hourly_idx,
            "held_flip_stop_pct": self._held_flip_stop_pct,
        }

    def import_state(self, state: dict) -> None:
        if not state:
            return
        self._in_long = state.get("in_long", False)
        self._in_short = state.get("in_short", False)
        self._entry_price = state.get("entry_price", 0.0)
        self._entry_bar = state.get("entry_bar", 0)
        self._bar_count = state.get("bar_count", 0)
        self._pending_long = state.get("pending_long", False)
        self._pending_short = state.get("pending_short", False)
        self._delayed_direction = state.get("delayed_direction")
        self._delayed_confirm_count = state.get("delayed_confirm_count", 0)
        self._hourly_closes_since_entry = state.get("hourly_closes_since_entry", 0)
        self._persist_direction = state.get("persist_direction")
        self._persist_flip_hourly_idx = state.get("persist_flip_hourly_idx", -1)
        self._persist_ref_price = state.get("persist_ref_price", 0.0)
        self._held_flip_direction = state.get("held_flip_direction")
        self._held_flip_price = state.get("held_flip_price", 0.0)
        self._held_flip_hourly_idx = state.get("held_flip_hourly_idx", -1)
        self._held_flip_stop_pct = state.get("held_flip_stop_pct", 0.0)

        self._prev_hourly_idx = state.get("prev_hourly_idx", -1)
        self._prev_st_bullish = state.get("prev_st_bullish")

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
        self._clear_entry_persist()
        self._clear_held_flip()

    def _clear_entry_persist(self) -> None:
        self._persist_direction = None
        self._persist_flip_hourly_idx = -1
        self._persist_ref_price = 0.0

    def _clear_held_flip(self) -> None:
        self._held_flip_direction = None
        self._held_flip_price = 0.0
        self._held_flip_hourly_idx = -1
        self._held_flip_stop_pct = 0.0

    def _arm_entry_persist(self, direction: str, hourly_idx: int) -> None:
        self._persist_direction = direction
        self._persist_flip_hourly_idx = int(hourly_idx)
        self._persist_ref_price = float(self._hourly["close"].iloc[hourly_idx])

    def _arm_held_flip(self, direction: str, hourly_idx: int, price: float, stop_pct: float) -> None:
        self._held_flip_direction = direction
        self._held_flip_hourly_idx = int(hourly_idx)
        self._held_flip_price = float(price)
        self._held_flip_stop_pct = float(stop_pct)

    def _flip_vol_regime_weight(self, hourly_idx: int) -> tuple[float, dict]:
        mode = self.flip_vol_ratio_regime_mode
        meta = {"mode": mode}
        if mode == "fixed":
            meta["reason"] = "fixed"
            return 0.0, meta

        if (
            self._vol_regime_high is None
            or hourly_idx < 0
            or hourly_idx >= len(self._vol_regime_high)
        ):
            meta["reason"] = "vol_regime_unavailable"
            return 0.0, meta

        high_regime = bool(self._vol_regime_high.iloc[hourly_idx])
        meta["high_regime"] = high_regime

        if mode == "squared":
            if (
                self._vol_regime_ratio is None
                or hourly_idx >= len(self._vol_regime_ratio)
            ):
                meta["reason"] = "ratio_unavailable"
                return (1.0 if high_regime else 0.0), meta

            ratio_now = self._vol_regime_ratio.iloc[hourly_idx]
            low_anchor = float(
                min(self.flip_vol_ratio_regime_low_anchor, self.flip_vol_ratio_regime_high_anchor)
            )
            high_anchor = float(
                max(self.flip_vol_ratio_regime_low_anchor, self.flip_vol_ratio_regime_high_anchor)
            )
            meta["reason"] = "ratio_squared"
            meta["ratio"] = (
                None if pd.isna(ratio_now) else round(float(ratio_now), 6)
            )
            meta["low_anchor"] = low_anchor
            meta["high_anchor"] = high_anchor
            if pd.isna(ratio_now):
                return (1.0 if high_regime else 0.0), meta
            if math.isclose(high_anchor, low_anchor):
                return (1.0 if float(ratio_now) >= high_anchor else 0.0), meta
            scale = (float(ratio_now) - low_anchor) / (high_anchor - low_anchor)
            scale = min(max(scale, 0.0), 1.0)
            power = max(self.flip_vol_ratio_regime_power, 0.1)
            meta["power"] = round(float(power), 6)
            return float(scale ** power), meta

        meta["reason"] = "unsupported_mode"
        return 0.0, meta

    def _active_flip_vol_params(self, hourly_idx: int) -> dict:
        active_ratio_min = self.flip_vol_ratio_min
        active_stop_pct = self.flip_vol_ratio_safety_stop_pct
        weight, meta = self._flip_vol_regime_weight(hourly_idx)
        info = {
            "regime_mode": self.flip_vol_ratio_regime_mode,
            "regime_weight": round(float(weight), 6),
            "active_ratio_min": round(float(active_ratio_min), 6),
            "active_stop_pct": round(float(active_stop_pct) * 100.0, 6),
        }

        if self.flip_vol_ratio_regime_mode != "fixed":
            low_ratio = self.flip_vol_ratio_regime_low_min
            high_ratio = self.flip_vol_ratio_regime_high_min
            low_stop = self.flip_vol_ratio_regime_low_stop_pct
            high_stop = self.flip_vol_ratio_regime_high_stop_pct
            active_ratio_min = low_ratio + weight * (high_ratio - low_ratio)
            active_stop_pct = low_stop + weight * (high_stop - low_stop)
            info["active_ratio_min"] = round(float(active_ratio_min), 6)
            info["active_stop_pct"] = round(float(active_stop_pct) * 100.0, 6)
            info["regime_low_ratio_min"] = round(float(low_ratio), 6)
            info["regime_high_ratio_min"] = round(float(high_ratio), 6)
            info["regime_low_stop_pct"] = round(float(low_stop) * 100.0, 6)
            info["regime_high_stop_pct"] = round(float(high_stop) * 100.0, 6)

        info.update(meta)
        info["active_stop_pct_decimal"] = float(active_stop_pct)
        info["active_ratio_min_decimal"] = float(active_ratio_min)
        return info

    def _flip_vol_ratio_info(self, hourly_idx: int) -> dict:
        info = {
            "short_period": self.flip_vol_ratio_short_period,
            "long_period": self.flip_vol_ratio_long_period,
            "ratio_min": self.flip_vol_ratio_min,
        }
        info.update(self._active_flip_vol_params(hourly_idx))
        if (
            not self.flip_vol_ratio_enabled
            or self._flip_vol_ratio is None
            or hourly_idx < 0
            or hourly_idx >= len(self._flip_vol_ratio)
        ):
            return info

        ratio_now = self._flip_vol_ratio.iloc[hourly_idx]
        short_now = self._flip_vol_short.iloc[hourly_idx]
        long_now = self._flip_vol_long_mean.iloc[hourly_idx]
        info["ratio"] = round(float(ratio_now), 6) if not pd.isna(ratio_now) else None
        info["short_vol"] = round(float(short_now), 6) if not pd.isna(short_now) else None
        info["long_mean_vol"] = round(float(long_now), 6) if not pd.isna(long_now) else None
        return info

    def _flip_vol_ratio_allows(self, hourly_idx: int) -> tuple[bool, dict]:
        info = self._flip_vol_ratio_info(hourly_idx)
        if not self.flip_vol_ratio_enabled:
            info["ready"] = True
            return True, info
        if (
            self._flip_vol_ratio is None
            or hourly_idx < 0
            or hourly_idx >= len(self._flip_vol_ratio)
        ):
            info["ready"] = False
            return True, info
        ratio_now = self._flip_vol_ratio.iloc[hourly_idx]
        if pd.isna(ratio_now):
            info["ready"] = False
            return True, info
        info["ready"] = True
        return bool(float(ratio_now) >= info["active_ratio_min_decimal"]), info

    def _held_flip_stop_triggered(self, direction: str, close: float) -> tuple[bool, float | None]:
        if (
            self._held_flip_stop_pct <= 0
            or self._held_flip_price <= 0
            or self._held_flip_direction != direction
        ):
            return False, None
        if direction == "short":
            adverse_move = (self._held_flip_price - close) / self._held_flip_price
        else:
            adverse_move = (close - self._held_flip_price) / self._held_flip_price
        return bool(adverse_move >= self._held_flip_stop_pct), float(adverse_move)

    def _resampled_roc(self, hourly_idx: int) -> float | None:
        """ROC on resampled closes: (close[idx] - close[idx-lb]) / close[idx-lb]."""
        lb = self.entry_persist_roc_lookback
        if lb < 1 or hourly_idx < lb:
            return None
        c0 = self._hourly["close"].iloc[hourly_idx]
        c1 = self._hourly["close"].iloc[hourly_idx - lb]
        if pd.isna(c0) or pd.isna(c1) or c1 == 0:
            return None
        return float((c0 - c1) / c1)

    def _persist_evaluate(
        self,
        hourly_idx: int,
        close: float,
        pv: PortfolioView,
        st_bullish: bool,
        indicators: dict,
    ) -> tuple[str, Action | None]:
        """Returns (mode, action). mode is 'pending', 'entered', or 'continue'."""
        if self._persist_direction is None:
            return "continue", None

        direction = self._persist_direction
        expected_bull = direction == "long"
        if st_bullish != expected_bull:
            self._clear_entry_persist()
            return "continue", None

        span = hourly_idx - self._persist_flip_hourly_idx + 1
        if span > self.entry_persist_max_bars:
            self._clear_entry_persist()
            return "continue", None

        if self._persist_ref_price <= 0:
            self._clear_entry_persist()
            return "continue", None

        h_close = float(self._hourly["close"].iloc[hourly_idx])
        drift = abs(h_close - self._persist_ref_price) / self._persist_ref_price
        if drift > self.entry_persist_max_price_drift:
            self._clear_entry_persist()
            return "continue", None

        roc = self._resampled_roc(hourly_idx)
        if roc is None:
            return "pending", Action(
                ActionType.HOLD,
                details={"reason": "entry_persist_roc_warmup", "indicators": indicators},
            )

        if (direction == "long" and roc > 0) or (direction == "short" and roc < 0):
            self._clear_entry_persist()
            qty = pv.cash * 0.9999 / close
            if qty <= 0:
                return "pending", Action(
                    ActionType.HOLD,
                    details={"reason": "entry_persist_no_cash", "indicators": indicators},
                )
            if direction == "long":
                self._in_long = True
                self._entry_price = close
                self._entry_bar = self._bar_count
                self._hourly_closes_since_entry = 0
                return "entered", Action(
                    ActionType.BUY,
                    qty,
                    {
                        "entry_reason": "st_flip_bullish_persist",
                        "roc": round(roc, 6),
                        "indicators": indicators,
                    },
                )
            self._in_short = True
            self._entry_price = close
            self._entry_bar = self._bar_count
            self._hourly_closes_since_entry = 0
            return "entered", Action(
                ActionType.SHORT,
                qty,
                {
                    "entry_reason": "st_flip_bearish_persist",
                    "roc": round(roc, 6),
                    "indicators": indicators,
                },
            )

        return "pending", Action(
            ActionType.HOLD,
            details={
                "reason": "entry_persist_wait_roc",
                "roc": round(roc, 6),
                "indicators": indicators,
            },
        )

    def _gap_gate_entry(
        self,
        hourly_idx: int,
        prev_hourly_idx: int,
        current_st_bullish: bool,
        current_close: float,
    ) -> tuple[bool, str, dict]:
        """Gate an entry based on the gap between the previous hourly evaluation and now.

        Normal operation runs with gap == 1 and the nominal fast-path always passes.
        After a restart or connectivity gap, applies tiered staleness checks against
        the most recent ST transition to decide whether the entry is still actionable.
        Returns (allowed, reason, info). reason is the diagnostics label when blocked.
        """
        info: dict = {}
        if prev_hourly_idx < 0:
            return True, "", info
        gap = hourly_idx - prev_hourly_idx
        info["gap"] = int(gap)
        if gap <= self.entry_gap_nominal_bars:
            return True, "", info
        if gap > self.entry_gap_extended_bars:
            return False, "stale_flip_gap_too_large", info
        # Scan backward for the most recent ST transition. The flip must lie
        # inside (prev_hourly_idx, hourly_idx], so bound the scan by gap.
        intended_flip_idx = None
        for offset in range(1, gap + 1):
            i = hourly_idx - offset
            if i < 0:
                break
            if bool(self._st_bullish.iloc[i]) != current_st_bullish:
                intended_flip_idx = i + 1
                break
        if intended_flip_idx is None:
            return False, "stale_flip_no_transition", info
        flip_age = hourly_idx - intended_flip_idx
        info["flip_age"] = int(flip_age)
        if flip_age <= self.entry_gap_nominal_bars:
            return True, "", info
        flip_price = float(self._hourly["close"].iloc[intended_flip_idx])
        if flip_price <= 0 or pd.isna(flip_price):
            return False, "stale_flip_invalid_price", info
        drift_pct = abs(current_close / flip_price - 1.0) * 100.0
        info["drift_pct"] = round(drift_pct, 3)
        if drift_pct > self.entry_gap_price_drift_pct:
            return False, "stale_flip_drift", info
        return True, "", info

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

    def warmup_bar(self, date, row, _data_so_far, _is_last_bar) -> None:
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
            self._clear_entry_persist()

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

        # Check if this is an hourly close (new hourly bar). Capture the old
        # value before the update so the entry-gap gate can compute the gap.
        prev_hourly_idx_before = int(getattr(self, "_prev_hourly_idx", -1))
        hourly_idx_int = int(hourly_idx)
        is_hourly_close = hourly_idx_int != prev_hourly_idx_before
        self._prev_hourly_idx = hourly_idx_int

        # Track hourly closes since entry for min-hold logic
        if is_hourly_close and (self._in_long or self._in_short):
            self._hourly_closes_since_entry += 1

        flip_ratio_info = self._flip_vol_ratio_info(int(hourly_idx))
        indicators = {
            "is_hourly_close": is_hourly_close,
            "hourly_idx": int(hourly_idx),
            "close": float(close),
            "st_line": float(st_line),
            "st_bullish": st_bullish,
            "atr": float(atr),
            "hmacd_hist": float(hmacd_hist) if not pd.isna(hmacd_hist) else None,
            "dist_to_st_atr": float((close - st_line) / atr) if atr > 0 else 0,
            "flip_vol_ratio": flip_ratio_info,
        }

        # --- PENDING FLIP ENTRY (enter opposite side after exit) ---

        if self._pending_long and not self._in_long and not self._in_short:
            self._pending_long = False
            if not self._confirm_agrees(hourly_idx, "long"):
                pass  # stay flat — confirmation ST disagrees
            elif self.entry_persist_max_bars > 0:
                self._arm_entry_persist("long", hourly_idx)
                _pm, pact = self._persist_evaluate(
                    hourly_idx, close, pv, st_bullish, indicators,
                )
                return pact if pact is not None else Action(
                    ActionType.HOLD,
                    details={"reason": "entry_persist_cleared", "indicators": indicators},
                )
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
            elif self.entry_persist_max_bars > 0:
                self._arm_entry_persist("short", hourly_idx)
                _pm, pact = self._persist_evaluate(
                    hourly_idx, close, pv, st_bullish, indicators,
                )
                return pact if pact is not None else Action(
                    ActionType.HOLD,
                    details={"reason": "entry_persist_cleared", "indicators": indicators},
                )
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

            if st_bullish:
                if self._held_flip_direction == "short":
                    self._clear_held_flip()
                return Action(ActionType.HOLD, details={"reason": "holding_long", "indicators": indicators})

            # ST is bearish. Either reject this flip and keep holding, or if a
            # prior rejection already happened, only exit on the safety stop.
            if is_hourly_close and self.min_hold_hours > 0 and self._hourly_closes_since_entry < self.min_hold_hours:
                return Action(ActionType.HOLD, details={
                    "reason": "min_hold_suppressed",
                    "hourly_closes": self._hourly_closes_since_entry,
                    "indicators": indicators,
                })

            if self._held_flip_direction == "short":
                stop_hit, adverse_move = self._held_flip_stop_triggered("short", float(close))
                if stop_hit:
                    pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                    self._in_long = False
                    self._hourly_closes_since_entry = 0
                    self._prev_st_bullish = st_bullish
                    self._clear_held_flip()
                    return Action(ActionType.SELL, pv.position_qty, {
                        "exit_reason": "st_flip_ratio_safety",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "adverse_move_pct": round(adverse_move * 100.0, 4) if adverse_move is not None else None,
                        "indicators": indicators,
                    })
                return Action(ActionType.HOLD, details={
                    "reason": "holding_long_rejected_flip",
                    "indicators": indicators,
                })

            if not is_hourly_close:
                return Action(ActionType.HOLD, details={"reason": "holding_long_pending_flip", "indicators": indicators})

            allowed, ratio_info = self._flip_vol_ratio_allows(int(hourly_idx))
            indicators["flip_vol_ratio"] = ratio_info
            if not allowed:
                hold_stop_pct = ratio_info.get("active_stop_pct_decimal", 0.0)
                indicators["flip_vol_ratio"]["held_stop_pct"] = round(hold_stop_pct * 100.0, 4)
                self._arm_held_flip("short", int(hourly_idx), float(close), hold_stop_pct)
                return Action(ActionType.HOLD, details={
                    "reason": "st_flip_ratio_rejected_hold",
                    "indicators": indicators,
                })

            pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
            self._in_long = False
            self._hourly_closes_since_entry = 0
            self._pending_short = True  # flip to short on next bar
            self._clear_held_flip()
            return Action(ActionType.SELL, pv.position_qty, {
                "exit_reason": "st_flip",
                "bars_held": bars_held,
                "pnl_pct": round(pnl_pct, 2),
                "indicators": indicators,
            })

        if self._in_short:
            bars_held = self._bar_count - self._entry_bar

            if not st_bullish:
                if self._held_flip_direction == "long":
                    self._clear_held_flip()
                return Action(ActionType.HOLD, details={"reason": "holding_short", "indicators": indicators})

            if is_hourly_close and self.min_hold_hours > 0 and self._hourly_closes_since_entry < self.min_hold_hours:
                return Action(ActionType.HOLD, details={
                    "reason": "min_hold_suppressed",
                    "hourly_closes": self._hourly_closes_since_entry,
                    "indicators": indicators,
                })

            if self._held_flip_direction == "long":
                stop_hit, adverse_move = self._held_flip_stop_triggered("long", float(close))
                if stop_hit:
                    pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                    self._in_short = False
                    self._hourly_closes_since_entry = 0
                    self._prev_st_bullish = st_bullish
                    self._clear_held_flip()
                    return Action(ActionType.COVER, pv.short_qty, {
                        "exit_reason": "st_flip_ratio_safety",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "adverse_move_pct": round(adverse_move * 100.0, 4) if adverse_move is not None else None,
                        "indicators": indicators,
                    })
                return Action(ActionType.HOLD, details={
                    "reason": "holding_short_rejected_flip",
                    "indicators": indicators,
                })

            if not is_hourly_close:
                return Action(ActionType.HOLD, details={"reason": "holding_short_pending_flip", "indicators": indicators})

            allowed, ratio_info = self._flip_vol_ratio_allows(int(hourly_idx))
            indicators["flip_vol_ratio"] = ratio_info
            if not allowed:
                hold_stop_pct = ratio_info.get("active_stop_pct_decimal", 0.0)
                indicators["flip_vol_ratio"]["held_stop_pct"] = round(hold_stop_pct * 100.0, 4)
                self._arm_held_flip("long", int(hourly_idx), float(close), hold_stop_pct)
                return Action(ActionType.HOLD, details={
                    "reason": "st_flip_ratio_rejected_hold",
                    "indicators": indicators,
                })

            pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
            self._in_short = False
            self._hourly_closes_since_entry = 0
            self._pending_long = True  # flip to long on next bar
            self._clear_held_flip()
            return Action(ActionType.COVER, pv.short_qty, {
                "exit_reason": "st_flip",
                "bars_held": bars_held,
                "pnl_pct": round(pnl_pct, 2),
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

        prev_bull = self._prev_st_bullish

        if (
            self.entry_persist_max_bars > 0
            and not self._in_long
            and not self._in_short
            and self._persist_direction is not None
        ):
            mode, pact = self._persist_evaluate(
                hourly_idx, close, pv, st_bullish, indicators,
            )
            if mode in ("pending", "entered"):
                self._prev_st_bullish = st_bullish
                if pact is None:
                    return Action(
                        ActionType.HOLD,
                        details={"reason": "entry_persist_internal", "indicators": indicators},
                    )
                return pact

        self._prev_st_bullish = st_bullish

        if prev_bull is None:
            return Action(ActionType.HOLD, details={"reason": "first_bar", "indicators": indicators})

        # Long entry: ST flipped from bearish to bullish
        if st_bullish and not prev_bull:
            if self._confirm_agrees(hourly_idx, "long"):
                if self.entry_persist_max_bars > 0:
                    self._arm_entry_persist("long", hourly_idx)
                    _em, eact = self._persist_evaluate(
                        hourly_idx, close, pv, st_bullish, indicators,
                    )
                    return eact if eact is not None else Action(
                        ActionType.HOLD,
                        details={"reason": "entry_persist_cleared", "indicators": indicators},
                    )
                if self.entry_delay_hours > 0:
                    self._delayed_direction = "long"
                    self._delayed_confirm_count = 1
                else:
                    allowed, gate_reason, gate_info = self._gap_gate_entry(
                        hourly_idx, prev_hourly_idx_before, True, float(close),
                    )
                    if not allowed:
                        return Action(ActionType.HOLD, details={
                            "reason": gate_reason,
                            "gap_info": gate_info,
                            "indicators": indicators,
                        })
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        self._in_long = True
                        self._entry_price = close
                        self._entry_bar = self._bar_count
                        self._hourly_closes_since_entry = 0
                        return Action(ActionType.BUY, qty, {
                            "entry_reason": "st_flip_bullish",
                            "gap_info": gate_info,
                            "indicators": indicators,
                        })

        # Short entry: ST flipped from bullish to bearish
        if not st_bullish and prev_bull:
            if self._confirm_agrees(hourly_idx, "short"):
                if self.entry_persist_max_bars > 0:
                    self._arm_entry_persist("short", hourly_idx)
                    _em, eact = self._persist_evaluate(
                        hourly_idx, close, pv, st_bullish, indicators,
                    )
                    return eact if eact is not None else Action(
                        ActionType.HOLD,
                        details={"reason": "entry_persist_cleared", "indicators": indicators},
                    )
                if self.entry_delay_hours > 0:
                    self._delayed_direction = "short"
                    self._delayed_confirm_count = 1
                else:
                    allowed, gate_reason, gate_info = self._gap_gate_entry(
                        hourly_idx, prev_hourly_idx_before, False, float(close),
                    )
                    if not allowed:
                        return Action(ActionType.HOLD, details={
                            "reason": gate_reason,
                            "gap_info": gate_info,
                            "indicators": indicators,
                        })
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        self._in_short = True
                        self._entry_price = close
                        self._entry_bar = self._bar_count
                        self._hourly_closes_since_entry = 0
                        return Action(ActionType.SHORT, qty, {
                            "entry_reason": "st_flip_bearish",
                            "gap_info": gate_info,
                            "indicators": indicators,
                        })

        # When flat and confirmation ST now agrees with primary ST direction,
        # enter if not already in a position (catches deferred entries).
        if self.confirm_st_atr_period > 0 and not self._in_long and not self._in_short \
                and self._delayed_direction is None \
                and self._persist_direction is None \
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
