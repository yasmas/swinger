import os
import tempfile

import pytest

from trade_log import TradeLogger
from trade_replay import TradeReplayVerifier


def _create_correct_trade_log(path: str):
    """Create a trade log where all values are mathematically consistent."""
    initial_cash = 100000.0
    with TradeLogger(path) as logger:
        # BUY 2 units at 1000 -> cash = 98000, holdings = 2000, total = 100000
        logger.log("2025-01-01", "BUY", "TEST", 2.0, 1000.00, 98000.00, 100000.00)
        # HOLD, price rises to 1100 -> cash = 98000, holdings = 2200, total = 100200
        logger.log("2025-01-02", "HOLD", "TEST", 0, 1100.00, 98000.00, 100200.00)
        # SELL 2 units at 1200 -> cash = 100400, holdings = 0, total = 100400
        logger.log("2025-01-03", "SELL", "TEST", 2.0, 1200.00, 100400.00, 100400.00)


def _create_incorrect_trade_log(path: str):
    """Create a trade log with a deliberate P/L error."""
    with TradeLogger(path) as logger:
        logger.log("2025-01-01", "BUY", "TEST", 2.0, 1000.00, 98000.00, 100000.00)
        logger.log("2025-01-02", "HOLD", "TEST", 0, 1100.00, 98000.00, 100200.00)
        # Wrong: portfolio_value should be 100400 after selling 2 @ 1200
        logger.log("2025-01-03", "SELL", "TEST", 2.0, 1200.00, 100400.00, 999999.00)


class TestTradeReplayVerifier:
    def test_correct_log_has_no_discrepancies(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_correct_trade_log(path)
            verifier = TradeReplayVerifier()
            discrepancies = verifier.verify(path, initial_cash=100000.0)
            assert len(discrepancies) == 0
        finally:
            os.unlink(path)

    def test_incorrect_log_catches_portfolio_value_error(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_incorrect_trade_log(path)
            verifier = TradeReplayVerifier()
            discrepancies = verifier.verify(path, initial_cash=100000.0)
            assert len(discrepancies) > 0

            pv_errors = [d for d in discrepancies if d.field == "portfolio_value"]
            assert len(pv_errors) > 0
            assert pv_errors[0].expected == pytest.approx(999999.00)
            assert pv_errors[0].actual == pytest.approx(100400.00)
        finally:
            os.unlink(path)

    def test_correct_log_cash_tracking(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_correct_trade_log(path)
            verifier = TradeReplayVerifier()
            discrepancies = verifier.verify(path, initial_cash=100000.0)
            cash_errors = [d for d in discrepancies if d.field == "cash_balance"]
            assert len(cash_errors) == 0
        finally:
            os.unlink(path)

    def test_incorrect_initial_cash_creates_discrepancy(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            _create_correct_trade_log(path)
            verifier = TradeReplayVerifier()
            discrepancies = verifier.verify(path, initial_cash=50000.0)
            assert len(discrepancies) > 0
        finally:
            os.unlink(path)
