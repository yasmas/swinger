import io

import pandas as pd

from .base import DataParserBase


class NasdaqHistoricalParser(DataParserBase):
    """Parses Nasdaq historical CSV format.

    Expected columns: Date, Close/Last, Volume, Open, High, Low
    Date format: MM/DD/YYYY
    """

    def parse(self, raw_data: str) -> pd.DataFrame:
        df = pd.read_csv(io.StringIO(raw_data))

        column_map = {
            "Date": "date",
            "Close/Last": "close",
            "Volume": "volume",
            "Open": "open",
            "High": "high",
            "Low": "low",
        }
        df = df.rename(columns=column_map)

        df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y")
        df = df.set_index("date")

        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype(float)

        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()

        return df
