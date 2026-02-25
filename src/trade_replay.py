from dataclasses import dataclass

import pandas as pd

from trade_log import TradeLogReader


@dataclass
class ReplayDiscrepancy:
    row_index: int
    date: str
    field: str
    expected: float
    actual: float
    difference: float


class TradeReplayVerifier:
    """Replays a trade log CSV independently and verifies P/L calculations.

    Reads the trade log, replays all BUY/SELL actions from initial_cash,
    and compares the recalculated portfolio_value against what's in the log.
    """

    def __init__(self, tolerance: float = 0.02):
        self.tolerance = tolerance

    def verify(self, trade_log_path: str, initial_cash: float) -> list[ReplayDiscrepancy]:
        df = TradeLogReader.read(trade_log_path)
        discrepancies = []

        cash = initial_cash
        holdings: dict[str, float] = {}  # symbol -> quantity

        for idx, row in df.iterrows():
            action = row["action"]
            symbol = row["symbol"]
            quantity = float(row["quantity"])
            price = float(row["price"])

            if action == "BUY" and quantity > 0:
                cash -= quantity * price
                holdings[symbol] = holdings.get(symbol, 0.0) + quantity
            elif action == "SELL" and quantity > 0:
                cash += quantity * price
                holdings[symbol] = holdings.get(symbol, 0.0) - quantity
                if abs(holdings[symbol]) < 1e-12:
                    del holdings[symbol]

            expected_cash = float(row["cash_balance"])
            if abs(cash - expected_cash) > self.tolerance:
                discrepancies.append(ReplayDiscrepancy(
                    row_index=int(idx),
                    date=str(row["date"]),
                    field="cash_balance",
                    expected=expected_cash,
                    actual=cash,
                    difference=cash - expected_cash,
                ))

            holdings_value = sum(
                qty * price for sym, qty in holdings.items()
            )
            replay_portfolio_value = cash + holdings_value
            logged_portfolio_value = float(row["portfolio_value"])

            if abs(replay_portfolio_value - logged_portfolio_value) > self.tolerance:
                discrepancies.append(ReplayDiscrepancy(
                    row_index=int(idx),
                    date=str(row["date"]),
                    field="portfolio_value",
                    expected=logged_portfolio_value,
                    actual=replay_portfolio_value,
                    difference=replay_portfolio_value - logged_portfolio_value,
                ))

        return discrepancies
