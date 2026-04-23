from pathlib import Path

import yaml


class Config:
    """Loads and provides access to a YAML backtest configuration."""

    def __init__(self, config_dict: dict):
        self._data = config_dict
        self.backtest = config_dict["backtest"]
        self.data_source = config_dict["data_source"]
        self.execution_data_source = config_dict.get("execution_data_source")
        self.strategies = config_dict["strategies"]

    @property
    def name(self) -> str:
        return self.backtest["name"]

    @property
    def version(self) -> str:
        return self.backtest.get("version", "")

    @property
    def initial_cash(self) -> float:
        return float(self.backtest["initial_cash"])

    @property
    def start_date(self) -> str:
        return str(self.backtest["start_date"])

    @property
    def end_date(self) -> str:
        return str(self.backtest["end_date"])

    @property
    def data_source_type(self) -> str:
        return self.data_source["type"]

    @property
    def parser_type(self) -> str:
        return self.data_source["parser"]

    @property
    def data_source_params(self) -> dict:
        return self.data_source.get("params", {})

    @property
    def has_execution_data_source(self) -> bool:
        return self.execution_data_source is not None

    @property
    def execution_data_source_type(self) -> str | None:
        if not self.execution_data_source:
            return None
        return self.execution_data_source["type"]

    @property
    def execution_parser_type(self) -> str | None:
        if not self.execution_data_source:
            return None
        return self.execution_data_source["parser"]

    @property
    def execution_data_source_params(self) -> dict:
        if not self.execution_data_source:
            return {}
        return self.execution_data_source.get("params", {})

    @property
    def symbol(self) -> str:
        return self.data_source["params"]["symbol"]

    @property
    def execution_symbol(self) -> str:
        if not self.execution_data_source:
            return self.symbol
        return self.execution_data_source["params"]["symbol"]

    @classmethod
    def from_yaml(cls, file_path: str) -> "Config":
        path = Path(file_path)
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(data)
