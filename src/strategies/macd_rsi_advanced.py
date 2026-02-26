import math

import numpy as np
import pandas as pd

from .base import StrategyBase, Action, ActionType


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_macd(closes: pd.Series, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_rsi(closes: pd.Series, period: int) -> pd.Series:
    delta = closes.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.fillna(50.0)
    rsi[avg_loss == 0] = 100.0
    rsi[avg_gain == 0] = 0.0
    return rsi


def compute_adx(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int) -> pd.Series:
    prev_high = highs.shift(1)
    prev_low = lows.shift(1)
    prev_close = closes.shift(1)

    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows - prev_close).abs(),
    ], axis=1).max(axis=1)

    plus_dm = (highs - prev_high).where((highs - prev_high) > (prev_low - lows), 0.0).clip(lower=0)
    minus_dm = (prev_low - lows).where((prev_low - lows) > (highs - prev_high), 0.0).clip(lower=0)

    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
    adx = dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return adx


def compute_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int) -> pd.Series:
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def resample_ohlcv(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    resampled = df.resample(interval).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return resampled


class MACDRSIAdvancedStrategy(StrategyBase):
    """Trend-following strategy: MACD + RSI + ADX filter + ATR stops.

    Indicators are computed on resampled (default 1H) bars to filter noise,
    while stop-losses execute on the raw bar granularity for precision.

    Performance: indicators are precomputed on first call using the full dataset
    resampled once, then looked up by index on each bar — O(n) total instead of O(n²).
    """

    def __init__(self, portfolio, config):
        super().__init__(portfolio, config)
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_entry_low = config.get("rsi_entry_low", 40)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.rsi_exit_confirm = config.get("rsi_exit_confirm", 65)
        self.adx_period = config.get("adx_period", 14)
        self.adx_threshold = config.get("adx_threshold", 20)
        self.atr_period = config.get("atr_period", 14)
        self.atr_stop_multiplier = config.get("atr_stop_multiplier", 3.0)
        self.atr_trailing_multiplier = config.get("atr_trailing_multiplier", 3.0)
        self.stop_loss_pct = config.get("stop_loss_pct", 8.0)
        self.trailing_stop_pct = config.get("trailing_stop_pct", 8.0)
        self.ema_trend_period = config.get("ema_trend_period", 200)
        self.cooldown_bars = config.get("cooldown_bars", 4)
        self.exit_on_macd_cross = config.get("exit_on_macd_cross", False)
        self.trend_reentry = config.get("trend_reentry", True)
        self.trend_reentry_cooldown = config.get("trend_reentry_cooldown", 2)
        self.trend_reentry_rsi_max = config.get("trend_reentry_rsi_max", 70)
        self.resample_interval = config.get("resample_interval", "1h")

        # Short selling parameters (higher conviction required)
        self.enable_short = config.get("enable_short", False)
        self.short_adx_threshold = config.get("short_adx_threshold", 25)
        self.short_rsi_entry_high = config.get("short_rsi_entry_high", 60)
        self.short_rsi_oversold = config.get("short_rsi_oversold", 30)
        self.short_rsi_exit_confirm = config.get("short_rsi_exit_confirm", 35)
        self.short_stop_loss_pct = config.get("short_stop_loss_pct", 6.0)
        self.short_trailing_stop_pct = config.get("short_trailing_stop_pct", 6.0)
        self.short_size_pct = config.get("short_size_pct", 50.0)

        # Short noise filters (v8): skip weak short entries
        self.short_noise_ema_pct = config.get("short_noise_ema_pct", -3.0)
        self.short_noise_vol_ratio = config.get("short_noise_vol_ratio", 1.0)
        self.short_bb_period = config.get("short_bb_period", 20)
        self.short_bb_std = config.get("short_bb_std", 2.0)
        self.short_bb_vol_min = config.get("short_bb_vol_min", 1.0)

        # Long state
        self._entry_price = None
        self._peak_since_entry = None
        self._bars_since_exit = self.cooldown_bars
        self._last_exit_profitable = False
        self._prev_macd = None
        self._prev_signal = None
        self._prev_rsi = None

        # Short state
        self._short_entry_price = None
        self._trough_since_entry = None
        self._resampled_bar_count = 0

        self._indicators = None
        self._resampled_index = None

    def prepare(self, full_data: pd.DataFrame) -> None:
        """Resample the full dataset once and precompute all indicator series."""
        resampled = resample_ohlcv(full_data, self.resample_interval)
        closes = resampled["close"]
        highs = resampled["high"]
        lows = resampled["low"]
        volumes = resampled["volume"]

        macd_line, signal_line, _ = compute_macd(closes, self.macd_fast, self.macd_slow, self.macd_signal)
        rsi = compute_rsi(closes, self.rsi_period)
        adx = compute_adx(highs, lows, closes, self.adx_period)
        atr = compute_atr(highs, lows, closes, self.atr_period)
        ema_trend = compute_ema(closes, self.ema_trend_period)

        vol_sma = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_sma

        bb_mid = closes.rolling(self.short_bb_period).mean()
        bb_std = closes.rolling(self.short_bb_period).std()
        bb_lower = bb_mid - self.short_bb_std * bb_std

        self._indicators = pd.DataFrame({
            "macd": macd_line,
            "macd_signal": signal_line,
            "rsi": rsi,
            "adx": adx,
            "atr": atr,
            "ema": ema_trend,
            "vol_ratio": vol_ratio,
            "bb_lower": bb_lower,
        }, index=resampled.index)
        self._resampled_index = resampled.index

    def _get_resampled_bar_idx(self, date: pd.Timestamp) -> int:
        idx = self._resampled_index.searchsorted(date, side="right") - 1
        return max(idx, 0)

    def on_bar(
        self,
        date: pd.Timestamp,
        row: pd.Series,
        data_so_far: pd.DataFrame,
        is_last_bar: bool,
    ) -> Action:
        symbol = self.config.get("symbol", "UNKNOWN")
        price = row["close"]
        has_long = symbol in self.portfolio.positions
        has_short = symbol in self.portfolio.short_positions

        if self._indicators is None:
            self.prepare(data_so_far)

        bar_idx = self._get_resampled_bar_idx(date)
        new_resampled_bar = bar_idx != self._resampled_bar_count
        self._resampled_bar_count = bar_idx

        min_bars = self.macd_slow + self.macd_signal + 5
        if bar_idx < min_bars:
            if not has_long and not has_short:
                self._bars_since_exit += 1 if new_resampled_bar else 0
            return Action(action=ActionType.HOLD, quantity=0, details={"reason": "Warming up indicators"})

        ind = self._indicators.iloc[bar_idx]
        cur_macd = ind["macd"]
        cur_signal = ind["macd_signal"]
        cur_rsi = ind["rsi"]
        cur_adx = ind["adx"]
        cur_atr = ind["atr"]
        cur_ema = ind["ema"]
        cur_vol_ratio = ind["vol_ratio"]
        cur_bb_lower = ind["bb_lower"]

        details = {
            "macd": round(float(cur_macd), 2),
            "macd_signal": round(float(cur_signal), 2),
            "rsi": round(float(cur_rsi), 2),
            "adx": round(float(cur_adx), 2),
            "atr": round(float(cur_atr), 2),
            "ema_200": round(float(cur_ema), 2),
        }

        if is_last_bar:
            if has_long:
                quantity = self.portfolio.positions[symbol].quantity
                self.portfolio.sell(symbol, quantity, price)
                details["reason"] = "Final bar - liquidate long"
                return Action(action=ActionType.SELL, quantity=quantity, details=details)
            if has_short:
                quantity = self.portfolio.short_positions[symbol].quantity
                self.portfolio.cover(symbol, quantity, price)
                details["reason"] = "Final bar - cover short"
                return Action(action=ActionType.COVER, quantity=quantity, details=details)

        if has_long:
            self._peak_since_entry = max(self._peak_since_entry or price, price)
            action = self._check_exit(symbol, price, cur_macd, cur_signal, cur_rsi, cur_atr, new_resampled_bar, details)
        elif has_short:
            self._trough_since_entry = min(self._trough_since_entry or price, price)
            action = self._check_short_exit(symbol, price, cur_macd, cur_signal, cur_rsi, cur_atr, new_resampled_bar, details)
        else:
            if new_resampled_bar:
                self._bars_since_exit += 1
            action = self._check_entry(symbol, price, cur_macd, cur_signal, cur_rsi, cur_adx, cur_ema, cur_vol_ratio, cur_bb_lower, new_resampled_bar, details)

        self._prev_macd = cur_macd
        self._prev_signal = cur_signal
        self._prev_rsi = cur_rsi

        return action

    def _check_entry(
        self, symbol, price, macd, signal, rsi, adx, ema, vol_ratio, bb_lower, new_bar, details
    ) -> Action:
        if not new_bar:
            details["reason"] = "No signal (intra-bar)"
            return Action(action=ActionType.HOLD, quantity=0, details=details)

        rsi_ok = self.rsi_entry_low <= rsi <= self.rsi_overbought
        adx_ok = adx >= self.adx_threshold
        trend_ok = price > ema
        macd_bullish = macd > signal

        # Trend continuation: after a profitable exit, re-enter with relaxed
        # conditions (no MACD cross needed, just MACD > signal) and shorter cooldown.
        if self.trend_reentry and self._last_exit_profitable:
            cooldown = self.trend_reentry_cooldown
            if self._bars_since_exit < cooldown:
                details["reason"] = f"Cooldown ({self._bars_since_exit}/{cooldown})"
                return Action(action=ActionType.HOLD, quantity=0, details=details)

            rsi_cooled = self.rsi_entry_low <= rsi <= self.trend_reentry_rsi_max
            if macd_bullish and rsi_cooled and adx_ok and trend_ok:
                quantity = math.floor(self.portfolio.cash / price * 1e8) / 1e8
                if quantity > 0:
                    self.portfolio.buy(symbol, quantity, price)
                    self._entry_price = price
                    self._peak_since_entry = price
                    self._last_exit_profitable = False
                    details["reason"] = "Trend continuation re-entry (MACD bullish + RSI/ADX/EMA)"
                    return Action(action=ActionType.BUY, quantity=quantity, details=details)

        # Standard entry: requires MACD golden cross
        if self._bars_since_exit < self.cooldown_bars:
            details["reason"] = f"Cooldown ({self._bars_since_exit}/{self.cooldown_bars})"
            return Action(action=ActionType.HOLD, quantity=0, details=details)

        macd_cross_up = (
            self._prev_macd is not None
            and self._prev_macd <= self._prev_signal
            and macd > signal
        )

        if macd_cross_up and rsi_ok and adx_ok and trend_ok:
            quantity = math.floor(self.portfolio.cash / price * 1e8) / 1e8
            if quantity > 0:
                self.portfolio.buy(symbol, quantity, price)
                self._entry_price = price
                self._peak_since_entry = price
                details["reason"] = "MACD golden cross + RSI/ADX/EMA confirmed"
                return Action(action=ActionType.BUY, quantity=quantity, details=details)

        # Short entry: MACD bearish + strong bearish indicators + price below EMA
        # + BB breakdown with volume (v8 noise filters).
        if self.enable_short and self._bars_since_exit >= self.cooldown_bars:
            macd_bearish = macd < signal
            short_rsi_ok = rsi <= self.short_rsi_entry_high
            short_adx_ok = adx >= self.short_adx_threshold
            short_trend_ok = price < ema

            pct_below_ema = (price - ema) / ema * 100
            near_ema_low_vol = (
                pct_below_ema > self.short_noise_ema_pct
                and vol_ratio < self.short_noise_vol_ratio
            )
            bb_confirmed = (
                price < bb_lower
                and vol_ratio >= self.short_bb_vol_min
            )

            if (macd_bearish and short_rsi_ok and short_adx_ok and short_trend_ok
                    and not near_ema_low_vol and bb_confirmed):
                short_cash = self.portfolio.cash * (self.short_size_pct / 100.0)
                quantity = math.floor(short_cash / price * 1e8) / 1e8
                if quantity > 0:
                    self.portfolio.short_sell(symbol, quantity, price)
                    self._short_entry_price = price
                    self._trough_since_entry = price
                    details["reason"] = "Short: MACD bearish + BB breakdown + vol confirmed"
                    return Action(action=ActionType.SHORT, quantity=quantity, details=details)

        reasons = []
        if not macd_cross_up and not macd_bullish:
            reasons.append("no MACD cross")
        elif not macd_cross_up:
            reasons.append("no MACD cross (MACD bullish but no fresh crossover)")
        if not rsi_ok:
            reasons.append(f"RSI {rsi:.0f} outside [{self.rsi_entry_low}-{self.rsi_overbought}]")
        if not adx_ok:
            reasons.append(f"ADX {adx:.0f} < {self.adx_threshold}")
        if not trend_ok:
            reasons.append(f"price below EMA-{self.ema_trend_period}")
        details["reason"] = "No signal: " + ", ".join(reasons)
        return Action(action=ActionType.HOLD, quantity=0, details=details)

    def _check_exit(
        self, symbol, price, macd, signal, rsi, atr, new_bar, details
    ) -> Action:
        quantity = self.portfolio.positions[symbol].quantity

        entry_price = self._entry_price
        peak = self._peak_since_entry

        # Stop-loss: max of ATR-based and percentage-based
        stop_distance = max(
            self.atr_stop_multiplier * atr,
            self.stop_loss_pct / 100.0 * entry_price,
        )
        stop_price = entry_price - stop_distance
        if price <= stop_price:
            self.portfolio.sell(symbol, quantity, price)
            self._reset_after_exit(price)
            details["reason"] = f"Stop-loss hit (entry={entry_price:.0f}, stop={stop_price:.0f})"
            return Action(action=ActionType.SELL, quantity=quantity, details=details)

        # Trailing stop: max of ATR-based and percentage-based
        trail_distance = max(
            self.atr_trailing_multiplier * atr,
            self.trailing_stop_pct / 100.0 * peak,
        )
        trail_price = peak - trail_distance
        if price <= trail_price:
            self.portfolio.sell(symbol, quantity, price)
            self._reset_after_exit(price)
            details["reason"] = f"Trailing stop hit (peak={peak:.0f}, trail={trail_price:.0f})"
            return Action(action=ActionType.SELL, quantity=quantity, details=details)

        if not new_bar:
            details["reason"] = "Holding (intra-bar)"
            return Action(action=ActionType.HOLD, quantity=0, details=details)

        # MACD death cross (optional, off by default)
        if self.exit_on_macd_cross:
            macd_cross_down = (
                self._prev_macd is not None
                and self._prev_macd >= self._prev_signal
                and macd < signal
            )
            if macd_cross_down:
                self.portfolio.sell(symbol, quantity, price)
                self._reset_after_exit(price)
                details["reason"] = "MACD death cross"
                return Action(action=ActionType.SELL, quantity=quantity, details=details)

        # RSI overbought reversal — only when in profit and RSI drops below confirmation level
        in_profit = price > entry_price
        rsi_was_overbought = self._prev_rsi is not None and self._prev_rsi >= self.rsi_overbought
        rsi_dropped = rsi < self.rsi_exit_confirm
        if in_profit and rsi_was_overbought and rsi_dropped:
            self.portfolio.sell(symbol, quantity, price)
            self._reset_after_exit(price)
            details["reason"] = f"RSI overbought reversal ({self._prev_rsi:.0f} -> {rsi:.0f})"
            return Action(action=ActionType.SELL, quantity=quantity, details=details)

        details["reason"] = "Holding"
        return Action(action=ActionType.HOLD, quantity=0, details=details)

    def _check_short_exit(
        self, symbol, price, macd, signal, rsi, atr, new_bar, details
    ) -> Action:
        """Check exit conditions for a short position (mirror of long exit)."""
        quantity = self.portfolio.short_positions[symbol].quantity
        entry_price = self._short_entry_price
        trough = self._trough_since_entry

        # Short stop-loss: price rises above entry + stop distance
        stop_distance = max(
            self.atr_stop_multiplier * atr,
            self.short_stop_loss_pct / 100.0 * entry_price,
        )
        stop_price = entry_price + stop_distance
        if price >= stop_price:
            self.portfolio.cover(symbol, quantity, price)
            self._reset_short_after_exit(price)
            details["reason"] = f"Short stop-loss hit (entry={entry_price:.0f}, stop={stop_price:.0f})"
            return Action(action=ActionType.COVER, quantity=quantity, details=details)

        # Short trailing stop: price rises above trough + trail distance
        trail_distance = max(
            self.atr_trailing_multiplier * atr,
            self.short_trailing_stop_pct / 100.0 * trough,
        )
        trail_price = trough + trail_distance
        if price >= trail_price:
            self.portfolio.cover(symbol, quantity, price)
            self._reset_short_after_exit(price)
            details["reason"] = f"Short trailing stop (trough={trough:.0f}, trail={trail_price:.0f})"
            return Action(action=ActionType.COVER, quantity=quantity, details=details)

        if not new_bar:
            details["reason"] = "Holding short (intra-bar)"
            return Action(action=ActionType.HOLD, quantity=0, details=details)

        # RSI oversold reversal — cover when RSI bounces from oversold and short is profitable
        in_profit = price < entry_price
        rsi_was_oversold = self._prev_rsi is not None and self._prev_rsi <= self.short_rsi_oversold
        rsi_bounced = rsi > self.short_rsi_exit_confirm
        if in_profit and rsi_was_oversold and rsi_bounced:
            self.portfolio.cover(symbol, quantity, price)
            self._reset_short_after_exit(price)
            details["reason"] = f"RSI oversold reversal ({self._prev_rsi:.0f} -> {rsi:.0f})"
            return Action(action=ActionType.COVER, quantity=quantity, details=details)

        # MACD golden cross — momentum shifting bullish, cover
        macd_cross_up = (
            self._prev_macd is not None
            and self._prev_macd <= self._prev_signal
            and macd > signal
        )
        if macd_cross_up:
            self.portfolio.cover(symbol, quantity, price)
            self._reset_short_after_exit(price)
            details["reason"] = "Short cover: MACD golden cross"
            return Action(action=ActionType.COVER, quantity=quantity, details=details)

        details["reason"] = "Holding short"
        return Action(action=ActionType.HOLD, quantity=0, details=details)

    def _reset_after_exit(self, price):
        self._last_exit_profitable = price > self._entry_price if self._entry_price else False
        self._entry_price = None
        self._peak_since_entry = None
        self._bars_since_exit = 0

    def _reset_short_after_exit(self, price):
        self._last_exit_profitable = False
        self._short_entry_price = None
        self._trough_since_entry = None
        self._bars_since_exit = 0
