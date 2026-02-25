import os
import tempfile
import math

import pandas as pd
import pytest

from trade_log import TradeLogger
from reporting.reporter import Reporter, compute_stats, build_chart


def _create_test_trade_log(path: str, initial_cash: float = 100000) -> pd.DataFrame:
    """Create a deterministic trade log for testing. Returns the expected DataFrame."""
    with TradeLogger(path) as logger:
        logger.log("2025-01-01", "BUY", "TEST", 10.0, 100.00,
                    initial_cash - 1000, initial_cash, {"reason": "buy"})
        logger.log("2025-01-02", "HOLD", "TEST", 0, 105.00,
                    initial_cash - 1000, initial_cash - 1000 + 10 * 105, {"reason": "hold"})
        logger.log("2025-01-03", "HOLD", "TEST", 0, 95.00,
                    initial_cash - 1000, initial_cash - 1000 + 10 * 95, {"reason": "hold"})
        logger.log("2025-01-04", "SELL", "TEST", 10.0, 110.00,
                    initial_cash - 1000 + 10 * 110, initial_cash - 1000 + 10 * 110,
                    {"reason": "sell"})


def _make_price_data() -> pd.DataFrame:
    dates = pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"])
    return pd.DataFrame({
        "open": [100, 105, 95, 110],
        "high": [102, 107, 97, 112],
        "low": [98, 103, 93, 108],
        "close": [100, 105, 95, 110],
        "volume": [1000, 1000, 1000, 1000],
    }, index=dates).rename_axis("date")


class TestComputeStats:
    def test_total_return(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_test_trade_log(path)
            from trade_log import TradeLogReader
            df = TradeLogReader.read(path)
            stats = compute_stats(df, 100000)

            final_value = 100000 - 1000 + 10 * 110
            expected_return = (final_value / 100000 - 1) * 100
            assert stats["total_return"] == pytest.approx(expected_return, abs=0.01)
        finally:
            os.unlink(path)

    def test_max_drawdown(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_test_trade_log(path)
            from trade_log import TradeLogReader
            df = TradeLogReader.read(path)
            stats = compute_stats(df, 100000)

            assert stats["max_drawdown"] < 0
        finally:
            os.unlink(path)

    def test_trade_counts(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_test_trade_log(path)
            from trade_log import TradeLogReader
            df = TradeLogReader.read(path)
            stats = compute_stats(df, 100000)

            assert stats["num_buys"] == 1
            assert stats["num_sells"] == 1
            assert stats["num_trades"] == 2
        finally:
            os.unlink(path)


class TestBuildChart:
    def test_chart_returns_html(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_test_trade_log(path)
            from trade_log import TradeLogReader
            df = TradeLogReader.read(path)
            price_data = _make_price_data()

            html = build_chart(df, price_data, "TEST")
            assert "<div" in html
            assert "plotly" in html.lower() or "Plotly" in html
        finally:
            os.unlink(path)


class TestReporter:
    def test_generate_creates_html_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = os.path.join(tmp_dir, "test.csv")
            _create_test_trade_log(log_path)
            price_data = _make_price_data()

            reporter = Reporter(output_dir=tmp_dir)
            output_path = reporter.generate(
                trade_log_path=log_path,
                price_data=price_data,
                strategy_name="buy_and_hold",
                symbol="TEST",
                initial_cash=100000,
            )

            assert os.path.exists(output_path)
            assert output_path.endswith(".html")

            with open(output_path) as f:
                content = f.read()
            assert "buy_and_hold" in content
            assert "TEST" in content

    def test_pct_invested_zero_after_sell(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = os.path.join(tmp_dir, "test.csv")
            _create_test_trade_log(log_path)

            from trade_log import TradeLogReader
            df = TradeLogReader.read(log_path)

            last_row = df.iloc[-1]
            pct = (1 - last_row["cash_balance"] / last_row["portfolio_value"]) * 100
            assert pct == pytest.approx(0.0, abs=0.01)

    def test_buy_sell_marker_count(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = os.path.join(tmp_dir, "test.csv")
            _create_test_trade_log(log_path)

            from trade_log import TradeLogReader
            df = TradeLogReader.read(log_path)

            buys = (df["action"] == "BUY").sum()
            sells = (df["action"] == "SELL").sum()
            assert buys == 1
            assert sells == 1
