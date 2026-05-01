"""MACD Vortex ADX strategy reporter.

Builds a lightweight-charts HTML report with:
- price + breakout reference overlays + trade markers
- MACD pane
- Vortex pane
- ADX / ATR pane
- portfolio value pane
"""
import json
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from reporting.lazy_swing_reporter import (
    _aligned_st_segments_to_json,
    _build_markers,
    _build_portfolio,
    _forward_fill_hist_to_5m,
    _forward_fill_series_to_5m,
    _forward_fill_st_segments_to_5m,
    _histogram_to_json,
    _line_to_json,
    _ohlcv_to_json,
    _resample_st_segments_to_timeframe,
    _resample_hist_to_timeframe,
    _resample_ohlcv,
    _resample_series_to_timeframe,
    _trend_mask,
    _volume_to_json,
)
from reporting.reporter import TEMPLATES_DIR, compute_stats
from strategies.intraday_indicators import compute_aroon, compute_supertrend, compute_vortex
from strategies.macd_rsi_advanced import compute_adx, compute_atr, compute_macd
from trade_log import TradeLogReader


_ST_BULL_COLOR = "#34d399"
_ST_BEAR_COLOR = "#f87171"


def _completed_signal_ohlcv(price_data: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Match the strategy's completed-bar signal timing."""
    signal = _resample_ohlcv(price_data, freq)
    if signal.empty or price_data.empty:
        return signal

    last_5m_ts = price_data.index[-1]
    resample_freq = pd.tseries.frequencies.to_offset(freq)
    last_signal_start = last_5m_ts.floor(freq)
    last_signal_end = last_signal_start + resample_freq - pd.Timedelta(minutes=5)
    if last_5m_ts < last_signal_end:
        signal = signal.iloc[:-1]
    return signal


def _constant_series(index: pd.DatetimeIndex, value: float) -> pd.Series:
    return pd.Series(float(value), index=index, dtype=float)


def _display_timestamp(ts: pd.Timestamp, timezone_name: str) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    return stamp.tz_convert(timezone_name)


def _build_indicator_frame(
    price_data: pd.DataFrame,
    params: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute the strategy's native-timeframe indicators for charting."""
    signal_freq = str(params.get("resample_interval", "30min"))
    signal = _completed_signal_ohlcv(price_data, signal_freq)
    if signal.empty:
        return signal, pd.DataFrame(index=signal.index)

    macd_fast = int(params.get("macd_fast", 12))
    macd_slow = int(params.get("macd_slow", 26))
    macd_signal = int(params.get("macd_signal", 9))
    vortex_period = int(params.get("vortex_period", 14))
    vortex_baseline_bars = int(params.get("vortex_baseline_bars", 3))
    vortex_strong_spread_mult = float(params.get("vortex_strong_spread_mult", 1.25))
    vortex_hugging_spread_mult = float(params.get("vortex_hugging_spread_mult", 1.05))
    vortex_ema_period = int(params.get("vortex_ema_period", 3))
    adx_period = int(params.get("adx_period", 14))
    adx_floor = float(params.get("adx_floor", 20.0))
    atr_period = int(params.get("atr_period", 14))
    breakout_lookback_bars = int(params.get("breakout_lookback_bars", 3))
    supertrend_atr_period = int(params.get("supertrend_atr_period", 12))
    supertrend_multiplier = float(params.get("supertrend_multiplier", 1.5))
    aroon_period = int(params.get("aroon_period", 14))

    macd_line, macd_signal_line, macd_hist = compute_macd(
        signal["close"], macd_fast, macd_slow, macd_signal
    )
    st_line, st_bull = compute_supertrend(
        signal["high"],
        signal["low"],
        signal["close"],
        supertrend_atr_period,
        supertrend_multiplier,
    )
    aroon_up, aroon_down = compute_aroon(signal["high"], signal["low"], aroon_period)
    vi_plus, vi_minus = compute_vortex(
        signal["high"], signal["low"], signal["close"], vortex_period
    )
    vi_plus_ema = vi_plus.ewm(span=vortex_ema_period, adjust=False).mean()
    vi_minus_ema = vi_minus.ewm(span=vortex_ema_period, adjust=False).mean()
    spread = (vi_plus - vi_minus).abs()
    baseline = spread.shift(1).rolling(vortex_baseline_bars).mean()
    strong_threshold = baseline * vortex_strong_spread_mult
    hugging_threshold = baseline * vortex_hugging_spread_mult
    adx = compute_adx(signal["high"], signal["low"], signal["close"], adx_period)
    atr = compute_atr(signal["high"], signal["low"], signal["close"], atr_period)
    breakout_high = signal["high"].shift(1).rolling(breakout_lookback_bars).max()
    breakout_low = signal["low"].shift(1).rolling(breakout_lookback_bars).min()

    indicators = pd.DataFrame(
        {
            "macd": macd_line,
            "macd_signal": macd_signal_line,
            "macd_hist": macd_hist,
            "supertrend_line": st_line,
            "supertrend_bullish": st_bull.astype(bool),
            "aroon_up": aroon_up,
            "aroon_down": aroon_down,
            "vi_plus": vi_plus,
            "vi_minus": vi_minus,
            "vi_plus_ema": vi_plus_ema,
            "vi_minus_ema": vi_minus_ema,
            "vortex_spread": spread,
            "vortex_baseline": baseline,
            "vortex_strong_threshold": strong_threshold,
            "vortex_hugging_threshold": hugging_threshold,
            "vortex_midline": _constant_series(signal.index, 1.0),
            "adx": adx,
            "adx_floor": _constant_series(signal.index, adx_floor),
            "atr": atr,
            "breakout_high_ref": breakout_high,
            "breakout_low_ref": breakout_low,
            "macd_zero": _constant_series(signal.index, 0.0),
        },
        index=signal.index,
    )
    return signal, indicators


def _tf_data_from_signal(
    candles: pd.DataFrame,
    signal: pd.DataFrame,
    indicators: pd.DataFrame,
    *,
    mode: str,
) -> dict:
    """Convert native indicator series to one chart timeframe."""
    if mode == "5m":
        index = candles.index
        supertrend = {
            "bull_segments": _forward_fill_st_segments_to_5m(
                indicators["supertrend_line"],
                indicators["supertrend_bullish"],
                index,
                active_mask=_trend_mask(indicators["supertrend_bullish"], True),
                bull_color=_ST_BULL_COLOR,
                bear_color=_ST_BULL_COLOR,
            ),
            "bear_segments": _forward_fill_st_segments_to_5m(
                indicators["supertrend_line"],
                indicators["supertrend_bullish"],
                index,
                active_mask=_trend_mask(indicators["supertrend_bullish"], False),
                bull_color=_ST_BEAR_COLOR,
                bear_color=_ST_BEAR_COLOR,
            ),
        }
        breakout = {
            "high": _forward_fill_series_to_5m(
                indicators["breakout_high_ref"], index, decimals=2
            ),
            "low": _forward_fill_series_to_5m(
                indicators["breakout_low_ref"], index, decimals=2
            ),
        }
        macd = {
            "hist": _forward_fill_hist_to_5m(indicators["macd_hist"], index),
            "line": _forward_fill_series_to_5m(indicators["macd"], index),
            "signal": _forward_fill_series_to_5m(indicators["macd_signal"], index),
            "zero": _forward_fill_series_to_5m(indicators["macd_zero"], index),
        }
        vortex = {
            "plus": _forward_fill_series_to_5m(indicators["vi_plus"], index),
            "minus": _forward_fill_series_to_5m(indicators["vi_minus"], index),
            "plus_ema": _forward_fill_series_to_5m(indicators["vi_plus_ema"], index),
            "minus_ema": _forward_fill_series_to_5m(indicators["vi_minus_ema"], index),
            "midline": _forward_fill_series_to_5m(indicators["vortex_midline"], index),
            "spread": _forward_fill_series_to_5m(
                indicators["vortex_spread"], index
            ),
            "baseline": _forward_fill_series_to_5m(
                indicators["vortex_baseline"], index
            ),
            "strong": _forward_fill_series_to_5m(
                indicators["vortex_strong_threshold"], index
            ),
            "hugging": _forward_fill_series_to_5m(
                indicators["vortex_hugging_threshold"], index
            ),
        }
        adx = {
            "adx": _forward_fill_series_to_5m(indicators["adx"], index),
            "floor": _forward_fill_series_to_5m(indicators["adx_floor"], index),
            "atr": _forward_fill_series_to_5m(indicators["atr"], index),
        }
        aroon = {
            "up": _forward_fill_series_to_5m(indicators["aroon_up"], index),
            "down": _forward_fill_series_to_5m(indicators["aroon_down"], index),
        }
    elif mode == "signal":
        supertrend = {
            "bull_segments": _aligned_st_segments_to_json(
                indicators["supertrend_line"],
                indicators["supertrend_bullish"],
                active_mask=_trend_mask(indicators["supertrend_bullish"], True),
                bull_color=_ST_BULL_COLOR,
                bear_color=_ST_BULL_COLOR,
            ),
            "bear_segments": _aligned_st_segments_to_json(
                indicators["supertrend_line"],
                indicators["supertrend_bullish"],
                active_mask=_trend_mask(indicators["supertrend_bullish"], False),
                bull_color=_ST_BEAR_COLOR,
                bear_color=_ST_BEAR_COLOR,
            ),
        }
        breakout = {
            "high": _line_to_json(indicators["breakout_high_ref"], decimals=2),
            "low": _line_to_json(indicators["breakout_low_ref"], decimals=2),
        }
        macd = {
            "hist": _histogram_to_json(indicators["macd_hist"]),
            "line": _line_to_json(indicators["macd"]),
            "signal": _line_to_json(indicators["macd_signal"]),
            "zero": _line_to_json(indicators["macd_zero"]),
        }
        vortex = {
            "plus": _line_to_json(indicators["vi_plus"]),
            "minus": _line_to_json(indicators["vi_minus"]),
            "plus_ema": _line_to_json(indicators["vi_plus_ema"]),
            "minus_ema": _line_to_json(indicators["vi_minus_ema"]),
            "midline": _line_to_json(indicators["vortex_midline"]),
            "spread": _line_to_json(indicators["vortex_spread"]),
            "baseline": _line_to_json(indicators["vortex_baseline"]),
            "strong": _line_to_json(indicators["vortex_strong_threshold"]),
            "hugging": _line_to_json(indicators["vortex_hugging_threshold"]),
        }
        adx = {
            "adx": _line_to_json(indicators["adx"]),
            "floor": _line_to_json(indicators["adx_floor"]),
            "atr": _line_to_json(indicators["atr"]),
        }
        aroon = {
            "up": _line_to_json(indicators["aroon_up"]),
            "down": _line_to_json(indicators["aroon_down"]),
        }
    else:
        supertrend = {
            "bull_segments": _resample_st_segments_to_timeframe(
                indicators["supertrend_line"],
                indicators["supertrend_bullish"],
                mode,
                active_mask=_trend_mask(indicators["supertrend_bullish"], True),
                bull_color=_ST_BULL_COLOR,
                bear_color=_ST_BULL_COLOR,
            ),
            "bear_segments": _resample_st_segments_to_timeframe(
                indicators["supertrend_line"],
                indicators["supertrend_bullish"],
                mode,
                active_mask=_trend_mask(indicators["supertrend_bullish"], False),
                bull_color=_ST_BEAR_COLOR,
                bear_color=_ST_BEAR_COLOR,
            ),
        }
        breakout = {
            "high": _resample_series_to_timeframe(
                indicators["breakout_high_ref"], mode, decimals=2
            ),
            "low": _resample_series_to_timeframe(
                indicators["breakout_low_ref"], mode, decimals=2
            ),
        }
        macd = {
            "hist": _resample_hist_to_timeframe(indicators["macd_hist"], mode),
            "line": _resample_series_to_timeframe(indicators["macd"], mode),
            "signal": _resample_series_to_timeframe(indicators["macd_signal"], mode),
            "zero": _resample_series_to_timeframe(indicators["macd_zero"], mode),
        }
        vortex = {
            "plus": _resample_series_to_timeframe(indicators["vi_plus"], mode),
            "minus": _resample_series_to_timeframe(indicators["vi_minus"], mode),
            "plus_ema": _resample_series_to_timeframe(indicators["vi_plus_ema"], mode),
            "minus_ema": _resample_series_to_timeframe(indicators["vi_minus_ema"], mode),
            "midline": _resample_series_to_timeframe(
                indicators["vortex_midline"], mode
            ),
            "spread": _resample_series_to_timeframe(
                indicators["vortex_spread"], mode
            ),
            "baseline": _resample_series_to_timeframe(
                indicators["vortex_baseline"], mode
            ),
            "strong": _resample_series_to_timeframe(
                indicators["vortex_strong_threshold"], mode
            ),
            "hugging": _resample_series_to_timeframe(
                indicators["vortex_hugging_threshold"], mode
            ),
        }
        adx = {
            "adx": _resample_series_to_timeframe(indicators["adx"], mode),
            "floor": _resample_series_to_timeframe(indicators["adx_floor"], mode),
            "atr": _resample_series_to_timeframe(indicators["atr"], mode),
        }
        aroon = {
            "up": _resample_series_to_timeframe(indicators["aroon_up"], mode),
            "down": _resample_series_to_timeframe(indicators["aroon_down"], mode),
        }

    return {
        "candles": _ohlcv_to_json(candles),
        "volume": _volume_to_json(candles),
        "supertrend": supertrend,
        "breakout": breakout,
        "macd": macd,
        "vortex": vortex,
        "adx": adx,
        "aroon": aroon,
    }


def _build_all_chart_data(
    price_data: pd.DataFrame,
    trade_log: pd.DataFrame,
    params: dict,
) -> dict:
    signal_freq = str(params.get("resample_interval", "30min"))
    signal, indicators = _build_indicator_frame(price_data, params)
    slow = _resample_ohlcv(price_data, "4h")

    chart_data = {
        "5m": _tf_data_from_signal(price_data, signal, indicators, mode="5m"),
        "signal": _tf_data_from_signal(signal, signal, indicators, mode="signal"),
        "4h": _tf_data_from_signal(slow, signal, indicators, mode="4h"),
        "markers": _build_markers(trade_log),
        "portfolio": _build_portfolio(trade_log),
        "range_labels": {
            "5m": "5m",
            "signal": signal_freq,
            "4h": "4H",
        },
    }
    return chart_data


class MACDVortexADXReporter:
    """Generate a dedicated technical report for MACD Vortex ADX."""

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        trade_log_path: str,
        price_data: pd.DataFrame,
        strategy_name: str,
        symbol: str,
        initial_cash: float,
        version: str = "",
        output_filename: str | None = None,
        auto_refresh_seconds: int | None = None,
        strategy_params: dict | None = None,
    ) -> str:
        params = strategy_params or {}
        report_timezone = str(params.get("report_timezone", "America/Los_Angeles"))
        trade_log = TradeLogReader.read(trade_log_path)
        cost_per_trade_pct = float(params.get("cost_per_trade_pct", 0.05))
        stats = compute_stats(
            trade_log, initial_cash, cost_per_trade_pct=cost_per_trade_pct
        )

        first_price = float(price_data["close"].iloc[0])
        last_price = float(price_data["close"].iloc[-1])
        bnh_return = (last_price / first_price - 1) * 100
        days = (price_data.index[-1] - price_data.index[0]).days
        bnh_years = days / 365.25 if days > 0 else 1.0
        bnh_cagr = (
            ((last_price / first_price) ** (1 / bnh_years) - 1) * 100
            if bnh_years > 0
            else 0.0
        )
        stats["bnh_return"] = bnh_return
        stats["bnh_cagr"] = bnh_cagr

        chart_data = _build_all_chart_data(price_data, trade_log, params)

        if not trade_log.empty:
            file_start_date = str(trade_log.iloc[0]["date"].date())
            file_end_date = str(trade_log.iloc[-1]["date"].date())
            display_start = _display_timestamp(trade_log.iloc[0]["date"], report_timezone)
            display_end = _display_timestamp(trade_log.iloc[-1]["date"], report_timezone)
        else:
            file_start_date = str(price_data.index[0].date())
            file_end_date = str(price_data.index[-1].date())
            display_start = _display_timestamp(price_data.index[0], report_timezone)
            display_end = _display_timestamp(price_data.index[-1], report_timezone)

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("macd_vortex_adx_report.html")

        html = template.render(
            strategy_name=strategy_name,
            is_st_vortex_adx=strategy_name == "st_vortex_adx",
            symbol=symbol,
            start_date=file_start_date,
            end_date=file_end_date,
            display_start=display_start.strftime("%Y-%m-%d %H:%M"),
            display_end=display_end.strftime("%Y-%m-%d %H:%M"),
            report_timezone=report_timezone,
            stats=stats,
            version=version,
            auto_refresh_seconds=auto_refresh_seconds,
            chart_data_json=json.dumps(chart_data),
            chart_range_labels=chart_data["range_labels"],
            chart_range_labels_json=json.dumps(chart_data["range_labels"]),
            signal_interval=str(params.get("resample_interval", "30min")),
            macd_fast=int(params.get("macd_fast", 12)),
            macd_slow=int(params.get("macd_slow", 26)),
            macd_signal=int(params.get("macd_signal", 9)),
            vortex_period=int(params.get("vortex_period", 14)),
            vortex_baseline_bars=int(params.get("vortex_baseline_bars", 3)),
            vortex_strong_spread_mult=float(
                params.get("vortex_strong_spread_mult", 1.25)
            ),
            vortex_hugging_spread_mult=float(
                params.get("vortex_hugging_spread_mult", 1.05)
            ),
            vortex_ema_period=int(params.get("vortex_ema_period", 3)),
            adx_period=int(params.get("adx_period", 14)),
            adx_floor=float(params.get("adx_floor", 20.0)),
            atr_period=int(params.get("atr_period", 14)),
            breakout_lookback_bars=int(params.get("breakout_lookback_bars", 3)),
            supertrend_atr_period=int(params.get("supertrend_atr_period", 12)),
            supertrend_multiplier=float(params.get("supertrend_multiplier", 1.5)),
            aroon_period=int(params.get("aroon_period", 14)),
            require_macd_above_zero_for_long=bool(
                params.get("require_macd_above_zero_for_long", False)
            ),
            trailing_stop_rth_only_for_equities=bool(
                params.get("trailing_stop_rth_only_for_equities", False)
            ),
            enable_short=bool(params.get("enable_short", True)),
            long_vol_ratio_enabled=bool(params.get("long_vol_ratio_enabled", False)),
            long_vol_ratio_min=float(params.get("long_vol_ratio_min", 0.0)),
            short_vol_ratio_enabled=bool(params.get("short_vol_ratio_enabled", False)),
            short_vol_ratio_min=float(params.get("short_vol_ratio_min", 0.0)),
            short_require_context_bearish=bool(
                params.get("short_require_context_bearish", False)
            ),
            short_context_interval=str(params.get("short_context_interval", "4h")),
            short_context_adx_floor=float(params.get("short_context_adx_floor", 20.0)),
            entry_cooldown_signal_bars=int(
                params.get("entry_cooldown_signal_bars", 0)
            ),
        )

        if output_filename is None:
            ver = f"_{version}" if version else ""
            output_filename = (
                f"{strategy_name}_{symbol}_{file_start_date}_{file_end_date}{ver}.html"
            )
        output_path = self.output_dir / output_filename
        output_path.write_text(html)
        return str(output_path)
