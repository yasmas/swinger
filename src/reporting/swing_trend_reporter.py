"""SwingTrend strategy reporter.

Forked from the LazySwing reporter so SwingTrend gets the same lightweight-
charts UI and timeframe switching, but without the LazySwing-specific
Supertrend overlay.
"""
import json
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from reporting.lazy_swing_reporter import (
    _build_markers,
    _build_portfolio,
    _ohlcv_to_json,
    _resample_ohlcv,
    _volume_to_json,
)
from reporting.reporter import TEMPLATES_DIR, compute_stats
from trade_log import TradeLogReader


def _build_all_chart_data(price_data: pd.DataFrame, trade_log: pd.DataFrame) -> dict:
    """Build chart data for all three SwingTrend timeframes."""
    # Lightweight-charts requires strictly increasing timestamps. Some source
    # files can contain duplicate 5m rows, so normalize once here before
    # generating any timeframe view.
    price_data = price_data.sort_index()
    if price_data.index.has_duplicates:
        price_data = price_data[~price_data.index.duplicated(keep="last")]

    h1 = _resample_ohlcv(price_data, "1h")
    h4 = _resample_ohlcv(price_data, "4h")

    return {
        "5m": {
            "candles": _ohlcv_to_json(price_data),
            "volume": _volume_to_json(price_data),
        },
        "1h": {
            "candles": _ohlcv_to_json(h1),
            "volume": _volume_to_json(h1),
        },
        "4h": {
            "candles": _ohlcv_to_json(h4),
            "volume": _volume_to_json(h4),
        },
        "markers": _build_markers(trade_log),
        "portfolio": _build_portfolio(trade_log),
        "range_labels": {
            "5m": "5m",
            "1h": "1H",
            "4h": "4H",
        },
    }


class SwingTrendReporter:
    """Generate a SwingTrend HTML report with the LazySwing-style UI."""

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
        del strategy_params  # unused, kept for run_backtest interface compatibility

        trade_log = TradeLogReader.read(trade_log_path)
        stats = compute_stats(trade_log, initial_cash)

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

        chart_data = _build_all_chart_data(price_data, trade_log)

        if not trade_log.empty:
            start_date = str(trade_log.iloc[0]["date"].date())
            end_date = str(trade_log.iloc[-1]["date"].date())
        else:
            start_date = str(price_data.index[0].date())
            end_date = str(price_data.index[-1].date())

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("swing_trend_report.html")

        html = template.render(
            strategy_name=strategy_name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            stats=stats,
            version=version,
            auto_refresh_seconds=auto_refresh_seconds,
            chart_data_json=json.dumps(chart_data),
            chart_range_labels=chart_data["range_labels"],
            chart_range_labels_json=json.dumps(chart_data["range_labels"]),
        )

        if output_filename is None:
            ver = f"_{version}" if version else ""
            output_filename = f"{strategy_name}_{symbol}_{start_date}_{end_date}{ver}.html"
        output_path = self.output_dir / output_filename

        with open(output_path, "w") as f:
            f.write(html)

        return str(output_path)
