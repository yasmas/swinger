import os
import tempfile

import pandas as pd
import pytest

from config import Config
from controller import Controller, BacktestResult
from strategies.base import Action, ActionType, PortfolioView, StrategyBase
from strategies.registry import STRATEGY_REGISTRY
from trade_log import TradeLogReader

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


def _make_config(tmp_dir, data_file="QQQ-HistoricalData.csv", parser="nasdaq_historical",
                 start="2025-06-01", end="2025-06-30", symbol="QQQ"):
    return Config({
        "backtest": {
            "name": "test_backtest",
            "initial_cash": 100000,
            "start_date": start,
            "end_date": end,
        },
        "data_source": {
            "type": "csv_file",
            "parser": parser,
            "params": {
                "file_path": os.path.join(DATA_DIR, data_file),
                "symbol": symbol,
            },
        },
        "strategies": [
            {"type": "buy_and_hold", "params": {}},
        ],
    })


class _WarmupProbeStrategy(StrategyBase):
    display_name = "Warmup Probe"
    min_warmup_hours = 48

    def __init__(self, config):
        super().__init__(config)
        self._warmup_bars = 0
        self._entered = False

    def warmup_bar(self, date, row, data_so_far, is_last_bar) -> None:
        self._warmup_bars += 1

    def on_bar(self, date, row, data_so_far, is_last_bar, pv: PortfolioView) -> Action:
        if not self._entered:
            self._entered = True
            return Action(
                ActionType.BUY,
                quantity=1.0,
                details={"warmup_bars": self._warmup_bars},
            )
        if is_last_bar and pv.position_qty > 0:
            return Action(ActionType.SELL, quantity=pv.position_qty, details={"reason": "done"})
        return Action(ActionType.HOLD, details={"reason": "hold"})


class _GapCarryProbeStrategy(StrategyBase):
    display_name = "Gap Carry Probe"

    def __init__(self, config):
        super().__init__(config)
        self._entered = False

    def on_bar(self, date, row, data_so_far, is_last_bar, pv: PortfolioView) -> Action:
        if not self._entered:
            self._entered = True
            return Action(ActionType.BUY, quantity=1.0, details={"reason": "entry"})
        if is_last_bar and pv.position_qty > 0:
            return Action(ActionType.SELL, quantity=pv.position_qty, details={"reason": "done"})
        return Action(ActionType.HOLD, details={"reason": "hold"})


def _write_binance_csv(path: str, start: str, periods: int, freq: str = "5min") -> None:
    ix = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    open_time = (ix.tz_localize(None).view("int64") // 1_000).astype("int64")
    df = pd.DataFrame({
        "open_time": open_time,
        "open": [100.0 + i * 0.1 for i in range(periods)],
        "high": [100.5 + i * 0.1 for i in range(periods)],
        "low": [99.5 + i * 0.1 for i in range(periods)],
        "close": [100.2 + i * 0.1 for i in range(periods)],
        "volume": [10.0] * periods,
        "close_time": (open_time + 299_999).astype("int64"),
        "quote_asset_volume": [0.0] * periods,
        "number_of_trades": [1] * periods,
        "taker_buy_base_volume": [0.0] * periods,
        "taker_buy_quote_volume": [0.0] * periods,
        "ignore": [0] * periods,
    })
    df.to_csv(path, index=False)


def _write_gap_csv(path: str) -> None:
    first_leg = pd.date_range("2025-01-02 14:30:00", periods=3, freq="30min", tz="UTC")
    second_leg = pd.date_range("2025-01-06 14:30:00", periods=2, freq="30min", tz="UTC")
    ix = first_leg.append(second_leg)
    open_time = (ix.tz_localize(None).view("int64") // 1_000).astype("int64")
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    df = pd.DataFrame({
        "open_time": open_time,
        "open": closes,
        "high": [c + 0.5 for c in closes],
        "low": [c - 0.5 for c in closes],
        "close": closes,
        "volume": [10.0] * len(ix),
        "close_time": (open_time + (30 * 60 * 1000) - 1).astype("int64"),
        "quote_asset_volume": [0.0] * len(ix),
        "number_of_trades": [1] * len(ix),
        "taker_buy_base_volume": [0.0] * len(ix),
        "taker_buy_quote_volume": [0.0] * len(ix),
        "ignore": [0] * len(ix),
    })
    df.to_csv(path, index=False)


class TestConfigLoader:
    def test_from_yaml(self):
        config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config", "btc_buy_and_hold.yaml")
        config = Config.from_yaml(config_path)
        assert config.name == "BTC Buy and Hold 2024"
        assert config.initial_cash == 100000
        assert config.symbol == "BTCUSDT"
        assert config.parser_type == "binance_kline"

    def test_config_properties(self):
        config = _make_config("/tmp")
        assert config.name == "test_backtest"
        assert config.initial_cash == 100000
        assert config.start_date == "2025-06-01"
        assert config.end_date == "2025-06-30"
        assert config.symbol == "QQQ"


class TestControllerWithNasdaq:
    def test_run_produces_results(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            controller = Controller(config, output_dir=tmp_dir)
            results = controller.run()

            assert len(results) == 1
            assert isinstance(results[0], BacktestResult)

    def test_trade_log_file_created(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            controller = Controller(config, output_dir=tmp_dir)
            results = controller.run()

            assert os.path.exists(results[0].trade_log_path)

    def test_trade_log_has_correct_structure(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            controller = Controller(config, output_dir=tmp_dir)
            results = controller.run()

            df = TradeLogReader.read(results[0].trade_log_path)
            assert len(df) > 0
            assert "action" in df.columns
            assert "portfolio_value" in df.columns

    def test_buy_and_hold_has_one_buy_one_sell(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            controller = Controller(config, output_dir=tmp_dir)
            results = controller.run()

            df = TradeLogReader.read(results[0].trade_log_path)
            assert (df["action"] == "BUY").sum() == 1
            assert (df["action"] == "SELL").sum() == 1

    def test_final_value_reasonable(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir)
            controller = Controller(config, output_dir=tmp_dir)
            results = controller.run()

            assert results[0].final_value > 0
            assert results[0].initial_cash == 100000

    def test_empty_date_range_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(tmp_dir, start="2000-01-01", end="2000-01-31")
            controller = Controller(config, output_dir=tmp_dir)
            with pytest.raises(ValueError, match="No data found"):
                controller.run()


class TestControllerWithBinance:
    def test_run_with_btc_data(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = _make_config(
                tmp_dir,
                data_file="BTCUSDT-5m-20240101-20260131.csv",
                parser="binance_kline",
                start="2024-03-01",
                end="2024-03-07",
                symbol="BTCUSDT",
            )
            controller = Controller(config, output_dir=tmp_dir)
            results = controller.run()

            assert len(results) == 1
            df = TradeLogReader.read(results[0].trade_log_path)
            assert (df["action"] == "BUY").sum() == 1
            assert (df["action"] == "SELL").sum() == 1
            assert results[0].final_value > 0

    def test_controller_preloads_warmup_bars_without_logging_them(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            STRATEGY_REGISTRY["warmup_probe"] = _WarmupProbeStrategy
            try:
                csv_path = os.path.join(tmp_dir, "warmup_probe.csv")
                _write_binance_csv(csv_path, "2024-02-28 00:00:00", periods=6 * 24 * 12)
                config = Config({
                    "backtest": {
                        "name": "warmup_probe_test",
                        "initial_cash": 100000,
                        "start_date": "2024-03-03",
                        "end_date": "2024-03-04",
                    },
                    "data_source": {
                        "type": "csv_file",
                        "parser": "binance_kline",
                        "params": {
                            "file_path": csv_path,
                            "symbol": "BTCUSDT",
                        },
                    },
                    "strategies": [
                        {"type": "warmup_probe", "params": {}},
                    ],
                })
                controller = Controller(config, output_dir=tmp_dir)
                results = controller.run()

                df = TradeLogReader.read(results[0].trade_log_path)
                assert df["date"].min() >= pd.Timestamp("2024-03-03")
                first_buy = df[df["action"] == "BUY"].iloc[0]
                assert first_buy["details"]["warmup_bars"] > 0
            finally:
                STRATEGY_REGISTRY.pop("warmup_probe", None)

    def test_controller_can_keep_positions_across_large_data_gaps(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            STRATEGY_REGISTRY["gap_carry_probe"] = _GapCarryProbeStrategy
            try:
                csv_path = os.path.join(tmp_dir, "gap_probe.csv")
                _write_gap_csv(csv_path)
                config = Config({
                    "backtest": {
                        "name": "gap_probe_test",
                        "initial_cash": 100000,
                        "start_date": "2025-01-02",
                        "end_date": "2025-01-06",
                        "keep_positions_on_data_gap": True,
                    },
                    "data_source": {
                        "type": "csv_file",
                        "parser": "binance_kline",
                        "params": {
                            "file_path": csv_path,
                            "symbol": "BTCUSDT",
                        },
                    },
                    "strategies": [
                        {"type": "gap_carry_probe", "params": {}},
                    ],
                })
                controller = Controller(config, output_dir=tmp_dir)
                results = controller.run()

                df = TradeLogReader.read(results[0].trade_log_path)
                assert "data_gap" not in {d.get("exit_reason") for d in df["details"] if isinstance(d, dict)}
                sells = df[df["action"] == "SELL"]
                assert len(sells) == 1
                assert sells.iloc[0]["date"] == pd.Timestamp("2025-01-06 15:00:00")
            finally:
                STRATEGY_REGISTRY.pop("gap_carry_probe", None)
