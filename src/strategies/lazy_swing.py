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
    compute_bollinger,
    compute_hma,
    compute_hmacd,
    compute_keltner,
    compute_realised_vol,
    compute_supertrend,
)
from .macd_rsi_advanced import compute_adx, compute_atr


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

        # ---- Profit-protect gate (idea #6 phase 0 result) ----
        # When vol_ratio rejects an ST flip, also check the current
        # unrealized gain (entry → rejection-bar close). If gain ≥ this
        # threshold (in %), honour the flip anyway (lock in profit; don't
        # risk the safety-stop wiping it out). Default 1e9 = disabled.
        self.flip_protect_min_gain_pct = float(
            config.get("flip_protect_min_gain_pct", 1e9)
        ) / 100.0

        # ---- ER (efficiency ratio) gate (idea 6 step 2.b) ----
        # When vol_ratio rejects, compute Kaufman ER on the last M 5m bars
        # (optionally excluding the last N bars). If ER ≥ threshold, the
        # market is in a clean trend → honour the flip anyway. Default
        # disabled (threshold 1e9).
        self.flip_er_gate_threshold = float(
            config.get("flip_er_gate_threshold", 1e9)
        )
        self.flip_er_gate_period = int(config.get("flip_er_gate_period", 48))
        self.flip_er_gate_exclude_bars = int(
            config.get("flip_er_gate_exclude_bars", 0)
        )

        # ---- Profit-Lock on Consolidation (EXIT-NEXT-FLIP) ----
        # When in position with ≥ min_gain_pct open profit AND we observe N
        # consecutive hourly closes where vol_ratio is below its active
        # threshold, arm a flag. On the next ST flip the flag bypasses the
        # vol_ratio gate (take the flip regardless). Optional knobs:
        #   - cancel_on_recovery: if True, counter resets to 0 whenever
        #     vol_ratio rises back above threshold. Flag also dis-arms.
        #   - action: "flip" (default, full reverse like normal flip) or
        #     "close_only" (close the position, stay flat).
        self.profit_lock_enabled = bool(config.get("profit_lock_enabled", False))
        self.profit_lock_min_gain_pct = float(
            config.get("profit_lock_min_gain_pct", 1.5)
        ) / 100.0
        self.profit_lock_low_vol_bars = int(
            config.get("profit_lock_low_vol_bars", 8)
        )
        self.profit_lock_cancel_on_recovery = bool(
            config.get("profit_lock_cancel_on_recovery", False)
        )
        self.profit_lock_action = str(
            config.get("profit_lock_action", "flip")
        ).lower()

        # Fast exit: exit on the 5m bar when price crosses the ST line (before
        # the 30m bar closes). No reverse entry — wait fast_exit_cooldown_bars
        # 5m bars, then re-enter if price is back on the correct side of ST.
        self.fast_exit_enabled = bool(config.get("fast_exit_enabled", False))
        self.fast_exit_cooldown_bars = int(config.get("fast_exit_cooldown_bars", 6))
        # Require this many consecutive 5m closes on wrong side before exiting
        self.fast_exit_min_bars = max(1, int(config.get("fast_exit_min_bars", 1)))
        # RVOL gate: only fast-exit when 5m realised-vol ratio >= this threshold.
        # When > 0 this replaces the M-bars counter entirely.
        # short/long periods are in 5m bars (24 = 2h; 2016 = 1 week).
        self.fast_exit_rvol_min_ratio = float(config.get("fast_exit_rvol_min_ratio", 0.0))
        self.fast_exit_rvol_short_period = int(config.get("fast_exit_rvol_short_period", 24))
        self.fast_exit_rvol_long_period = int(config.get("fast_exit_rvol_long_period", 2016))
        # Regime-adaptive threshold: interpolate between low_min (low-vol regime)
        # and high_min (high-vol regime) using the existing 30m _flip_vol_regime_weight.
        # When both equal fast_exit_rvol_min_ratio the behaviour is unchanged.
        self.fast_exit_rvol_low_min = float(config.get("fast_exit_rvol_low_min", self.fast_exit_rvol_min_ratio))
        self.fast_exit_rvol_high_min = float(config.get("fast_exit_rvol_high_min", self.fast_exit_rvol_min_ratio))
        # Require fast_exit_cooldown_bars consecutive bars on correct side before re-entering.
        self.fast_exit_reentry_confirm = bool(config.get("fast_exit_reentry_confirm", False))
        # Idea 2: when True AND RVOL is in use, require BOTH min_bars AND RVOL
        # (today RVOL bypasses min_bars). Backward-compatible default False.
        self.fast_exit_min_bars_with_rvol = bool(config.get("fast_exit_min_bars_with_rvol", False))
        # Idea 3: 5m ER confirm — only fast_exit when Kaufman ER over the last
        # M 5m closes ≥ threshold (suppress fast_exit during chop / mean revert).
        # Sentinel 1e9 = disabled (always pass). 24 ≈ 2h, 48 ≈ 4h.
        self.fast_exit_er_gate_period = int(config.get("fast_exit_er_gate_period", 24))
        self.fast_exit_er_gate_threshold = float(config.get("fast_exit_er_gate_threshold", 1e9))
        # Idea 1: proactive (pre-cross) exit when close is within k×ATR of ST
        # on the favorable side AND the current 5m bar is against the position.
        # Still requires RVOL ≥ high_min (high-conviction) to fire pre-cross.
        # 0.0 = disabled.
        self.fast_exit_proactive_atr_mult = float(config.get("fast_exit_proactive_atr_mult", 0.0))
        # Hybrid Idea 1+3: when set (<1e8), the proactive (pre-cross) trigger
        # ALSO requires 5m Kaufman ER over fast_exit_er_gate_period bars ≥ this
        # threshold. Crossed-line triggers are NOT gated by this — they keep
        # their existing RVOL behaviour. Sentinel 1e9 = disabled.
        self.fast_exit_proactive_er_threshold = float(
            config.get("fast_exit_proactive_er_threshold", 1e9)
        )

        # Flat-realign safety net: after N consecutive hourly closes spent
        # genuinely flat (no position, pending, fast_exit cooldown, delayed,
        # or persist state), align with the current ST direction if the
        # vol-ratio gate allows. This recovers from cases where the implicit
        # _prev_st_bullish chop filter (see note on _prev_st_bullish init)
        # leaves us sitting out a clear ST regime indefinitely. 0 = disabled.
        # Default is 0 (disabled): cross-year sweep showed that every N≥2 adds
        # +3–6pp on 2026 but costs -137pp to -203pp on 2025 (regression grows
        # with N), so the realign fires at late/exhausted points in the move and
        # the natural chop filter is doing real work that should not be overridden.
        self.flat_realign_hourly_closes = int(config.get("flat_realign_hourly_closes", 0))

        # Regime-gated trailing stop experiment. Disabled by default; when
        # enabled, the stop only fires when the current regime allows it.
        self.regime_trail_enabled = bool(config.get("regime_trail_enabled", False))
        self.regime_trail_mode = str(config.get("regime_trail_mode", "not_momentum")).lower()
        self.trail_stop_pct = float(config.get("trail_stop_pct", 0.0)) / 100.0
        self.trail_stop_min_gain_pct = float(
            config.get("trail_stop_min_gain_pct", 2.0)
        ) / 100.0
        self.trail_stop_reentry_pct = float(
            config.get("trail_stop_reentry_pct", 0.5)
        ) / 100.0
        self.trail_stop_cooldown_bars = int(config.get("trail_stop_cooldown_bars", 0))
        self.trail_stop_atr_multiple = float(config.get("trail_stop_atr_multiple", 0.0))
        self.trail_stop_reentry_enabled = bool(config.get("trail_stop_reentry_enabled", True))
        self.trail_stop_exit_on_signal = bool(config.get("trail_stop_exit_on_signal", False))

        self.regime_momentum_adx_period = int(config.get("regime_momentum_adx_period", 14))
        self.regime_momentum_adx_min = float(config.get("regime_momentum_adx_min", 40.0))
        self.regime_momentum_er_period = int(config.get("regime_momentum_er_period", 24))
        self.regime_momentum_er_min = float(config.get("regime_momentum_er_min", 0.40))
        self.regime_momentum_adx_delta_bars = int(
            config.get("regime_momentum_adx_delta_bars", 2)
        )
        self.regime_momentum_adx_delta_min = float(
            config.get("regime_momentum_adx_delta_min", 1.0)
        )
        self.regime_momentum_vol_period = int(config.get("regime_momentum_vol_period", 24))
        self.regime_momentum_vol_long_period = int(
            config.get("regime_momentum_vol_long_period", 336)
        )
        self.regime_momentum_vol_ratio_max = float(
            config.get("regime_momentum_vol_ratio_max", 1.0)
        )
        self.regime_stretch_kc_z_min = float(config.get("regime_stretch_kc_z_min", 1.0))
        self.regime_stretch_bb_z_min = float(config.get("regime_stretch_bb_z_min", 1.5))
        self.regime_decay_adx_max = float(config.get("regime_decay_adx_max", 20.0))
        self.regime_decay_er_max = float(config.get("regime_decay_er_max", 0.25))
        self.regime_decay_adx_delta_max = float(
            config.get("regime_decay_adx_delta_max", -2.0)
        )
        self.regime_exhaustion_stretch_lookback = int(
            config.get("regime_exhaustion_stretch_lookback", 3)
        )
        self.regime_exhaustion_kc_z_min = float(
            config.get("regime_exhaustion_kc_z_min", 2.0)
        )
        self.regime_exhaustion_bb_z_min = float(
            config.get("regime_exhaustion_bb_z_min", 3.0)
        )
        self.regime_exhaustion_adx_lookback = int(
            config.get("regime_exhaustion_adx_lookback", 2)
        )
        self.regime_exhaustion_prev_adx_min = float(
            config.get("regime_exhaustion_prev_adx_min", 30.0)
        )
        self.regime_exhaustion_adx_drop_pct = float(
            config.get("regime_exhaustion_adx_drop_pct", 2.5)
        )

        # Profit-exit indicator params (macd_exit / ema_trail modes)
        self.profit_exit_macd_fast = int(config.get("profit_exit_macd_fast", 12))
        self.profit_exit_macd_slow = int(config.get("profit_exit_macd_slow", 26))
        self.profit_exit_macd_signal_period = int(config.get("profit_exit_macd_signal_period", 9))
        self.profit_exit_macd_condition = str(config.get("profit_exit_macd_condition", "cross"))
        self.profit_exit_macd_histogram_bars = int(config.get("profit_exit_macd_histogram_bars", 2))
        # When > 0, histogram fires only if (in addition to consecutive drops)
        # the histogram has lost this fraction of its peak since entry. E.g.
        # 0.5 → current must be ≤ peak * 0.5 (long) or ≥ trough * 0.5 (short).
        self.profit_exit_macd_histogram_peak_drop_pct = float(
            config.get("profit_exit_macd_histogram_peak_drop_pct", 0.0)
        )
        self.profit_exit_ema_period = int(config.get("profit_exit_ema_period", 13))

        # combined_bc params: window N (5m bars) within which the second signal
        # (B's adx_exhaustion or C's macd_exit) must fire after the first to
        # trigger an exit.
        self.combined_bc_window_bars = int(config.get("combined_bc_window_bars", 6))
        # When >0 AND trail_stop_exit_on_signal=False: after BC triggers, keep
        # the trail "exit-armed" for this many additional 5m bars; exit on any
        # bar within the window where giveback ≥ trail_stop_pct.
        # 0 = legacy (giveback must be met on the trigger bar itself).
        self.trail_stop_giveback_window_bars = int(
            config.get("trail_stop_giveback_window_bars", 0)
        )

        # Resample frequency offset (cached for boundary checks)
        self._resample_freq = pd.tseries.frequencies.to_offset(self.resample_interval)

        # State
        self._in_long = False
        self._in_short = False
        self._entry_price = 0.0
        self._entry_bar = 0
        self._peak_since_entry = 0.0
        self._trough_since_entry = 0.0
        self._bar_count = 0
        # NOTE on _prev_st_bullish staleness (load-bearing for edge):
        # _prev_st_bullish is only refreshed when the entry logic runs to the
        # bottom (flat at hourly close, no pending/fast_exit/delayed/persist
        # state). The in_long/in_short branches return early at "holding_*",
        # and pending/fast_exit_reentry entry paths don't update it either.
        # During chained fast_exit + reentry cycles (typical of choppy regimes)
        # the value stays frozen at whatever it was many cycles ago. After a
        # cooldown abandon, the resulting prev_bull == st_bullish state means
        # the flip-detection in decide_action silently misses the immediate
        # post-abandon flip. Empirically this acts as a "skip the flip after
        # chop" filter and is part of the strategy's edge in choppy regimes.
        # For clean cycles (one ST flip → fast_exit → abandon), prev_bull was
        # set correctly at entry and the flip IS honored on abandon.
        # The flat_realign_hourly_closes safety net catches cases where this
        # filter would leave the strategy stranded flat through a clear ST
        # regime (see end of decide_action).
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

        # Profit-lock on consolidation state
        self._low_vol_bars_consec: int = 0
        self._profit_lock_armed: bool = False

        # Fast exit state
        self._fast_exit_cooldown_left: int = 0
        self._fast_exit_direction: str = ""  # "long" or "short" or ""
        self._fast_exit_consec_bars: int = 0  # consecutive 5m bars on wrong side of ST
        self._fast_exit_reentry_consec: int = 0  # consecutive bars recovered (re-entry confirm)
        self._fast_exit_rvol: pd.Series | None = None  # 5m RVOL ratio series

        # Flat-realign counter (see flat_realign_hourly_closes)
        self._flat_realign_consec: int = 0

        # Regime-trailing-stop state
        self._regime_adx: pd.Series | None = None
        self._regime_adx_delta: pd.Series | None = None
        self._regime_adx_pct_change: pd.Series | None = None
        self._regime_er: pd.Series | None = None
        self._regime_vol_ratio: pd.Series | None = None
        self._regime_bb_abs_z: pd.Series | None = None
        self._regime_kc_abs_z: pd.Series | None = None
        self._regime_bb_abs_z_recent: pd.Series | None = None
        self._regime_kc_abs_z_recent: pd.Series | None = None
        self._profit_exit_macd_line: pd.Series | None = None
        self._profit_exit_macd_signal_series: pd.Series | None = None
        self._profit_exit_macd_hist: pd.Series | None = None
        self._profit_exit_ema: pd.Series | None = None
        self._trail_exit_direction: str = ""
        self._trail_exit_price: float = 0.0
        self._trail_exit_cooldown_left: int = 0

        # combined_bc state: flag armed by first signal (B or C), waiting for
        # the other within combined_bc_window_bars 5m bars.
        self._combined_bc_armed_by: str | None = None
        self._combined_bc_armed_at_bar: int = -1
        self._combined_bc_last_b_eval_hourly: int = -1
        self._combined_bc_last_c_eval_hourly: int = -1
        # Exit-armed bar (set when BC triggers under exit_on_signal=False; trail
        # remains "exit-allowed" for trail_stop_giveback_window_bars after).
        self._combined_bc_exit_armed_at_bar: int = -1

        # MACD histogram peak/trough tracker (for peak-drop filter).
        # For long: tracks max (most-positive) histogram value since entry.
        # For short: tracks min (most-negative) histogram value since entry.
        self._macd_hist_peak: float | None = None

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

    @staticmethod
    def _efficiency_ratio(closes: pd.Series, period: int) -> pd.Series:
        direction = (closes - closes.shift(period)).abs()
        volatility = closes.diff().abs().rolling(period).sum()
        return direction / volatility.replace(0.0, np.nan)

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

        if self.regime_trail_enabled:
            self._regime_adx = compute_adx(
                highs, lows, closes, self.regime_momentum_adx_period,
            )
            self._regime_adx_delta = self._regime_adx.diff(
                self.regime_momentum_adx_delta_bars
            )
            prev_exhaustion_adx = self._regime_adx.shift(
                self.regime_exhaustion_adx_lookback
            )
            self._regime_adx_pct_change = (
                self._regime_adx / prev_exhaustion_adx.replace(0.0, np.nan) - 1.0
            ) * 100.0
            self._regime_er = self._efficiency_ratio(
                closes, self.regime_momentum_er_period,
            )
            regime_vol_short = compute_realised_vol(
                closes,
                period=self.regime_momentum_vol_period,
                annualize=False,
            )
            regime_vol_long = regime_vol_short.shift(1).rolling(
                self.regime_momentum_vol_long_period,
                min_periods=self.regime_momentum_vol_long_period,
            ).mean()
            self._regime_vol_ratio = regime_vol_short / regime_vol_long.replace(0.0, np.nan)

            bb_upper, bb_mid, _bb_lower = compute_bollinger(closes, 20, 2.0)
            bb_std = (bb_upper - bb_mid) / 2.0
            self._regime_bb_abs_z = ((closes - bb_mid) / bb_std.replace(0.0, np.nan)).abs()
            kc_upper, kc_mid, kc_lower = compute_keltner(highs, lows, closes, 20, 20, 1.5)
            kc_half_width = (kc_upper - kc_lower) / 2.0
            self._regime_kc_abs_z = (
                (closes - kc_mid) / kc_half_width.replace(0.0, np.nan)
            ).abs()
            stretch_window = max(self.regime_exhaustion_stretch_lookback, 1)
            self._regime_bb_abs_z_recent = self._regime_bb_abs_z.rolling(
                stretch_window, min_periods=1
            ).max()
            self._regime_kc_abs_z_recent = self._regime_kc_abs_z.rolling(
                stretch_window, min_periods=1
            ).max()
        else:
            self._regime_adx = None
            self._regime_adx_delta = None
            self._regime_adx_pct_change = None
            self._regime_er = None
            self._regime_vol_ratio = None
            self._regime_bb_abs_z = None
            self._regime_kc_abs_z = None
            self._regime_bb_abs_z_recent = None
            self._regime_kc_abs_z_recent = None

        # Profit-exit indicators (computed only when the mode needs them)
        if self.regime_trail_enabled and self.regime_trail_mode in ("macd_exit", "combined_bc"):
            ema_fast = closes.ewm(span=self.profit_exit_macd_fast, adjust=False).mean()
            ema_slow = closes.ewm(span=self.profit_exit_macd_slow, adjust=False).mean()
            self._profit_exit_macd_line = ema_fast - ema_slow
            self._profit_exit_macd_signal_series = self._profit_exit_macd_line.ewm(
                span=self.profit_exit_macd_signal_period, adjust=False
            ).mean()
            self._profit_exit_macd_hist = (
                self._profit_exit_macd_line - self._profit_exit_macd_signal_series
            )
        elif self.regime_trail_enabled and self.regime_trail_mode == "ema_trail":
            self._profit_exit_ema = closes.ewm(
                span=self.profit_exit_ema_period, adjust=False
            ).mean()
        else:
            self._profit_exit_macd_line = None
            self._profit_exit_macd_signal_series = None
            self._profit_exit_macd_hist = None
            self._profit_exit_ema = None

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

        # 5m RVOL ratio for fast-exit gate (computed on raw 5m closes)
        _fe_rvol_needed = self.fast_exit_enabled and (
            self.fast_exit_rvol_min_ratio > 0
            or self.fast_exit_rvol_low_min > 0
            or self.fast_exit_rvol_high_min > 0
        )
        if _fe_rvol_needed:
            raw_closes = full_data["close"]
            fe_vol_short = compute_realised_vol(raw_closes, period=self.fast_exit_rvol_short_period)
            fe_vol_long_mean = fe_vol_short.shift(1).rolling(
                self.fast_exit_rvol_long_period,
                min_periods=self.fast_exit_rvol_long_period,
            ).mean()
            self._fast_exit_rvol = fe_vol_short / fe_vol_long_mean.replace(0.0, np.nan)
        else:
            self._fast_exit_rvol = None

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
            "peak_since_entry": self._peak_since_entry,
            "trough_since_entry": self._trough_since_entry,
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
            "trail_exit_direction": self._trail_exit_direction,
            "trail_exit_price": self._trail_exit_price,
            "trail_exit_cooldown_left": self._trail_exit_cooldown_left,
        }

    def import_state(self, state: dict) -> None:
        if not state:
            return
        self._in_long = state.get("in_long", False)
        self._in_short = state.get("in_short", False)
        self._entry_price = state.get("entry_price", 0.0)
        self._entry_bar = state.get("entry_bar", 0)
        self._peak_since_entry = state.get("peak_since_entry", self._entry_price)
        self._trough_since_entry = state.get("trough_since_entry", self._entry_price)
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
        self._trail_exit_direction = state.get("trail_exit_direction", "")
        self._trail_exit_price = state.get("trail_exit_price", 0.0)
        self._trail_exit_cooldown_left = state.get("trail_exit_cooldown_left", 0)

        self._prev_hourly_idx = state.get("prev_hourly_idx", -1)
        self._prev_st_bullish = state.get("prev_st_bullish")

    def reset_position(self) -> None:
        self._in_long = False
        self._in_short = False
        self._clear_position_state()
        self._prev_st_bullish = None
        self._pending_long = False
        self._pending_short = False
        self._delayed_direction = None
        self._delayed_confirm_count = 0
        self._hourly_closes_since_entry = 0
        self._clear_entry_persist()
        self._clear_held_flip()
        self._clear_fast_exit()
        self._clear_trail_exit()
        self._flat_realign_consec = 0

    def _clear_fast_exit(self) -> None:
        self._fast_exit_cooldown_left = 0
        self._fast_exit_direction = ""
        self._fast_exit_consec_bars = 0
        self._fast_exit_reentry_consec = 0

    def _clear_entry_persist(self) -> None:
        self._persist_direction = None
        self._persist_flip_hourly_idx = -1
        self._persist_ref_price = 0.0

    def _clear_trail_exit(self) -> None:
        self._trail_exit_direction = ""
        self._trail_exit_price = 0.0
        self._trail_exit_cooldown_left = 0

    def _record_position_entry(self, price: float) -> None:
        self._entry_price = float(price)
        self._entry_bar = self._bar_count
        self._hourly_closes_since_entry = 0
        self._peak_since_entry = float(price)
        self._trough_since_entry = float(price)
        self._clear_trail_exit()
        self._combined_bc_armed_by = None
        self._combined_bc_armed_at_bar = -1
        self._combined_bc_exit_armed_at_bar = -1
        self._macd_hist_peak = None
        self._low_vol_bars_consec = 0
        self._profit_lock_armed = False

    def _clear_position_state(self) -> None:
        self._entry_price = 0.0
        self._entry_bar = 0
        self._hourly_closes_since_entry = 0
        self._peak_since_entry = 0.0
        self._trough_since_entry = 0.0
        self._combined_bc_armed_by = None
        self._combined_bc_armed_at_bar = -1
        self._combined_bc_exit_armed_at_bar = -1
        self._macd_hist_peak = None
        self._low_vol_bars_consec = 0
        self._profit_lock_armed = False

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

    def _regime_trail_info(self, hourly_idx: int) -> dict:
        info = {"enabled": self.regime_trail_enabled}
        if not self.regime_trail_enabled:
            info["ready"] = False
            info["mode"] = "disabled"
            return info
        series = [
            self._regime_adx,
            self._regime_adx_delta,
            self._regime_adx_pct_change,
            self._regime_er,
            self._regime_vol_ratio,
            self._regime_bb_abs_z,
            self._regime_kc_abs_z,
            self._regime_bb_abs_z_recent,
            self._regime_kc_abs_z_recent,
        ]
        if any(s is None for s in series) or hourly_idx < 0:
            info["ready"] = False
            info["mode"] = "unavailable"
            return info
        if any(hourly_idx >= len(s) for s in series if s is not None):
            info["ready"] = False
            info["mode"] = "unavailable"
            return info

        adx = self._regime_adx.iloc[hourly_idx]
        adx_delta = self._regime_adx_delta.iloc[hourly_idx]
        adx_pct_change = self._regime_adx_pct_change.iloc[hourly_idx]
        prev_exhaustion_idx = hourly_idx - self.regime_exhaustion_adx_lookback
        prev_exhaustion_adx = (
            self._regime_adx.iloc[prev_exhaustion_idx]
            if prev_exhaustion_idx >= 0
            else np.nan
        )
        er = self._regime_er.iloc[hourly_idx]
        vol_ratio = self._regime_vol_ratio.iloc[hourly_idx]
        bb_abs_z = self._regime_bb_abs_z.iloc[hourly_idx]
        kc_abs_z = self._regime_kc_abs_z.iloc[hourly_idx]
        bb_abs_z_recent = self._regime_bb_abs_z_recent.iloc[hourly_idx]
        kc_abs_z_recent = self._regime_kc_abs_z_recent.iloc[hourly_idx]
        values = [
            adx,
            adx_delta,
            adx_pct_change,
            prev_exhaustion_adx,
            er,
            vol_ratio,
            bb_abs_z,
            kc_abs_z,
            bb_abs_z_recent,
            kc_abs_z_recent,
        ]
        if any(pd.isna(v) for v in values):
            info["ready"] = False
            info["mode"] = "warmup"
            return info

        adx = float(adx)
        adx_delta = float(adx_delta)
        adx_pct_change = float(adx_pct_change)
        prev_exhaustion_adx = float(prev_exhaustion_adx)
        er = float(er)
        vol_ratio = float(vol_ratio)
        bb_abs_z = float(bb_abs_z)
        kc_abs_z = float(kc_abs_z)
        bb_abs_z_recent = float(bb_abs_z_recent)
        kc_abs_z_recent = float(kc_abs_z_recent)
        momentum = (
            adx >= self.regime_momentum_adx_min
            and er >= self.regime_momentum_er_min
            and adx_delta >= self.regime_momentum_adx_delta_min
            and vol_ratio <= self.regime_momentum_vol_ratio_max
        )
        stretched = (
            kc_abs_z >= self.regime_stretch_kc_z_min
            or bb_abs_z >= self.regime_stretch_bb_z_min
        )
        trend_not_confirmed = (
            adx < self.regime_momentum_adx_min
            or er < self.regime_momentum_er_min
            or adx_delta < 0.0
        )
        weak_or_fading = (
            adx <= self.regime_decay_adx_max
            or er <= self.regime_decay_er_max
            or adx_delta <= self.regime_decay_adx_delta_max
        )
        if momentum:
            mode = "momentum"
        elif stretched and trend_not_confirmed:
            mode = "mean_revert"
        elif not stretched and weak_or_fading:
            mode = "momentum_decay"
        else:
            mode = "neutral"
        base_mode = mode

        strict_exhaustion = (
            (
                kc_abs_z_recent >= self.regime_exhaustion_kc_z_min
                or bb_abs_z_recent >= self.regime_exhaustion_bb_z_min
            )
            and prev_exhaustion_adx >= self.regime_exhaustion_prev_adx_min
            and adx_pct_change <= -self.regime_exhaustion_adx_drop_pct
            and not momentum
        )

        if self.regime_trail_mode == "strict_exhaustion":
            trail_allowed = strict_exhaustion
            if strict_exhaustion:
                mode = "strict_exhaustion"
        elif self.regime_trail_mode == "momentum_decay":
            trail_allowed = mode == "momentum_decay"
        elif self.regime_trail_mode == "allowed_modes":
            trail_allowed = mode in {"mean_revert", "momentum_decay"}
        elif self.regime_trail_mode == "adx_exhaustion":
            # ADX-drop only: no KC/BB stretch required
            adx_exhaustion = (
                prev_exhaustion_adx >= self.regime_exhaustion_prev_adx_min
                and adx_pct_change <= -self.regime_exhaustion_adx_drop_pct
                and not momentum
            )
            trail_allowed = adx_exhaustion
            if adx_exhaustion:
                mode = "adx_exhaustion"
        elif self.regime_trail_mode in ("macd_exit", "ema_trail", "combined_bc"):
            # Direction-specific gate — overridden in _trail_stop_action
            trail_allowed = True
        else:
            trail_allowed = mode != "momentum"

        info.update({
            "ready": True,
            "mode": mode,
            "base_mode": base_mode,
            "trail_allowed": bool(trail_allowed),
            "adx": round(adx, 4),
            "adx_delta": round(adx_delta, 4),
            "adx_pct_change": round(adx_pct_change, 4),
            "prev_exhaustion_adx": round(prev_exhaustion_adx, 4),
            "efficiency": round(er, 6),
            "slow_vol_ratio": round(vol_ratio, 6),
            "bb_abs_z": round(bb_abs_z, 4),
            "kc_abs_z": round(kc_abs_z, 4),
            "bb_abs_z_recent": round(bb_abs_z_recent, 4),
            "kc_abs_z_recent": round(kc_abs_z_recent, 4),
            "stretched": bool(stretched),
            "strict_exhaustion": bool(strict_exhaustion),
            "exhaustion_stretch_lookback": self.regime_exhaustion_stretch_lookback,
            "exhaustion_kc_z_min": self.regime_exhaustion_kc_z_min,
            "exhaustion_bb_z_min": self.regime_exhaustion_bb_z_min,
            "exhaustion_adx_lookback": self.regime_exhaustion_adx_lookback,
            "exhaustion_prev_adx_min": self.regime_exhaustion_prev_adx_min,
            "exhaustion_adx_drop_pct": self.regime_exhaustion_adx_drop_pct,
            "regime_trail_mode": self.regime_trail_mode,
        })
        return info

    def _regime_trail_allowed(self, hourly_idx: int) -> tuple[bool, dict]:
        info = self._regime_trail_info(hourly_idx)
        return bool(info.get("ready") and info.get("trail_allowed")), info

    def _check_macd_exit_signal(self, hourly_idx: int, direction: str) -> bool:
        """True when MACD turns against the open position direction."""
        line = self._profit_exit_macd_line
        sig = self._profit_exit_macd_signal_series
        hist = self._profit_exit_macd_hist
        if line is None or sig is None or hourly_idx < 1 or hourly_idx >= len(line):
            return False
        cond = self.profit_exit_macd_condition
        if cond == "cross":
            prev_l = float(line.iloc[hourly_idx - 1])
            prev_s = float(sig.iloc[hourly_idx - 1])
            curr_l = float(line.iloc[hourly_idx])
            curr_s = float(sig.iloc[hourly_idx])
            if direction == "long":
                return prev_l >= prev_s and curr_l < curr_s
            else:
                return prev_l <= prev_s and curr_l > curr_s
        elif cond == "histogram":
            n = self.profit_exit_macd_histogram_bars
            if hist is None or hourly_idx < n:
                return False
            curr_hist = float(hist.iloc[hourly_idx])
            # Update running peak/trough since entry (idempotent under repeated calls).
            if self._macd_hist_peak is None:
                self._macd_hist_peak = curr_hist
            elif direction == "long":
                if curr_hist > self._macd_hist_peak:
                    self._macd_hist_peak = curr_hist
            else:
                if curr_hist < self._macd_hist_peak:
                    self._macd_hist_peak = curr_hist

            vals = [float(hist.iloc[hourly_idx - i]) for i in range(n + 1)]
            if direction == "long":
                consec = all(vals[i] > vals[i + 1] for i in range(n))
            else:
                consec = all(vals[i] < vals[i + 1] for i in range(n))
            if not consec:
                return False

            drop_thresh = self.profit_exit_macd_histogram_peak_drop_pct
            if drop_thresh > 0.0:
                peak = self._macd_hist_peak
                if direction == "long":
                    # Need a positive peak to fall from; current must have lost ≥ X%.
                    if peak is None or peak <= 0.0:
                        return False
                    return curr_hist <= peak * (1.0 - drop_thresh)
                else:
                    if peak is None or peak >= 0.0:
                        return False
                    # Trough is negative; "lost X% from trough" means recovered
                    # toward zero by X% of trough magnitude.
                    return curr_hist >= peak * (1.0 - drop_thresh)
            return True
        return False

    def _combined_bc_b_rising_edge(self, hourly_idx: int, regime_info: dict) -> bool:
        """True the first time B's adx_exhaustion fires on this hourly_idx."""
        prev_adx = float(regime_info.get("prev_exhaustion_adx", 0.0) or 0.0)
        adx_pct_change = float(regime_info.get("adx_pct_change", 0.0) or 0.0)
        is_momentum = regime_info.get("base_mode") == "momentum"
        b_signal = (
            prev_adx >= self.regime_exhaustion_prev_adx_min
            and adx_pct_change <= -self.regime_exhaustion_adx_drop_pct
            and not is_momentum
        )
        if b_signal and hourly_idx != self._combined_bc_last_b_eval_hourly:
            self._combined_bc_last_b_eval_hourly = hourly_idx
            return True
        return False

    def _combined_bc_c_rising_edge(self, hourly_idx: int, direction: str) -> bool:
        """True the first time C's macd_exit fires on this hourly_idx."""
        c_signal = self._check_macd_exit_signal(hourly_idx, direction)
        if c_signal and hourly_idx != self._combined_bc_last_c_eval_hourly:
            self._combined_bc_last_c_eval_hourly = hourly_idx
            return True
        return False

    def _flip_er_gate_passes(self, data_so_far: pd.DataFrame) -> tuple[bool, float | None]:
        """Compute Kaufman ER on the last `flip_er_gate_period` 5-min closes
        (optionally excluding the last `flip_er_gate_exclude_bars`). Return
        (passes, er_value). Disabled if threshold is sentinel (1e9)."""
        if self.flip_er_gate_threshold >= 1e8:
            return False, None
        M = self.flip_er_gate_period
        N = self.flip_er_gate_exclude_bars
        if data_so_far is None or len(data_so_far) < M + N + 1:
            return False, None
        closes = data_so_far["close"].to_numpy()
        end = len(closes) - 1 - N  # last bar (inclusive) used in ER
        start = end - M
        if start < 0:
            return False, None
        seg = closes[start: end + 1]
        if len(seg) < 2:
            return False, None
        net = abs(float(seg[-1] - seg[0]))
        path = float(np.abs(np.diff(seg)).sum())
        if path <= 0:
            return False, None
        er = net / path
        return (er >= self.flip_er_gate_threshold), er

    def _proactive_er_passes(self, data_so_far) -> bool:
        """5m Kaufman ER over the last fast_exit_er_gate_period closes vs the
        proactive-only threshold. Returns True when disabled (sentinel)."""
        if self.fast_exit_proactive_er_threshold >= 1e8:
            return True
        M = self.fast_exit_er_gate_period
        if data_so_far is None or len(data_so_far) < M + 1:
            return False
        closes = data_so_far["close"].to_numpy()
        seg = closes[-M - 1:]
        if len(seg) < 2:
            return False
        net = abs(float(seg[-1] - seg[0]))
        path = float(np.abs(np.diff(seg)).sum())
        if path <= 0:
            return False
        return (net / path) >= self.fast_exit_proactive_er_threshold

    def _fast_exit_er_gate_passes(self, data_so_far) -> tuple[bool, float | None]:
        """5m Kaufman ER over the last fast_exit_er_gate_period closes.
        Returns (passes, er). When threshold is sentinel (>=1e8) the gate is
        disabled and always passes."""
        if self.fast_exit_er_gate_threshold >= 1e8:
            return True, None
        M = self.fast_exit_er_gate_period
        if data_so_far is None or len(data_so_far) < M + 1:
            return False, None
        closes = data_so_far["close"].to_numpy()
        seg = closes[-M - 1:]
        if len(seg) < 2:
            return False, None
        net = abs(float(seg[-1] - seg[0]))
        path = float(np.abs(np.diff(seg)).sum())
        if path <= 0:
            return False, None
        er = net / path
        return (er >= self.fast_exit_er_gate_threshold), er

    def _evaluate_fast_exit_trigger(
        self, side: str, close: float, st_line: float, atr: float,
        row, date, hourly_idx: int, data_so_far,
    ) -> tuple[bool, str]:
        """Decide whether fast_exit should fire on the current 5m bar.

        Encapsulates the original RVOL/min_bars gate plus three new options:
        - fast_exit_min_bars_with_rvol: require BOTH counters when both armed.
        - fast_exit_er_gate_threshold:  suppress when 5m ER over M < threshold.
        - fast_exit_proactive_atr_mult: allow pre-cross trigger when close is
          within k×ATR of ST AND the current 5m bar is against the position
          (requires RVOL ≥ high_min when RVOL is in use).
        Resets consec_bars when neither crossed nor near, mirroring legacy.
        Returns (trigger, sub_reason).
        """
        c = float(close)
        s = float(st_line)
        if side == "long":
            crossed = c < s
            margin = c - s  # positive when above the line (in favor)
            against_bar = float(row.get("close", c)) < float(row.get("open", c))
        else:
            crossed = c > s
            margin = s - c
            against_bar = float(row.get("close", c)) > float(row.get("open", c))
        near = (
            (not crossed)
            and self.fast_exit_proactive_atr_mult > 0.0
            and atr > 0
            and 0 <= margin <= self.fast_exit_proactive_atr_mult * atr
            and against_bar
        )
        if not (crossed or near):
            self._fast_exit_consec_bars = 0
            return (False, "")

        _use_rvol = (
            self.fast_exit_rvol_min_ratio > 0
            or self.fast_exit_rvol_low_min > 0
            or self.fast_exit_rvol_high_min > 0
        )
        rvol = None
        rvol_ok = True
        active_threshold = 0.0
        if _use_rvol:
            rvol = (
                self._fast_exit_rvol.get(date)
                if self._fast_exit_rvol is not None
                else None
            )
            if self.fast_exit_rvol_low_min != self.fast_exit_rvol_high_min:
                weight, _ = self._flip_vol_regime_weight(int(hourly_idx))
                active_threshold = (
                    self.fast_exit_rvol_low_min
                    + weight * (self.fast_exit_rvol_high_min - self.fast_exit_rvol_low_min)
                )
            else:
                active_threshold = (
                    self.fast_exit_rvol_min_ratio or self.fast_exit_rvol_low_min
                )
            rvol_ok = (
                rvol is not None and not pd.isna(rvol) and float(rvol) >= active_threshold
            )

        bars_ok = True
        if (not _use_rvol) or self.fast_exit_min_bars_with_rvol:
            self._fast_exit_consec_bars += 1
            bars_ok = self._fast_exit_consec_bars >= self.fast_exit_min_bars

        trigger = (rvol_ok if _use_rvol else True) and bars_ok

        if near and trigger and _use_rvol:
            hi = self.fast_exit_rvol_high_min or self.fast_exit_rvol_min_ratio
            if not (
                rvol is not None and not pd.isna(rvol) and float(rvol) >= hi
            ):
                trigger = False

        if trigger and self.fast_exit_er_gate_threshold < 1e8:
            er_pass, _er_val = self._fast_exit_er_gate_passes(data_so_far)
            if not er_pass:
                trigger = False

        # Hybrid 1+3: proactive-only ER gate. Reuses fast_exit_er_gate_period.
        if (
            trigger
            and near
            and not crossed
            and self.fast_exit_proactive_er_threshold < 1e8
        ):
            er_pass = self._proactive_er_passes(data_so_far)
            if not er_pass:
                trigger = False

        if trigger:
            return (True, "fast_exit_proactive" if (near and not crossed) else "fast_exit")
        return (False, "")

    def _check_ema_exit_signal(self, hourly_idx: int, direction: str, close: float) -> bool:
        """True when price crosses the profit-exit EMA against the position."""
        ema = self._profit_exit_ema
        if ema is None or hourly_idx >= len(ema):
            return False
        ema_val = float(ema.iloc[hourly_idx])
        return close < ema_val if direction == "long" else close > ema_val

    def _trail_stop_action(
        self,
        direction: str,
        close: float,
        hourly_idx: int,
        pv: PortfolioView,
        bars_held: int,
        indicators: dict,
    ) -> Action | None:
        if (
            not self.regime_trail_enabled
            or (self.trail_stop_pct <= 0 and self.trail_stop_atr_multiple <= 0)
            or self._entry_price <= 0
        ):
            return None

        allowed, regime_info = self._regime_trail_allowed(hourly_idx)
        # Direction-specific override for MACD/EMA modes
        if self.regime_trail_mode == "macd_exit":
            allowed = self._check_macd_exit_signal(hourly_idx, direction)
            regime_info["trail_allowed"] = allowed
        elif self.regime_trail_mode == "ema_trail":
            allowed = self._check_ema_exit_signal(hourly_idx, direction, close)
            regime_info["trail_allowed"] = allowed
        # combined_bc handled below (needs gain to be computed first)
        indicators["regime_trail"] = regime_info
        active_trail_stop_pct = float(self.trail_stop_pct)
        trail_atr_pct = None
        if self.trail_stop_atr_multiple > 0:
            atr_now = indicators.get("atr")
            if atr_now is not None and not pd.isna(atr_now) and close > 0:
                trail_atr_pct = float(atr_now) / float(close)
                active_trail_stop_pct = max(
                    active_trail_stop_pct,
                    self.trail_stop_atr_multiple * trail_atr_pct,
                )
        if active_trail_stop_pct <= 0:
            return None

        if direction == "long":
            self._peak_since_entry = max(self._peak_since_entry or close, close)
            gain = self._peak_since_entry / self._entry_price - 1.0
            giveback = (
                (self._peak_since_entry - close) / self._peak_since_entry
                if self._peak_since_entry > 0
                else 0.0
            )
            action_type = ActionType.SELL
            quantity = pv.position_qty
            pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
        else:
            self._trough_since_entry = min(self._trough_since_entry or close, close)
            gain = self._entry_price / self._trough_since_entry - 1.0 if self._trough_since_entry > 0 else 0.0
            giveback = (
                (close - self._trough_since_entry) / self._trough_since_entry
                if self._trough_since_entry > 0
                else 0.0
            )
            action_type = ActionType.COVER
            quantity = pv.short_qty
            pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct

        indicators["regime_trail"].update({
            "peak_since_entry": round(float(self._peak_since_entry), 6),
            "trough_since_entry": round(float(self._trough_since_entry), 6),
            "trail_gain_pct": round(float(gain) * 100.0, 4),
            "trail_giveback_pct": round(float(giveback) * 100.0, 4),
            "trail_stop_pct": round(float(active_trail_stop_pct) * 100.0, 4),
            "trail_stop_floor_pct": round(float(self.trail_stop_pct) * 100.0, 4),
            "trail_stop_atr_multiple": round(float(self.trail_stop_atr_multiple), 4),
            "trail_stop_atr_pct": (
                round(float(trail_atr_pct) * 100.0, 4)
                if trail_atr_pct is not None
                else None
            ),
            "trail_min_gain_pct": round(float(self.trail_stop_min_gain_pct) * 100.0, 4),
        })

        # combined_bc: arm a flag on first signal (B's adx_exhaustion or C's
        # macd_exit), require the *other* signal to fire within
        # combined_bc_window_bars 5m bars while gain stays above min_gain.
        if self.regime_trail_mode == "combined_bc":
            b_signal_now = self._combined_bc_b_rising_edge(hourly_idx, regime_info)
            c_signal_now = self._combined_bc_c_rising_edge(hourly_idx, direction)

            armed_by = self._combined_bc_armed_by
            armed_at = self._combined_bc_armed_at_bar
            window = self.combined_bc_window_bars

            # Cancel armed flag if profit retraced below min_gain (option a)
            # or if the wait window has elapsed.
            if armed_by is not None:
                if gain < self.trail_stop_min_gain_pct:
                    armed_by = None
                elif (self._bar_count - armed_at) > window:
                    armed_by = None
                if armed_by is None:
                    self._combined_bc_armed_by = None
                    self._combined_bc_armed_at_bar = -1

            triggered = False
            if armed_by is None:
                # Arm only if currently above min_gain (no point arming below).
                if gain >= self.trail_stop_min_gain_pct:
                    if b_signal_now and c_signal_now:
                        # Both fired same bar — fire immediately.
                        triggered = True
                    elif b_signal_now:
                        self._combined_bc_armed_by = "B"
                        self._combined_bc_armed_at_bar = self._bar_count
                    elif c_signal_now:
                        self._combined_bc_armed_by = "C"
                        self._combined_bc_armed_at_bar = self._bar_count
            else:
                # Already armed — wait for the *other* signal.
                if armed_by == "B" and c_signal_now:
                    triggered = True
                elif armed_by == "C" and b_signal_now:
                    triggered = True

            # Windowed exit-arm: if exit_on_signal=False and giveback_window_bars > 0,
            # let the trigger keep `allowed=True` for that many additional 5m bars,
            # so giveback can be confirmed any time within the window.
            gb_window = self.trail_stop_giveback_window_bars
            if (
                not self.trail_stop_exit_on_signal
                and gb_window > 0
            ):
                if triggered:
                    self._combined_bc_exit_armed_at_bar = self._bar_count
                if self._combined_bc_exit_armed_at_bar >= 0:
                    bars_since = self._bar_count - self._combined_bc_exit_armed_at_bar
                    if bars_since > gb_window or gain < self.trail_stop_min_gain_pct:
                        self._combined_bc_exit_armed_at_bar = -1
                allowed = self._combined_bc_exit_armed_at_bar >= 0
            else:
                allowed = triggered

            regime_info["trail_allowed"] = allowed
            regime_info["combined_bc_armed_by"] = self._combined_bc_armed_by
            regime_info["combined_bc_b_signal"] = bool(b_signal_now)
            regime_info["combined_bc_c_signal"] = bool(c_signal_now)
            regime_info["combined_bc_window_bars"] = int(window)
            regime_info["combined_bc_exit_armed_at_bar"] = int(self._combined_bc_exit_armed_at_bar)

        exit_on_signal = self.trail_stop_exit_on_signal and allowed
        if (
            not allowed
            or gain < self.trail_stop_min_gain_pct
            or (not exit_on_signal and giveback < active_trail_stop_pct)
        ):
            return None

        self._in_long = False
        self._in_short = False
        self._trail_exit_direction = direction
        self._trail_exit_price = float(close)
        self._trail_exit_cooldown_left = self.trail_stop_cooldown_bars
        self._fast_exit_consec_bars = 0
        self._clear_held_flip()
        self._clear_position_state()
        return Action(action_type, quantity, {
            "exit_reason": "regime_trail_stop",
            "bars_held": bars_held,
            "pnl_pct": round(pnl_pct, 2),
            "trail_gain_pct": round(float(gain) * 100.0, 4),
            "trail_giveback_pct": round(float(giveback) * 100.0, 4),
            "trail_stop_pct": round(float(active_trail_stop_pct) * 100.0, 4),
            "trail_exit_on_signal": bool(exit_on_signal),
            "indicators": indicators,
        })

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
                self._record_position_entry(close)
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
            self._record_position_entry(close)
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
            self._clear_position_state()
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

        # ---- Profit-lock on consolidation tracking ----
        # On every hourly close while in position, check whether vol_ratio is
        # below the active threshold. Track consecutive low-vol bars and arm
        # the EXIT-NEXT-FLIP flag when count + open gain both exceed config.
        if (
            self.profit_lock_enabled
            and is_hourly_close
            and (self._in_long or self._in_short)
            and self._entry_price > 0
        ):
            pl_allowed, _pl_info = self._flip_vol_ratio_allows(int(hourly_idx))
            if pl_allowed:
                if self.profit_lock_cancel_on_recovery:
                    self._low_vol_bars_consec = 0
                    self._profit_lock_armed = False
            else:
                self._low_vol_bars_consec += 1
            if self._in_long:
                _cur_gain = (close / self._entry_price - 1)
            else:
                _cur_gain = (self._entry_price / close - 1) if close > 0 else 0.0
            if (
                self._low_vol_bars_consec >= self.profit_lock_low_vol_bars
                and _cur_gain >= self.profit_lock_min_gain_pct
            ):
                self._profit_lock_armed = True

        # Track consecutive hourly closes spent genuinely flat (no position
        # or pending state of any kind). Used by the flat-realign safety net.
        if is_hourly_close:
            truly_flat = (
                not self._in_long
                and not self._in_short
                and not self._pending_long
                and not self._pending_short
                and not self._fast_exit_direction
                and not self._trail_exit_direction
                and self._delayed_direction is None
                and self._persist_direction is None
            )
            if truly_flat:
                self._flat_realign_consec += 1
            else:
                self._flat_realign_consec = 0

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
                    self._record_position_entry(close)
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
                    self._record_position_entry(close)
                    return Action(ActionType.SHORT, qty, {
                        "entry_reason": "st_flip_bearish",
                        "immediate_flip": True,
                        "indicators": indicators,
                    })

        # --- EXIT LOGIC ---

        if self._in_long:
            bars_held = self._bar_count - self._entry_bar

            # Fast exit: leave when price crosses below ST line, gated by
            # either M consecutive bars (no RVOL params set) or 5m RVOL ratio
            # (optionally regime-adaptive via low_min/high_min).
            if self.fast_exit_enabled and st_bullish:
                trigger, sub_reason = self._evaluate_fast_exit_trigger(
                    "long", close, st_line, atr, row, date, int(hourly_idx), data_so_far,
                )
                if trigger:
                    pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                    self._in_long = False
                    self._hourly_closes_since_entry = 0
                    self._fast_exit_direction = "long"
                    self._fast_exit_cooldown_left = self.fast_exit_cooldown_bars
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    return Action(ActionType.SELL, pv.position_qty, {
                        "exit_reason": sub_reason,
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "indicators": indicators,
                    })

            if st_bullish:
                if self._held_flip_direction == "short":
                    self._clear_held_flip()
                trail_action = self._trail_stop_action(
                    "long", float(close), int(hourly_idx), pv, bars_held, indicators,
                )
                if trail_action is not None:
                    return trail_action
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
                    self._fast_exit_consec_bars = 0
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
                # Profit-lock-on-consolidation: bypass vol_ratio gate if flag is armed.
                if self.profit_lock_enabled and self._profit_lock_armed:
                    pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                    cur_gain = (close / self._entry_price - 1) if self._entry_price > 0 else 0.0
                    self._in_long = False
                    self._hourly_closes_since_entry = 0
                    if self.profit_lock_action == "flip":
                        self._pending_short = True
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    low_vol_bars = self._low_vol_bars_consec
                    self._low_vol_bars_consec = 0
                    self._profit_lock_armed = False
                    return Action(ActionType.SELL, pv.position_qty, {
                        "exit_reason": "profit_lock_consolidation",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "gain_at_lock_pct": round(cur_gain * 100, 4),
                        "low_vol_bars_at_lock": low_vol_bars,
                        "profit_lock_action": self.profit_lock_action,
                        "indicators": indicators,
                    })
                # Profit-protect: override rejection if unrealized gain at
                # rejection ≥ threshold (lock in profit, take the flip).
                cur_gain = (close / self._entry_price - 1) if self._entry_price > 0 else 0.0
                if cur_gain >= self.flip_protect_min_gain_pct:
                    pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                    self._in_long = False
                    self._hourly_closes_since_entry = 0
                    self._pending_short = True
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    return Action(ActionType.SELL, pv.position_qty, {
                        "exit_reason": "st_flip_protect",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "gain_at_rejection_pct": round(cur_gain * 100, 4),
                        "indicators": indicators,
                    })
                # ER gate: clean trend → honor flip.
                er_pass, er_val = self._flip_er_gate_passes(data_so_far)
                if er_pass:
                    pnl_pct = (close / self._entry_price - 1) * 100 - self.cost_per_trade_pct
                    self._in_long = False
                    self._hourly_closes_since_entry = 0
                    self._pending_short = True
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    return Action(ActionType.SELL, pv.position_qty, {
                        "exit_reason": "st_flip_er_gate",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "er_at_rejection": round(float(er_val), 4) if er_val is not None else None,
                        "indicators": indicators,
                    })
                # Legacy rejection.
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
            self._fast_exit_consec_bars = 0
            self._clear_held_flip()
            return Action(ActionType.SELL, pv.position_qty, {
                "exit_reason": "st_flip",
                "bars_held": bars_held,
                "pnl_pct": round(pnl_pct, 2),
                "indicators": indicators,
            })

        if self._in_short:
            bars_held = self._bar_count - self._entry_bar

            # Fast exit: leave when price crosses above ST line, gated by
            # either M consecutive bars or 5m RVOL ratio (optionally regime-adaptive).
            if self.fast_exit_enabled and not st_bullish:
                trigger, sub_reason = self._evaluate_fast_exit_trigger(
                    "short", close, st_line, atr, row, date, int(hourly_idx), data_so_far,
                )
                if trigger:
                    pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                    self._in_short = False
                    self._hourly_closes_since_entry = 0
                    self._fast_exit_direction = "short"
                    self._fast_exit_cooldown_left = self.fast_exit_cooldown_bars
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    return Action(ActionType.COVER, pv.short_qty, {
                        "exit_reason": sub_reason,
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "indicators": indicators,
                    })

            if not st_bullish:
                if self._held_flip_direction == "long":
                    self._clear_held_flip()
                trail_action = self._trail_stop_action(
                    "short", float(close), int(hourly_idx), pv, bars_held, indicators,
                )
                if trail_action is not None:
                    return trail_action
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
                    self._fast_exit_consec_bars = 0
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
                # Profit-lock-on-consolidation: if the EXIT-NEXT-FLIP flag is
                # armed (≥N low-vol bars + open gain ≥ threshold), bypass the
                # vol_ratio gate and take the flip. Optionally close-only.
                if self.profit_lock_enabled and self._profit_lock_armed:
                    pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                    cur_gain = (self._entry_price / close - 1) if (close > 0 and self._entry_price > 0) else 0.0
                    self._in_short = False
                    self._hourly_closes_since_entry = 0
                    if self.profit_lock_action == "flip":
                        self._pending_long = True
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    low_vol_bars = self._low_vol_bars_consec
                    self._low_vol_bars_consec = 0
                    self._profit_lock_armed = False
                    return Action(ActionType.COVER, pv.short_qty, {
                        "exit_reason": "profit_lock_consolidation",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "gain_at_lock_pct": round(cur_gain * 100, 4),
                        "low_vol_bars_at_lock": low_vol_bars,
                        "profit_lock_action": self.profit_lock_action,
                        "indicators": indicators,
                    })
                cur_gain = (self._entry_price / close - 1) if (close > 0 and self._entry_price > 0) else 0.0
                if cur_gain >= self.flip_protect_min_gain_pct:
                    pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                    self._in_short = False
                    self._hourly_closes_since_entry = 0
                    self._pending_long = True
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    return Action(ActionType.COVER, pv.short_qty, {
                        "exit_reason": "st_flip_protect",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "gain_at_rejection_pct": round(cur_gain * 100, 4),
                        "indicators": indicators,
                    })
                er_pass, er_val = self._flip_er_gate_passes(data_so_far)
                if er_pass:
                    pnl_pct = (self._entry_price / close - 1) * 100 - self.cost_per_trade_pct
                    self._in_short = False
                    self._hourly_closes_since_entry = 0
                    self._pending_long = True
                    self._fast_exit_consec_bars = 0
                    self._clear_held_flip()
                    return Action(ActionType.COVER, pv.short_qty, {
                        "exit_reason": "st_flip_er_gate",
                        "bars_held": bars_held,
                        "pnl_pct": round(pnl_pct, 2),
                        "er_at_rejection": round(float(er_val), 4) if er_val is not None else None,
                        "indicators": indicators,
                    })
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
            self._fast_exit_consec_bars = 0
            self._clear_held_flip()
            return Action(ActionType.COVER, pv.short_qty, {
                "exit_reason": "st_flip",
                "bars_held": bars_held,
                "pnl_pct": round(pnl_pct, 2),
                "indicators": indicators,
            })

        # --- FAST EXIT COOLDOWN & RE-ENTRY ---
        if (
            self.fast_exit_enabled
            and self._fast_exit_direction
            and not self._in_long and not self._in_short
        ):
            # Count down on every 5m bar
            if self._fast_exit_cooldown_left > 0:
                self._fast_exit_cooldown_left -= 1
                return Action(ActionType.HOLD, details={"reason": "fast_exit_cooldown", "indicators": indicators})

            direction = self._fast_exit_direction
            # If 30m bar officially flipped against us: abandon, let normal logic handle
            if (direction == "long" and not st_bullish) or (direction == "short" and st_bullish):
                self._clear_fast_exit()
                # fall through to normal entry logic
            else:
                # Re-enter if price recovered back to the correct side of ST.
                # With fast_exit_reentry_confirm, require fast_exit_cooldown_bars
                # consecutive bars on the correct side before re-entering — same
                # gate as the original fast exit — to filter one-bar bounces.
                recovered = (
                    (direction == "long" and float(close) > float(st_line))
                    or (direction == "short" and float(close) < float(st_line))
                )
                if recovered:
                    if self.fast_exit_reentry_confirm:
                        self._fast_exit_reentry_consec += 1
                        if self._fast_exit_reentry_consec < self.fast_exit_cooldown_bars:
                            return Action(ActionType.HOLD, details={"reason": "fast_exit_reentry_confirm", "indicators": indicators})
                    self._clear_fast_exit()
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        if direction == "long":
                            self._in_long = True
                        else:
                            self._in_short = True
                        self._record_position_entry(float(close))
                        action_type = ActionType.BUY if direction == "long" else ActionType.SHORT
                        return Action(action_type, qty, {
                            "entry_reason": "fast_exit_reentry",
                            "indicators": indicators,
                        })
                else:
                    self._fast_exit_reentry_consec = 0
                    return Action(ActionType.HOLD, details={"reason": "fast_exit_reentry_wait", "indicators": indicators})

        # --- REGIME TRAIL STOP COOLDOWN & RE-ENTRY ---
        if (
            self.regime_trail_enabled
            and self._trail_exit_direction
            and not self._in_long and not self._in_short
        ):
            direction = self._trail_exit_direction
            # If ST flipped against the exited direction, abandon same-side
            # re-entry and let normal ST flip logic take over.
            if (direction == "long" and not st_bullish) or (direction == "short" and st_bullish):
                self._clear_trail_exit()
            else:
                if not self.trail_stop_reentry_enabled:
                    return Action(ActionType.HOLD, details={
                        "reason": "regime_trail_reentry_disabled",
                        "indicators": indicators,
                    })
                if self._trail_exit_cooldown_left > 0:
                    self._trail_exit_cooldown_left -= 1
                    return Action(ActionType.HOLD, details={
                        "reason": "regime_trail_cooldown",
                        "indicators": indicators,
                    })
                if direction == "long":
                    recovered = close >= self._trail_exit_price * (1.0 + self.trail_stop_reentry_pct)
                    action_type = ActionType.BUY
                else:
                    recovered = close <= self._trail_exit_price * (1.0 - self.trail_stop_reentry_pct)
                    action_type = ActionType.SHORT
                if recovered:
                    qty = pv.cash * 0.9999 / close
                    if qty > 0:
                        trail_exit_price = self._trail_exit_price
                        if direction == "long":
                            self._in_long = True
                        else:
                            self._in_short = True
                        self._record_position_entry(close)
                        return Action(action_type, qty, {
                            "entry_reason": "regime_trail_reentry",
                            "trail_reentry_direction": direction,
                            "trail_exit_price": round(float(trail_exit_price), 6),
                            "indicators": indicators,
                        })
                return Action(ActionType.HOLD, details={
                    "reason": "regime_trail_reentry_wait",
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
                                self._record_position_entry(close)
                                return Action(ActionType.BUY, qty, {
                                    "entry_reason": "st_flip_bullish_delayed",
                                    "delay_hours": self.entry_delay_hours,
                                    "indicators": indicators,
                                })
                            else:
                                self._in_short = True
                                self._record_position_entry(close)
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
                        self._record_position_entry(close)
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
                        self._record_position_entry(close)
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
                    self._record_position_entry(close)
                    return Action(ActionType.BUY, qty, {
                        "entry_reason": "confirm_aligned_long",
                        "indicators": indicators,
                    })
            elif not st_bullish and self._confirm_agrees(hourly_idx, "short"):
                qty = pv.cash * 0.9999 / close
                if qty > 0:
                    self._in_short = True
                    self._record_position_entry(close)
                    return Action(ActionType.SHORT, qty, {
                        "entry_reason": "confirm_aligned_short",
                        "indicators": indicators,
                    })

        # Flat-realign safety net: if we've been flat for N hourly closes,
        # the implicit chop filter (see _prev_st_bullish init note) may have
        # silently swallowed a flip and stranded us out of a clear ST regime.
        # Align with the current ST direction if the vol-ratio gate allows.
        if (
            self.flat_realign_hourly_closes > 0
            and self._flat_realign_consec >= self.flat_realign_hourly_closes
        ):
            allowed, ratio_info = self._flip_vol_ratio_allows(int(hourly_idx))
            indicators["flip_vol_ratio"] = ratio_info
            if allowed:
                if st_bullish:
                    self._pending_long = True
                else:
                    self._pending_short = True
                self._flat_realign_consec = 0
                return Action(ActionType.HOLD, details={
                    "reason": "flat_realign",
                    "indicators": indicators,
                })
            # Vol rejected: reset counter so we re-evaluate after another N
            # flat hourly closes rather than firing on every 5m bar.
            self._flat_realign_consec = 0

        return Action(ActionType.HOLD, details={"reason": "no_signal", "indicators": indicators})
