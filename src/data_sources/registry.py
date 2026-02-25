from .parsers.nasdaq import NasdaqHistoricalParser
from .parsers.binance import BinanceKlineParser
from .csv_file import CsvFileDataSource

PARSER_REGISTRY: dict[str, type] = {
    "nasdaq_historical": NasdaqHistoricalParser,
    "binance_kline": BinanceKlineParser,
}

DATA_SOURCE_REGISTRY: dict[str, type] = {
    "csv_file": CsvFileDataSource,
}
