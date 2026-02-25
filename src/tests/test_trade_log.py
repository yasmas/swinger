import os
import tempfile

import pytest

from trade_log import TradeLogger, TradeLogReader, TRADE_LOG_COLUMNS


class TestTradeLogRoundTrip:
    def test_write_and_read_back(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            with TradeLogger(path) as logger:
                logger.log(
                    date="2025-01-01",
                    action="BUY",
                    symbol="BTC",
                    quantity=1.5,
                    price=42000.00,
                    cash_balance=37000.00,
                    portfolio_value=100000.00,
                    details={"reason": "test buy"},
                )
                logger.log(
                    date="2025-01-02",
                    action="HOLD",
                    symbol="BTC",
                    quantity=0,
                    price=43000.00,
                    cash_balance=37000.00,
                    portfolio_value=101500.00,
                    details={"reason": "holding"},
                )
                logger.log(
                    date="2025-01-03",
                    action="SELL",
                    symbol="BTC",
                    quantity=1.5,
                    price=44000.00,
                    cash_balance=103000.00,
                    portfolio_value=103000.00,
                    details={"reason": "test sell"},
                )

            df = TradeLogReader.read(path)

            assert len(df) == 3
            for col in TRADE_LOG_COLUMNS:
                assert col in df.columns

            assert df.iloc[0]["action"] == "BUY"
            assert df.iloc[1]["action"] == "HOLD"
            assert df.iloc[2]["action"] == "SELL"

            assert df.iloc[0]["symbol"] == "BTC"
            assert df.iloc[2]["quantity"] == pytest.approx(1.5)
            assert df.iloc[2]["cash_balance"] == pytest.approx(103000.00)

        finally:
            os.unlink(path)

    def test_details_json_round_trip(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            details = {"ma_short": 42500.0, "ma_long": 41000.0, "rsi": 65.3}
            with TradeLogger(path) as logger:
                logger.log(
                    date="2025-01-01",
                    action="BUY",
                    symbol="BTC",
                    quantity=1.0,
                    price=42000.00,
                    cash_balance=58000.00,
                    portfolio_value=100000.00,
                    details=details,
                )

            df = TradeLogReader.read(path)
            recovered = df.iloc[0]["details"]
            assert recovered["ma_short"] == pytest.approx(42500.0)
            assert recovered["rsi"] == pytest.approx(65.3)

        finally:
            os.unlink(path)

    def test_empty_details_default(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name

        try:
            with TradeLogger(path) as logger:
                logger.log(
                    date="2025-01-01",
                    action="HOLD",
                    symbol="BTC",
                    quantity=0,
                    price=42000.00,
                    cash_balance=100000.00,
                    portfolio_value=100000.00,
                )

            df = TradeLogReader.read(path)
            assert df.iloc[0]["details"] == {}

        finally:
            os.unlink(path)
