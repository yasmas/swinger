import io

import pandas as pd

from .base import DataParserBase

# Binance switched from millisecond to microsecond timestamps on 2025-01-01.
# Microsecond timestamps are > 1e15, millisecond timestamps are < 1e15.
_MICROSECOND_THRESHOLD = 1e15


class BinanceKlineParser(DataParserBase):
    """Parses Binance kline CSV format (as downloaded from data.binance.vision).

    Expected columns (with header row):
        open_time, open, high, low, close, volume, close_time,
        quote_asset_volume, number_of_trades, taker_buy_base_volume,
        taker_buy_quote_volume, ignore
    """

    def parse(self, raw_data: str) -> pd.DataFrame:
        df = pd.read_csv(io.StringIO(raw_data))

        timestamps = df["open_time"].astype(float)
        ms_timestamps = timestamps.where(
            timestamps < _MICROSECOND_THRESHOLD,
            timestamps / 1000,
        )
        df["date"] = pd.to_datetime(ms_timestamps, unit="ms", utc=True)
        df["date"] = df["date"].dt.tz_localize(None)

        df = df.set_index("date")

        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype(float)

        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()

        return df
