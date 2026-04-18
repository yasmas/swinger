import os

import pandas as pd
import pytest

from data_sources.csv_file import CsvFileDataSource
from data_sources.parsers.nasdaq import NasdaqHistoricalParser
from data_sources.parsers.binance import BinanceKlineParser
from data_sources.registry import PARSER_REGISTRY, DATA_SOURCE_REGISTRY

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


class TestCsvFileDataSourceWithNasdaq:
    @pytest.fixture
    def qqq_source(self):
        parser = NasdaqHistoricalParser()
        config = {"file_path": os.path.join(DATA_DIR, "QQQ-HistoricalData.csv")}
        return CsvFileDataSource(parser, config)

    def test_get_data_returns_dataframe(self, qqq_source):
        df = qqq_source.get_data("QQQ", "2025-01-01", "2025-12-31")
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_get_data_date_filtering(self, qqq_source):
        df = qqq_source.get_data("QQQ", "2025-06-01", "2025-06-30")
        assert df.index.min() >= pd.Timestamp("2025-06-01")
        assert df.index.max() <= pd.Timestamp("2025-06-30")

    def test_get_data_has_standard_columns(self, qqq_source):
        df = qqq_source.get_data("QQQ", "2025-01-01", "2025-03-31")
        for col in ["open", "high", "low", "close", "volume"]:
            assert col in df.columns

    def test_get_data_empty_for_out_of_range(self, qqq_source):
        df = qqq_source.get_data("QQQ", "2000-01-01", "2000-12-31")
        assert len(df) == 0


class TestCsvFileDataSourceWithBinance:
    @pytest.fixture
    def btc_source(self):
        parser = BinanceKlineParser()
        config = {"file_path": os.path.join(DATA_DIR, "BTCUSDT-5m-20240101-20260131.csv")}
        return CsvFileDataSource(parser, config)

    def test_get_data_returns_dataframe(self, btc_source):
        df = btc_source.get_data("BTCUSDT", "2024-06-01", "2024-06-30")
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    def test_get_data_date_filtering(self, btc_source):
        df = btc_source.get_data("BTCUSDT", "2024-03-01", "2024-03-31")
        assert df.index.min() >= pd.Timestamp("2024-03-01")
        assert df.index.max() <= pd.Timestamp("2024-03-31")

    def test_get_data_spans_timestamp_format_boundary(self, btc_source):
        """Data crosses the ms->µs boundary at 2025-01-01."""
        df = btc_source.get_data("BTCUSDT", "2024-12-15", "2025-01-15")
        assert df.index.min() >= pd.Timestamp("2024-12-15")
        assert df.index.max() <= pd.Timestamp("2025-01-15")
        assert len(df) > 0


class TestRegistries:
    def test_parser_registry_contains_nasdaq(self):
        assert "nasdaq_historical" in PARSER_REGISTRY
        assert PARSER_REGISTRY["nasdaq_historical"] is NasdaqHistoricalParser

    def test_parser_registry_contains_binance(self):
        assert "binance_kline" in PARSER_REGISTRY
        assert PARSER_REGISTRY["binance_kline"] is BinanceKlineParser

    def test_parser_registry_coinbase_intx_alias(self):
        assert PARSER_REGISTRY["coinbase_intx_kline"] is BinanceKlineParser

    def test_data_source_registry_contains_csv_file(self):
        assert "csv_file" in DATA_SOURCE_REGISTRY
        assert DATA_SOURCE_REGISTRY["csv_file"] is CsvFileDataSource

    def test_parser_registry_instantiation(self):
        for name, cls in PARSER_REGISTRY.items():
            parser = cls()
            assert hasattr(parser, "parse")

    def test_data_source_registry_instantiation(self):
        parser = NasdaqHistoricalParser()
        source = DATA_SOURCE_REGISTRY["csv_file"](parser, {"file_path": "/dev/null"})
        assert hasattr(source, "get_data")
