import pandas as pd

from .base import DataSourceBase
from .parsers.base import DataParserBase


class CsvFileDataSource(DataSourceBase):
    """Reads price data from a local CSV file and delegates to a parser.

    Config params:
        file_path (str): Path to the CSV file.
    """

    def __init__(self, parser: DataParserBase, config: dict):
        super().__init__(parser, config)
        self.file_path = config["file_path"]

    def fetch_raw(self, symbol: str, start_date: pd.Timestamp, end_date: pd.Timestamp) -> str:
        with open(self.file_path, "r") as f:
            return f.read()
