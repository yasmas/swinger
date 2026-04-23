from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from brokers.base import BrokerCapabilities, OrderSide, PortfolioSnapshot
from strategies.base import Action, ActionType, PortfolioView
import trading.swing_bot as swing_bot_mod
from trading.swing_bot import SwingBot, load_config


class _FakeExchange:
    def __init__(self, config: dict):
        self.config = config
        self.product_id = config.get("product_id", "TEST")
        self.base_url = f"fake://{self.product_id}"

    def get_current_price(self, symbol: str) -> float:
        return 2100.0 if self.product_id == "ETP-20DEC30-CDE" else 2000.0


class _FakeDataManager:
    instances: list["_FakeDataManager"] = []

    def __init__(self, exchange, symbol, data_dir, warm_up_hours=250, feed_delay_minutes=0):
        self.exchange = exchange
        self.symbol = symbol
        self.data_dir = data_dir
        self.warm_up_hours = warm_up_hours
        self.feed_delay_minutes = feed_delay_minutes
        self.has_gap = False
        self.had_exchange_error = False
        self._df_5m = pd.DataFrame(
            {"open": [2000.0], "high": [2001.0], "low": [1999.0], "close": [2000.0], "volume": [1.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2025-01-02 00:00:00")], name="date"),
        )
        self._df_1h = self._df_5m.copy()
        self.__class__.instances.append(self)

    def startup(self):
        return self._df_5m, self._df_1h

    def get_df_5m(self):
        return self._df_5m

    def get_df_1h(self):
        return self._df_1h

    def fetch_and_append_5m(self):
        return None

    def is_hour_boundary(self, new_bar):
        return False


class _FakeStateManager:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        return {}

    def save(self, **kwargs) -> None:
        self.last_save = kwargs


class _FakeStrategyRunner:
    instances: list["_FakeStrategyRunner"] = []

    @staticmethod
    def get_min_warmup_hours(strategy_type: str, strategy_params: dict) -> int:
        return 0

    def __init__(self, strategy_type: str, strategy_params: dict, symbol: str, diagnostics_path: str | None = None):
        self.strategy_type = strategy_type
        self.strategy_params = strategy_params
        self.symbol = symbol
        self.diagnostics_path = diagnostics_path
        self.strategy = type("FakeStrategyState", (), {"reset_position": lambda self: None})()
        self.__class__.instances.append(self)

    def startup(self, df_5m, df_1h, exchange_price=None, strategy_state=None):
        self.startup_args = (df_5m, df_1h, exchange_price, strategy_state)

    def on_5m_bar(self, df_5m, portfolio_view):
        self.last_portfolio_view = portfolio_view
        return Action(ActionType.BUY, quantity=1.0, details={"reason": "entry"})

    def get_strategy_state(self) -> dict:
        return {}


class _FakeBroker:
    def __init__(self, exchange):
        self.exchange = exchange
        self.portfolio_view_calls: list[str] = []
        self.submit_calls: list[tuple[str, OrderSide]] = []
        self._pending_order_id = None

    def startup(self, config: dict) -> None:
        self.config = config

    def reconstruct_from_trades(self, trade_log_path: str, symbol: str) -> None:
        self.reconstruct_args = (trade_log_path, symbol)

    def import_state(self, state: dict) -> None:
        self.imported_state = state

    def export_state(self) -> dict:
        return {}

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            supports_shorting=True,
            supports_margin=False,
            supports_leverage=False,
            max_leverage=None,
        )

    def has_pending_order(self) -> bool:
        return self._pending_order_id is not None

    def get_pending_order_info(self) -> dict | None:
        if self._pending_order_id is None:
            return None
        return {"order_id": self._pending_order_id, "action": "BUY"}

    def portfolio_view(self, symbol: str) -> PortfolioView:
        self.portfolio_view_calls.append(symbol)
        return PortfolioView(cash=1000.0)

    def submit_order(self, symbol: str, side: OrderSide, notional: float | None = None) -> str:
        self.submit_calls.append((symbol, side))
        self._pending_order_id = "fake-order"
        return self._pending_order_id

    def get_portfolio_snapshot(self, prices=None) -> PortfolioSnapshot:
        return PortfolioSnapshot(cash=1000.0, positions={}, total_value=1000.0)

    def get_position(self, symbol: str):
        return None

    def emergency_close(self, symbol: str):
        return None

    def check_order(self, order_id: str):
        return None

    def cancel_order(self, order_id: str) -> bool:
        self._pending_order_id = None
        return True

    def get_contract_size(self, symbol: str) -> float:
        return 1.0


def test_load_config_derives_execution_exchange_from_execution_symbol(tmp_path):
    cfg_path = tmp_path / "bot.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "trader_name": "test",
        "bot": {
            "symbol": "ETP-20DEC30-CDE",
            "signal_symbol": "ETH-PERP-INTX",
            "execution_symbol": "ETP-20DEC30-CDE",
            "data_dir": str(tmp_path / "data"),
            "state_file": str(tmp_path / "state.yaml"),
        },
        "broker": {"type": "paper", "initial_cash": 1000},
        "exchange": {"type": "coinbase", "product_id": "ETH-PERP-INTX"},
        "strategy": {"type": "lazy_swing", "params": {}},
    }))

    config = load_config(str(cfg_path))

    assert config["bot"]["signal_symbol"] == "ETH-PERP-INTX"
    assert config["bot"]["execution_symbol"] == "ETP-20DEC30-CDE"
    assert config["execution_exchange"]["product_id"] == "ETP-20DEC30-CDE"


def test_swing_bot_uses_signal_symbol_for_data_and_execution_symbol_for_orders(monkeypatch, tmp_path):
    config = {
        "trader_name": "test-bot",
        "bot": {
            "symbol": "ETP-20DEC30-CDE",
            "signal_symbol": "ETH-PERP-INTX",
            "execution_symbol": "ETP-20DEC30-CDE",
            "data_dir": str(tmp_path / "data"),
            "state_file": str(tmp_path / "state.yaml"),
            "warm_up_hours": 1,
        },
        "broker": {"type": "coinbase"},
        "exchange": {"type": "coinbase", "product_id": "ETH-PERP-INTX"},
        "execution_exchange": {"type": "coinbase", "product_id": "ETP-20DEC30-CDE"},
        "strategy": {"type": "lazy_swing", "params": {}},
        "reporting": {
            "trade_log": str(tmp_path / "trades.csv"),
            "output_dir": str(tmp_path / "reports"),
            "report_file": str(tmp_path / "reports" / "report.html"),
        },
    }

    _FakeDataManager.instances.clear()
    _FakeStrategyRunner.instances.clear()
    monkeypatch.setattr("trading.swing_bot.create_exchange", lambda cfg: _FakeExchange(cfg))
    monkeypatch.setattr("trading.swing_bot.DataManager", _FakeDataManager)
    monkeypatch.setattr("trading.swing_bot.StateManager", _FakeStateManager)
    monkeypatch.setattr("trading.swing_bot.StrategyRunner", _FakeStrategyRunner)
    monkeypatch.setitem(swing_bot_mod.BROKER_REGISTRY, "coinbase", _FakeBroker)

    bot = SwingBot(config)
    bot._startup_hook()
    bot._evaluate_strategy(datetime.now(timezone.utc))

    assert _FakeDataManager.instances[0].symbol == "ETH-PERP-INTX"
    assert _FakeDataManager.instances[0].exchange.product_id == "ETH-PERP-INTX"
    assert bot.broker.exchange.product_id == "ETP-20DEC30-CDE"
    assert _FakeStrategyRunner.instances[0].symbol == "ETH-PERP-INTX"
    assert bot.broker.portfolio_view_calls[-1] == "ETP-20DEC30-CDE"
    assert bot.broker.submit_calls[-1] == ("ETP-20DEC30-CDE", OrderSide.BUY)


def test_swing_bot_defaults_to_single_symbol_when_no_overrides_are_set(monkeypatch, tmp_path):
    config = {
        "trader_name": "test-bot",
        "bot": {
            "symbol": "ETH-PERP-INTX",
            "data_dir": str(tmp_path / "data"),
            "state_file": str(tmp_path / "state.yaml"),
            "warm_up_hours": 1,
        },
        "broker": {"type": "coinbase"},
        "exchange": {"type": "coinbase", "product_id": "ETH-PERP-INTX"},
        "strategy": {"type": "lazy_swing", "params": {}},
        "reporting": {
            "trade_log": str(tmp_path / "trades.csv"),
            "output_dir": str(tmp_path / "reports"),
            "report_file": str(tmp_path / "reports" / "report.html"),
        },
    }

    _FakeDataManager.instances.clear()
    monkeypatch.setattr("trading.swing_bot.create_exchange", lambda cfg: _FakeExchange(cfg))
    monkeypatch.setattr("trading.swing_bot.DataManager", _FakeDataManager)
    monkeypatch.setattr("trading.swing_bot.StateManager", _FakeStateManager)
    monkeypatch.setattr("trading.swing_bot.StrategyRunner", _FakeStrategyRunner)
    monkeypatch.setitem(swing_bot_mod.BROKER_REGISTRY, "coinbase", _FakeBroker)

    bot = SwingBot(config)
    bot._startup_hook()

    assert bot.signal_symbol == "ETH-PERP-INTX"
    assert bot.execution_symbol == "ETH-PERP-INTX"
    assert _FakeDataManager.instances[0].symbol == "ETH-PERP-INTX"
    assert bot.broker.exchange.product_id == "ETH-PERP-INTX"
