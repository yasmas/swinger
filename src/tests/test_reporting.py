import os
import tempfile
import math
from unittest.mock import patch

import pandas as pd
import pytest

from trade_log import TradeLogger
from reporting.reporter import Reporter, compute_stats, build_chart
from reporting.swing_party_reporter import (
    SwingPartyReporter,
    build_swing_party_chart_data,
    build_trade_table_rows,
    held_flags_at_bar_times,
)


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


class TestSwingPartyReport:
    def test_held_flags_follows_position_qty(self):
        t0 = pd.Timestamp("2025-01-01 10:00")
        t1 = pd.Timestamp("2025-01-01 11:00")
        t2 = pd.Timestamp("2025-01-01 12:00")
        log = pd.DataFrame(
            {
                "date": [t0, t1],
                "symbol": ["AAA", "AAA"],
                "position_qty": [10.0, 0.0],
                "short_qty": [0.0, 0.0],
            }
        )
        bars = pd.DatetimeIndex([t0, t1, t2])
        h = held_flags_at_bar_times(log, "AAA", bars)
        assert h.tolist() == [True, False, False]

    def test_build_chart_data_has_timeframes_and_series(self):
        idx = pd.date_range("2025-01-01", periods=5, freq="1h")
        df = pd.DataFrame(
            {
                "open": [100.0, 100, 100, 100, 100],
                "high": [101.0, 101, 101, 101, 101],
                "low": [99.0, 99, 99, 99, 99],
                "close": [100.0, 102, 104, 103, 105],
                "volume": [1000.0] * 5,
            },
            index=idx,
        )
        datasets = {"AAA": df, "BBB": df}
        log = pd.DataFrame(
            {
                "date": [idx[1]],
                "symbol": ["AAA"],
                "position_qty": [5.0],
                "short_qty": [0.0],
                "portfolio_value": [100_000.0],
            }
        )
        bundle = build_swing_party_chart_data(datasets, log)
        assert "5m" in bundle and "1h" in bundle and "4h" in bundle
        assert "portfolio" in bundle
        assert "AAA" in bundle["1h"]
        sol = bundle["1h"]["AAA"]["solid"]
        dot = bundle["1h"]["AAA"]["dotted"]
        assert isinstance(sol, list) and isinstance(dot, list)
        assert all(isinstance(s, list) for s in sol)
        assert all(isinstance(s, list) for s in dot)

    @patch("reporting.swing_party_reporter.load_multi_asset_datasets")
    def test_swing_party_reporter_generates_html(self, mock_load, tmp_path):
        idx = pd.date_range("2025-01-01", periods=10, freq="1h")
        ohlcv = pd.DataFrame(
            {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1000.0,
            },
            index=idx,
        )
        mock_load.return_value = {"AAA": ohlcv.copy(), "BBB": ohlcv.copy()}

        log_path = tmp_path / "log.csv"
        initial = 100_000.0
        with TradeLogger(str(log_path)) as logger:
            logger.log(
                str(idx[0]),
                "BUY",
                "AAA",
                1.0,
                100.0,
                initial - 100.0,
                initial,
                {},
                position_qty=1.0,
                position_avg_cost=100.0,
            )
            logger.log(
                str(idx[2]),
                "SELL",
                "AAA",
                1.0,
                100.0,
                initial,
                initial,
                {},
                position_qty=0.0,
                position_avg_cost=0.0,
            )

        config = {
            "backtest": {
                "name": "unit_test",
                "start_date": "2025-01-01",
                "end_date": "2025-01-02",
                "initial_cash": initial,
                "version": "",
            },
            "data_source": {"type": "file", "parser": "noop"},
            "strategy": {
                "assets": ["AAA", "BBB"],
                "cost_per_trade_pct": 0.05,
                "supertrend_atr_period": 10,
                "supertrend_multiplier": 2.0,
                "max_positions": 2,
            },
        }

        out_dir = tmp_path / "reports"
        path = SwingPartyReporter(output_dir=str(out_dir)).generate(
            trade_log_path=str(log_path),
            config=config,
            strategy_name="swing_party",
        )
        assert os.path.isfile(path)
        text = open(path).read()
        assert "Normalized %" in text
        assert "Trades" in text
        assert "Portfolio Value" in text
        assert "AAA" in text and "BBB" in text

    def test_build_trade_table_rows_long_round_trip(self):
        idx = pd.date_range("2025-01-01", periods=2, freq="1h")
        log = pd.DataFrame(
            {
                "date": [idx[0], idx[1]],
                "action": ["BUY", "SELL"],
                "symbol": ["AAA", "AAA"],
                "quantity": [10.0, 10.0],
                "price": [100.0, 110.0],
                "cash_balance": [0.0, 0.0],
                "portfolio_value": [1000.0, 1100.0],
                "position_qty": [10.0, 0.0],
                "position_avg_cost": [100.0, 0.0],
                "short_qty": [0.0, 0.0],
                "short_avg_cost": [0.0, 0.0],
                "details": [{}, {}],
            }
        )
        rows = build_trade_table_rows(log)
        assert len(rows) == 2
        assert rows[0]["trade_type"] == "BUY"
        assert rows[0]["pnl_dollar"] is None
        assert rows[0]["portfolio_value"] is None
        assert "time_unix" in rows[0]
        assert rows[1]["trade_type"] == "SELL"
        assert rows[1]["pnl_dollar"] == pytest.approx(100.0)
        assert rows[1]["pnl_pct"] == pytest.approx(10.0)
        assert rows[1]["portfolio_value"] == pytest.approx(1100.0)
        assert rows[0]["highlight_start_unix"] == rows[0]["time_unix"]
        assert rows[0]["highlight_end_unix"] == rows[1]["time_unix"]
        assert rows[1]["highlight_start_unix"] == rows[0]["time_unix"]
        assert rows[1]["highlight_end_unix"] == rows[1]["time_unix"]

    def test_build_trade_table_rows_short_round_trip(self):
        log = pd.DataFrame(
            {
                "date": [pd.Timestamp("2025-01-01 10:00"), pd.Timestamp("2025-01-02 10:00")],
                "action": ["SHORT", "COVER"],
                "symbol": ["ZZZ", "ZZZ"],
                "quantity": [5.0, 5.0],
                "price": [200.0, 180.0],
                "cash_balance": [0.0, 0.0],
                "portfolio_value": [1000.0, 1000.0],
                "position_qty": [0.0, 0.0],
                "position_avg_cost": [0.0, 0.0],
                "short_qty": [5.0, 0.0],
                "short_avg_cost": [200.0, 0.0],
                "details": [{}, {}],
            }
        )
        rows = build_trade_table_rows(log)
        assert len(rows) == 2
        assert rows[0]["pnl_dollar"] is None
        assert rows[0]["portfolio_value"] is None
        assert rows[1]["pnl_dollar"] == pytest.approx(100.0)
        assert rows[1]["pnl_pct"] == pytest.approx(10.0)
        assert rows[1]["portfolio_value"] == pytest.approx(1000.0)
        assert rows[0]["highlight_start_unix"] == rows[0]["time_unix"]
        assert rows[0]["highlight_end_unix"] == rows[1]["time_unix"]
        assert rows[1]["highlight_start_unix"] == rows[0]["time_unix"]
        assert rows[1]["highlight_end_unix"] == rows[1]["time_unix"]
