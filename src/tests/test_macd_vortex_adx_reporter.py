import os
import tempfile

import pandas as pd

from reporting.macd_vortex_adx_reporter import (
    MACDVortexADXReporter,
    _build_all_chart_data,
)
from trade_log import TradeLogger


DEFAULT_PARAMS = {
    "resample_interval": "30min",
    "macd_fast": 15,
    "macd_slow": 33,
    "macd_signal": 9,
    "use_histogram_flip": True,
    "macd_fresh_bars": 2,
    "require_macd_above_zero_for_long": True,
    "vortex_period": 21,
    "vortex_baseline_bars": 5,
    "vortex_strong_spread_mult": 1.25,
    "vortex_hugging_spread_mult": 1.05,
    "vortex_weave_lookback": 2,
    "adx_period": 20,
    "adx_floor": 30,
    "require_adx_rising": True,
    "breakout_lookback_bars": 3,
    "armed_breakout_expiry_bars": 2,
    "atr_period": 20,
    "atr_stop_multiplier": 2.0,
    "atr_trailing_multiplier": 1.5,
    "report_timezone": "America/Los_Angeles",
    "trailing_stop_rth_only_for_equities": True,
    "enable_short": False,
}


def _make_price_data() -> pd.DataFrame:
    idx = pd.date_range("2026-01-02 09:30", periods=240, freq="5min")
    closes = [100.0 + (i * 0.18) + ((i % 6) - 2.5) * 0.04 for i in range(len(idx))]
    price_data = pd.DataFrame(
        {
            "open": [c - 0.05 for c in closes],
            "high": [c + 0.35 for c in closes],
            "low": [c - 0.35 for c in closes],
            "close": closes,
            "volume": [1000.0 + (i * 8.0) for i in range(len(idx))],
        },
        index=idx,
    )
    price_data.index.name = "date"
    return price_data


def _create_trade_log(path: str, price_data: pd.DataFrame) -> None:
    with TradeLogger(path) as logger:
        logger.log(
            str(price_data.index[60]),
            "BUY",
            "TEST",
            100.0,
            float(price_data["close"].iloc[60]),
            90000.0,
            100000.0,
            {"reason": "Immediate long entry", "entry_reason": "macd_vortex_adx_immediate_long"},
        )
        logger.log(
            str(price_data.index[120]),
            "HOLD",
            "TEST",
            0.0,
            float(price_data["close"].iloc[120]),
            90000.0,
            101200.0,
            {"reason": "Holding"},
        )
        logger.log(
            str(price_data.index[180]),
            "SELL",
            "TEST",
            100.0,
            float(price_data["close"].iloc[180]),
            101500.0,
            101500.0,
            {"reason": "Trailing stop hit", "exit_reason": "trailing_stop"},
        )


class TestMACDVortexADXReportData:
    def test_build_chart_data_contains_all_indicator_groups(self):
        price_data = _make_price_data()
        trade_log = pd.DataFrame(
            columns=["date", "action", "price", "quantity", "portfolio_value", "details"]
        )

        chart_data = _build_all_chart_data(price_data, trade_log, DEFAULT_PARAMS)

        assert chart_data["range_labels"]["signal"] == "30min"
        assert chart_data["5m"]["candles"]
        assert chart_data["signal"]["candles"]
        assert chart_data["4h"]["candles"]
        assert chart_data["signal"]["breakout"]["high"]
        assert chart_data["signal"]["breakout"]["low"]
        assert chart_data["signal"]["macd"]["line"]
        assert chart_data["signal"]["macd"]["hist"]
        assert chart_data["signal"]["vortex"]["plus"]
        assert chart_data["signal"]["vortex"]["spread"]
        assert chart_data["signal"]["vortex"]["strong"]
        assert chart_data["signal"]["adx"]["adx"]
        assert chart_data["signal"]["adx"]["floor"]
        assert chart_data["signal"]["adx"]["atr"]


class TestMACDVortexADXReporter:
    def test_reporter_generates_indicator_html(self):
        price_data = _make_price_data()

        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = os.path.join(tmp_dir, "macd_vortex_adx.csv")
            _create_trade_log(log_path, price_data)

            reporter = MACDVortexADXReporter(output_dir=tmp_dir)
            output_path = reporter.generate(
                trade_log_path=log_path,
                price_data=price_data,
                strategy_name="MACD Vortex ADX",
                symbol="TEST",
                initial_cash=100000,
                version="vtest",
                strategy_params=DEFAULT_PARAMS,
            )

            assert os.path.exists(output_path)
            with open(output_path) as f:
                content = f.read()

            assert "Price + breakout references" in content
            assert "MACD(15, 33, 9)" in content
            assert "Vortex(21)" in content
            assert "ADX(20) + ATR(20)" in content
            assert "Times shown in America/Los_Angeles" in content
            assert "Equity trailing stops RTH-only: on" in content
            assert 'id="price-chart"' in content
            assert 'id="macd-chart"' in content
            assert 'id="vortex-chart"' in content
            assert 'id="adx-chart"' in content
            assert 'id="portfolio-chart"' in content
