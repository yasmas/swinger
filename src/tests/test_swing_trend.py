import pandas as pd

from strategies.base import ActionType, PortfolioView
from strategies.swing_trend import SwingTrendStrategy


def _make_5m_data() -> pd.DataFrame:
    idx = pd.date_range("2025-01-01 09:00", periods=24, freq="5min")
    df = pd.DataFrame(
        {
            "open": [100.0] * len(idx),
            "high": [101.0] * len(idx),
            "low": [99.0] * len(idx),
            "close": [100.0] * len(idx),
            "volume": [1000.0] * len(idx),
        },
        index=idx,
    )
    df.index.name = "date"
    return df


def _make_strategy(data: pd.DataFrame) -> SwingTrendStrategy:
    strategy = SwingTrendStrategy(
        {
            "symbol": "TEST",
            "entry_mode": "breakout",
            "enable_short": False,
            "adx_threshold": 20,
            "cooldown_bars": 0,
            "max_supertrend_stop_pct": 20.0,
        }
    )
    strategy.prepare(data)
    strategy._warmup_bars = 0

    hourly_idx = strategy._hourly.index
    strategy._hma_slope = pd.Series([-1.0, 1.0], index=hourly_idx)
    strategy._st_line = pd.Series([110.0, 98.0], index=hourly_idx)
    strategy._trail_st_line = pd.Series([110.0, 98.0], index=hourly_idx)
    strategy._st_bullish = pd.Series([False, True], index=hourly_idx)
    strategy._kc_upper = pd.Series([120.0, 95.0], index=hourly_idx)
    strategy._kc_mid = pd.Series([100.0, 90.0], index=hourly_idx)
    strategy._kc_lower = pd.Series([80.0, 70.0], index=hourly_idx)
    strategy._adx = pd.Series([30.0, 30.0], index=hourly_idx)
    strategy._short_adx = strategy._adx
    strategy._macd_line = None
    strategy._macd_signal_line = None
    strategy._macd_histogram = None
    strategy._rsi = None
    strategy._atr = None
    strategy._rel_vol = None
    strategy._cmf = None
    strategy._macd_cmf = None
    strategy._mfi = None
    strategy._obv_slope = None
    return strategy


def _run_bar(strategy: SwingTrendStrategy, data: pd.DataFrame, pos: int) -> tuple[pd.Timestamp, object]:
    pv = PortfolioView(cash=100000.0)
    ts = data.index[pos]
    action = strategy.on_bar(ts, data.iloc[pos], data.iloc[: pos + 1], False, pv)
    return ts, action


class TestSwingTrendHourlyMapping:
    def test_completed_hour_mapping_uses_last_finished_hour(self):
        assert SwingTrendStrategy._last_completed_hour_timestamp(pd.Timestamp("2025-01-01 10:00")) == pd.Timestamp("2025-01-01 09:00")
        assert SwingTrendStrategy._last_completed_hour_timestamp(pd.Timestamp("2025-01-01 10:05")) == pd.Timestamp("2025-01-01 09:00")
        assert SwingTrendStrategy._last_completed_hour_timestamp(pd.Timestamp("2025-01-01 10:50")) == pd.Timestamp("2025-01-01 09:00")
        assert SwingTrendStrategy._last_completed_hour_timestamp(pd.Timestamp("2025-01-01 10:55")) == pd.Timestamp("2025-01-01 10:00")

    def test_hourly_close_fires_on_55_not_00(self):
        data = _make_5m_data()
        strategy = _make_strategy(data)

        _, action_955 = _run_bar(strategy, data, 11)
        _, action_1000 = _run_bar(strategy, data, 12)
        _, action_1055 = _run_bar(strategy, data, 23)

        assert action_955.details["indicators"]["is_hourly_close"] is True
        assert action_1000.details["indicators"]["is_hourly_close"] is False
        assert action_1055.details["trigger"] == "keltner_breakout"

    def test_bars_before_55_read_previous_completed_hour_indicators(self):
        data = _make_5m_data()
        strategy = _make_strategy(data)

        _run_bar(strategy, data, 11)
        _, action_1000 = _run_bar(strategy, data, 12)
        _, action_1055 = _run_bar(strategy, data, 23)

        ind_1000 = action_1000.details["indicators"]
        assert ind_1000["hourly_idx"] == 0
        assert ind_1000["st_bullish"] is False
        assert ind_1000["kc_upper"] == 120.0

        assert action_1055.action == ActionType.BUY

    def test_regression_no_early_entry_at_hour_start(self):
        data = _make_5m_data()
        strategy = _make_strategy(data)

        _run_bar(strategy, data, 11)
        _, action_1000 = _run_bar(strategy, data, 12)
        _, action_1055 = _run_bar(strategy, data, 23)

        assert action_1000.action == ActionType.HOLD
        assert action_1000.details["reason"] == "wait_hourly"
        assert action_1055.action == ActionType.BUY

    def test_hourly_state_counters_advance_once_per_completed_hour(self):
        data = _make_5m_data()
        strategy = _make_strategy(data)
        strategy.state.macd_bars_since_exit = 0

        _run_bar(strategy, data, 11)
        after_955 = strategy.state.macd_bars_since_exit
        _run_bar(strategy, data, 12)
        after_1000 = strategy.state.macd_bars_since_exit

        strategy = _make_strategy(data)
        strategy.state.macd_bars_since_exit = 0
        _run_bar(strategy, data, 11)
        _run_bar(strategy, data, 12)
        _run_bar(strategy, data, 23)
        after_1055 = strategy.state.macd_bars_since_exit

        assert after_955 == 1
        assert after_1000 == 1
        assert after_1055 == 2
