import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd


TRADE_LOG_COLUMNS = [
    "date",
    "action",
    "symbol",
    "quantity",
    "price",
    "cash_balance",
    "portfolio_value",
    "details",
]


class TradeLogger:
    """Writes trade actions to a CSV log file in the standard format."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.file_path, "w", newline="")
        self._writer = csv.writer(self._file, quoting=csv.QUOTE_MINIMAL)
        self._writer.writerow(TRADE_LOG_COLUMNS)

    def log(
        self,
        date: str,
        action: str,
        symbol: str,
        quantity: float,
        price: float,
        cash_balance: float,
        portfolio_value: float,
        details: Optional[dict] = None,
    ) -> None:
        details_str = json.dumps(details) if details else "{}"
        self._writer.writerow([
            date,
            action,
            symbol,
            f"{quantity:.8f}",
            f"{price:.2f}",
            f"{cash_balance:.2f}",
            f"{portfolio_value:.2f}",
            details_str,
        ])
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TradeLogReader:
    """Reads a trade log CSV back into a DataFrame."""

    @staticmethod
    def read(file_path: str) -> pd.DataFrame:
        df = pd.read_csv(file_path)
        df["date"] = pd.to_datetime(df["date"])
        df["details"] = df["details"].apply(json.loads)
        return df
