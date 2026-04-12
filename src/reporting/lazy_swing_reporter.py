"""LazySwing strategy reporter.

Uses lightweight-charts for OHLC candlestick chart with Supertrend overlay
and trade markers (BUY/SELL/SHORT/COVER). No RSI/MACD subplots — just
price + ST + portfolio value.

Three timeframe views: 1W (5m bars), 1M (1h bars), 6M (4h bars).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader

from trade_log import TradeLogReader
from reporting.reporter import compute_stats, posix_utc_seconds, TEMPLATES_DIR
from strategies.intraday_indicators import compute_supertrend

# Supertrend line colors
_ST_BULL_COLOR = "#26a69a"
_ST_BEAR_COLOR = "#ef5350"


def _resample_ohlcv(price_data: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample OHLCV to the given frequency."""
    return price_data.resample(freq).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])


def _compute_st_on_hourly(price_data: pd.DataFrame, params: dict):
    """Compute Supertrend on 1h bars. Returns (st_line, st_bull_bool) Series."""
    st_atr_period = int(params.get("supertrend_atr_period", 13))
    st_multiplier = float(params.get("supertrend_multiplier", 2.5))

    h1 = _resample_ohlcv(price_data, "1h")
    st_line, st_bull = compute_supertrend(
        h1["high"], h1["low"], h1["close"], st_atr_period, st_multiplier,
    )
    return h1, st_line, st_bull.astype(bool)


def _ohlcv_to_json(df: pd.DataFrame) -> list[dict]:
    """Convert OHLCV DataFrame to lightweight-charts candle format."""
    candles = []
    for ts, row in df.iterrows():
        candles.append({
            "time": posix_utc_seconds(ts),
            "open": round(float(row["open"]), 2),
            "high": round(float(row["high"]), 2),
            "low": round(float(row["low"]), 2),
            "close": round(float(row["close"]), 2),
        })
    return candles


def _volume_to_json(df: pd.DataFrame) -> list[dict]:
    """Convert OHLCV DataFrame to lightweight-charts histogram (volume) format."""
    volume = []
    for ts, row in df.iterrows():
        volume.append({
            "time": posix_utc_seconds(ts),
            "value": round(float(row["volume"]), 2),
            "color": "#22c55e30" if row["close"] >= row["open"] else "#ef444430",
        })
    return volume


def _st_to_json(st_line: pd.Series, st_bull: pd.Series) -> list[dict]:
    """Build a single ST line with per-point color (green=bull, red=bear)."""
    data = []
    for ts in st_line.index:
        val = st_line[ts]
        if pd.isna(val):
            continue
        bull = bool(st_bull[ts])
        data.append({
            "time": posix_utc_seconds(ts),
            "value": round(float(val), 2),
            "color": _ST_BULL_COLOR if bull else _ST_BEAR_COLOR,
        })
    return data


def _resample_st_to_timeframe(
    st_line: pd.Series, st_bull: pd.Series, freq: str
) -> list[dict]:
    """Forward-fill 1h ST values to a coarser timeframe and return JSON."""
    if freq == "1h":
        return _st_to_json(st_line, st_bull)

    # Resample: take last value per period (ST is a level, not OHLC)
    st_resampled = st_line.resample(freq).last().dropna()
    bull_resampled = st_bull.resample(freq).last().dropna()

    data = []
    for ts in st_resampled.index:
        val = st_resampled[ts]
        if pd.isna(val) or ts not in bull_resampled.index:
            continue
        bull = bool(bull_resampled[ts])
        data.append({
            "time": posix_utc_seconds(ts),
            "value": round(float(val), 2),
            "color": _ST_BULL_COLOR if bull else _ST_BEAR_COLOR,
        })
    return data


def _forward_fill_st_to_5m(
    st_line: pd.Series, st_bull: pd.Series, index_5m: pd.DatetimeIndex
) -> list[dict]:
    """Forward-fill 1h ST values onto the 5m index."""
    st_5m = st_line.reindex(index_5m, method="ffill")
    bull_5m = st_bull.reindex(index_5m, method="ffill")

    data = []
    for ts, val, b in zip(st_5m.index, st_5m.values, bull_5m.values):
        if pd.isna(val) or pd.isna(b):
            continue
        data.append({
            "time": posix_utc_seconds(ts),
            "value": round(float(val), 2),
            "color": _ST_BULL_COLOR if bool(b) else _ST_BEAR_COLOR,
        })
    return data


def _build_markers(trade_log: pd.DataFrame) -> list[dict]:
    """Build trade markers (not snapped — template handles snapping)."""
    if trade_log.empty:
        return []

    actions_df = trade_log[trade_log["action"].isin(["BUY", "SELL", "SHORT", "COVER"])]
    markers = []
    for _, row in actions_df.iterrows():
        action = row["action"]
        markers.append({
            "time": posix_utc_seconds(row["date"]),
            "position": "belowBar" if action in ("BUY", "COVER") else "aboveBar",
            "color": "#22c55e" if action in ("BUY", "COVER") else "#ef4444",
            "shape": "arrowUp" if action in ("BUY", "COVER") else "arrowDown",
            "text": action,
        })
    markers.sort(key=lambda m: m["time"])
    return markers


def _build_portfolio(trade_log: pd.DataFrame) -> list[dict]:
    """Build portfolio value time series, sampled hourly."""
    if trade_log.empty:
        return []
    pv = trade_log.set_index("date")["portfolio_value"].resample("1h").last().dropna()
    return [
        {"time": posix_utc_seconds(ts), "value": round(float(val), 2)}
        for ts, val in pv.items()
    ]


def _build_all_chart_data(
    price_data: pd.DataFrame,
    trade_log: pd.DataFrame,
    params: dict,
) -> dict:
    """Build chart data for all three timeframes."""
    # Compute ST on 1h (the strategy's native timeframe)
    h1, st_line, st_bull = _compute_st_on_hourly(price_data, params)

    # 5m candles (raw data)
    candles_5m = _ohlcv_to_json(price_data)
    volume_5m = _volume_to_json(price_data)
    st_5m = _forward_fill_st_to_5m(st_line, st_bull, price_data.index)

    # 1h candles
    candles_1h = _ohlcv_to_json(h1)
    volume_1h = _volume_to_json(h1)
    st_1h = _st_to_json(st_line, st_bull)

    # 4h candles
    h4 = _resample_ohlcv(price_data, "4h")
    candles_4h = _ohlcv_to_json(h4)
    volume_4h = _volume_to_json(h4)
    st_4h = _resample_st_to_timeframe(st_line, st_bull, "4h")

    markers = _build_markers(trade_log)
    portfolio = _build_portfolio(trade_log)

    return {
        "5m":  {"candles": candles_5m,  "st": st_5m, "volume": volume_5m},
        "1h":  {"candles": candles_1h,  "st": st_1h, "volume": volume_1h},
        "4h":  {"candles": candles_4h,  "st": st_4h, "volume": volume_4h},
        "markers": markers,
        "portfolio": portfolio,
    }


class LazySwingReporter:
    """Generates a LazySwing HTML report with lightweight-charts candlestick + Supertrend."""

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
        """Generate HTML report and return the output file path."""
        params = strategy_params or {}
        trade_log = TradeLogReader.read(trade_log_path)
        stats = compute_stats(trade_log, initial_cash)

        # Buy-and-hold benchmark
        first_price = float(price_data["close"].iloc[0])
        last_price = float(price_data["close"].iloc[-1])
        bnh_return = (last_price / first_price - 1) * 100
        days = (price_data.index[-1] - price_data.index[0]).days
        bnh_years = days / 365.25 if days > 0 else 1.0
        bnh_cagr = (
            ((last_price / first_price) ** (1 / bnh_years) - 1) * 100
            if bnh_years > 0 else 0.0
        )
        stats["bnh_return"] = bnh_return
        stats["bnh_cagr"] = bnh_cagr

        chart_data = _build_all_chart_data(price_data, trade_log, params)

        if not trade_log.empty:
            start_date = str(trade_log.iloc[0]["date"].date())
            end_date = str(trade_log.iloc[-1]["date"].date())
        else:
            start_date = str(price_data.index[0].date())
            end_date = str(price_data.index[-1].date())

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("lazy_swing_report.html")

        st_atr = int(params.get("supertrend_atr_period", 13))
        st_mult = float(params.get("supertrend_multiplier", 2.5))

        html = template.render(
            strategy_name=strategy_name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            stats=stats,
            version=version,
            auto_refresh_seconds=auto_refresh_seconds,
            chart_data_json=json.dumps(chart_data),
            st_atr_period=st_atr,
            st_multiplier=st_mult,
        )

        if output_filename is None:
            ver = f"_{version}" if version else ""
            output_filename = (
                f"{strategy_name}_{symbol}_{start_date}_{end_date}{ver}.html"
            )
        output_path = self.output_dir / output_filename

        with open(output_path, "w") as f:
            f.write(html)

        return str(output_path)
