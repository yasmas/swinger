import pandas as pd
import pytest

from data_sources.parsers.nasdaq import NasdaqHistoricalParser
from data_sources.parsers.binance import BinanceKlineParser
from data_sources.parsers.base import STANDARD_COLUMNS

SAMPLE_NASDAQ_CSV = """Date,Close/Last,Volume,Open,High,Low
02/19/2026,603.47,60960840,602.81,605.815,600.75
02/18/2026,605.79,64250670,602.11,609.771,600.72
02/17/2026,601.30,69013760,598.375,603.95,593.34
"""

SAMPLE_BINANCE_CSV_MS = """open_time,open,high,low,close,volume,close_time,quote_asset_volume,number_of_trades,taker_buy_base_volume,taker_buy_quote_volume,ignore
1704067200000,42283.58000000,42397.23000000,42261.02000000,42397.23000000,155.25731000,1704067499999,6572925.83256960,6350,106.05732000,4490262.47081090,0
1704067500000,42397.22000000,42432.74000000,42385.26000000,42409.96000000,141.31102000,1704067799999,5993374.70276780,5134,69.45156000,2945451.61138490,0
"""

# Simulated microsecond timestamp (post-2025): 1737936000000000 = 2025-01-27 00:00:00 UTC in µs
SAMPLE_BINANCE_CSV_US = """open_time,open,high,low,close,volume,close_time,quote_asset_volume,number_of_trades,taker_buy_base_volume,taker_buy_quote_volume,ignore
1737936000000000,102000.00,102500.00,101500.00,102300.00,100.5,1737936299999999,10000000.00,5000,50.0,5000000.00,0
1737936300000000,102300.00,102800.00,102200.00,102700.00,120.3,1737936599999999,12000000.00,6000,60.0,6000000.00,0
"""


class TestNasdaqHistoricalParser:
    def test_parse_returns_dataframe_with_standard_columns(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)

        for col in STANDARD_COLUMNS:
            assert col in df.columns, f"Missing column: {col}"

    def test_parse_index_is_named_date(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)
        assert df.index.name == "date"

    def test_parse_dates_correctly(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)
        assert df.index[0] == pd.Timestamp("2026-02-17")
        assert df.index[-1] == pd.Timestamp("2026-02-19")

    def test_parse_sorts_by_date_ascending(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)
        assert list(df.index) == sorted(df.index)

    def test_close_last_maps_to_close(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)
        assert df.loc[pd.Timestamp("2026-02-19"), "close"] == 603.47

    def test_open_high_low_values_correct(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)
        row = df.loc[pd.Timestamp("2026-02-19")]
        assert row["open"] == 602.81
        assert row["high"] == 605.815
        assert row["low"] == 600.75

    def test_validate_passes(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)
        result = parser.validate(df)
        assert result is not None

    def test_row_count(self):
        parser = NasdaqHistoricalParser()
        df = parser.parse(SAMPLE_NASDAQ_CSV)
        assert len(df) == 3


class TestBinanceKlineParser:
    def test_parse_millisecond_timestamps(self):
        parser = BinanceKlineParser()
        df = parser.parse(SAMPLE_BINANCE_CSV_MS)

        for col in STANDARD_COLUMNS:
            assert col in df.columns
        assert df.index.name == "date"
        assert len(df) == 2

        expected_date = pd.Timestamp("2024-01-01 00:00:00")
        assert df.index[0] == expected_date

    def test_parse_microsecond_timestamps(self):
        parser = BinanceKlineParser()
        df = parser.parse(SAMPLE_BINANCE_CSV_US)

        expected_date = pd.Timestamp("2025-01-27 00:00:00")
        assert df.index[0] == expected_date
        assert df.loc[expected_date, "close"] == 102300.00

    def test_ohlcv_values_correct(self):
        parser = BinanceKlineParser()
        df = parser.parse(SAMPLE_BINANCE_CSV_MS)

        row = df.iloc[0]
        assert row["open"] == 42283.58
        assert row["high"] == 42397.23
        assert row["close"] == 42397.23
        assert row["low"] == 42261.02
        assert row["volume"] == pytest.approx(155.25731, abs=1e-5)

    def test_sorted_ascending(self):
        parser = BinanceKlineParser()
        df = parser.parse(SAMPLE_BINANCE_CSV_MS)
        assert list(df.index) == sorted(df.index)

    def test_validate_passes(self):
        parser = BinanceKlineParser()
        df = parser.parse(SAMPLE_BINANCE_CSV_MS)
        result = parser.validate(df)
        assert result is not None
