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
    _build_markers,
    _build_portfolio,
    _forward_fill_hist_to_5m,
    _forward_fill_series_to_5m,
    _histogram_to_json,
    _line_to_json,
    _ohlcv_to_json,
    _resample_hist_to_timeframe,
    _resample_ohlcv,
    _resample_series_to_timeframe,
    _volume_to_json,
)
from reporting.reporter import TEMPLATES_DIR, compute_stats
from strategies.intraday_indicators import compute_vortex
from strategies.macd_rsi_advanced import compute_adx, compute_atr, compute_macd
from trade_log import TradeLogReader


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
    adx_period = int(params.get("adx_period", 14))
    adx_floor = float(params.get("adx_floor", 20.0))
    atr_period = int(params.get("atr_period", 14))
    breakout_lookback_bars = int(params.get("breakout_lookback_bars", 3))

    macd_line, macd_signal_line, macd_hist = compute_macd(
        signal["close"], macd_fast, macd_slow, macd_signal
    )
    vi_plus, vi_minus = compute_vortex(
        signal["high"], signal["low"], signal["close"], vortex_period
    )
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
            "vi_plus": vi_plus,
            "vi_minus": vi_minus,
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
    elif mode == "signal":
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
    else:
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

    return {
        "candles": _ohlcv_to_json(candles),
        "volume": _volume_to_json(candles),
        "breakout": breakout,
        "macd": macd,
        "vortex": vortex,
        "adx": adx,
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
            adx_period=int(params.get("adx_period", 14)),
            adx_floor=float(params.get("adx_floor", 20.0)),
            atr_period=int(params.get("atr_period", 14)),
            breakout_lookback_bars=int(params.get("breakout_lookback_bars", 3)),
            require_macd_above_zero_for_long=bool(
                params.get("require_macd_above_zero_for_long", False)
            ),
            trailing_stop_rth_only_for_equities=bool(
                params.get("trailing_stop_rth_only_for_equities", False)
            ),
            enable_short=bool(params.get("enable_short", True)),
        )

        if output_filename is None:
            ver = f"_{version}" if version else ""
            output_filename = (
                f"{strategy_name}_{symbol}_{file_start_date}_{file_end_date}{ver}.html"
            )
        output_path = self.output_dir / output_filename
        output_path.write_text(html)
        return str(output_path)
