from abc import ABC, abstractmethod

import pandas as pd


STANDARD_COLUMNS = ["open", "high", "low", "close", "volume"]


class DataParserBase(ABC):
    """Knows how to normalize a specific raw data format into
    the standard OHLCV DataFrame with a datetime index named 'date'."""

    @abstractmethod
    def parse(self, raw_data: str) -> pd.DataFrame:
        """Parse raw data into a standardized OHLCV DataFrame.

        Returns:
            DataFrame with DatetimeIndex named 'date' and columns:
            open (float), high (float), low (float), close (float), volume (float).
            Sorted by date ascending.
        """
        pass

    def validate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Validate that the DataFrame has the expected schema."""
        missing = set(STANDARD_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"Parsed DataFrame is missing columns: {missing}")
        if df.index.name != "date":
            raise ValueError(f"Index must be named 'date', got '{df.index.name}'")
        return df
