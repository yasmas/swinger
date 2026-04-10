from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from .parsers.base import DataParserBase


class DataSourceBase(ABC):
    """Knows where/how to fetch raw data. Delegates parsing to a DataParser."""

    def __init__(self, parser: DataParserBase, config: dict):
        self.parser = parser
        self.config = config

    @abstractmethod
    def fetch_raw(self, symbol: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> Any:
        """Fetch raw data from the underlying source."""
        pass

    def get_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Fetch + parse + date-filter. This is what the controller calls."""
        start = pd.Timestamp(start_date)
        end = pd.Timestamp(end_date)
        # Inclusive calendar end: include all bars on end_date through end of that day
        start_ns = start.normalize()
        end_exclusive = end.normalize() + pd.Timedelta(days=1)

        raw = self.fetch_raw(symbol, start, end)
        df = self.parser.parse(raw)
        df = self.parser.validate(df)

        return df[(df.index >= start_ns) & (df.index < end_exclusive)]
