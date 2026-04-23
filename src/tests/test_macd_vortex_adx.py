import pandas as pd

from strategies.base import ActionType, PortfolioView
from strategies.macd_vortex_adx import MACDVortexADXStrategy
from strategies.registry import STRATEGY_REGISTRY


DEFAULT_CFG = {
    "symbol": "TEST",
    "resample_interval": "30min",
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "use_histogram_flip": True,
    "macd_fresh_bars": 2,
    "require_macd_above_zero_for_long": False,
    "vortex_period": 14,
    "vortex_baseline_bars": 3,
    "vortex_strong_spread_mult": 1.25,
    "vortex_hugging_spread_mult": 1.05,
    "vortex_weave_lookback": 2,
    "adx_period": 14,
    "adx_floor": 20,
    "require_adx_rising": True,
    "breakout_lookback_bars": 3,
    "armed_breakout_expiry_bars": 2,
    "atr_period": 14,
    "atr_stop_multiplier": 2.0,
    "atr_trailing_multiplier": 1.5,
    "asset_class": "auto",
    "trailing_stop_rth_only_for_equities": False,
    "equity_session_timezone": "America/New_York",
    "equity_regular_session_start": "09:30",
    "equity_regular_session_end": "16:00",
    "enable_short": True,
}


def _make_5m_data(prices: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(prices), freq="5min")
    df = pd.DataFrame(
        {
            "open": prices,
            "high": [p + 0.4 for p in prices],
            "low": [p - 0.4 for p in prices],
            "close": prices,
            "volume": [100.0] * len(prices),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def _flat_then_trend(
    flat_bars: int = 240,
    trend_bars: int = 240,
    start: float = 100.0,
    step: float = 0.15,
) -> list[float]:
    prices = [start + ((i % 4) - 1.5) * 0.02 for i in range(flat_bars)]
    last = prices[-1]
    for i in range(trend_bars):
        prices.append(last + (i + 1) * step)
    return prices


def _flat_then_downtrend(
    flat_bars: int = 240,
    trend_bars: int = 240,
    start: float = 120.0,
    step: float = 0.15,
) -> list[float]:
    prices = [start + ((i % 4) - 1.5) * 0.02 for i in range(flat_bars)]
    last = prices[-1]
    for i in range(trend_bars):
        prices.append(last - (i + 1) * step)
    return prices


def _strategy(cfg_overrides=None) -> MACDVortexADXStrategy:
    cfg = {**DEFAULT_CFG}
    if cfg_overrides:
        cfg.update(cfg_overrides)
    return MACDVortexADXStrategy(cfg)


def _pv(cash=100000.0, position_qty=0.0, short_qty=0.0) -> PortfolioView:
    return PortfolioView(cash=cash, position_qty=position_qty, short_qty=short_qty)


class TestMACDVortexADXStrategy:
    def test_registry_contains_strategy(self):
        from strategies.macd_vortex_adx import MACDVortexADXStrategy as StrategyClass

        assert STRATEGY_REGISTRY["macd_vortex_adx"] is StrategyClass

    def test_no_lookahead_mapping_uses_completed_bucket(self):
        prices = _flat_then_trend(flat_bars=48, trend_bars=24)
        data = _make_5m_data(prices)
        strategy = _strategy()
        strategy.prepare(data.iloc[:11])

        first_ts = data.index[0]
        first_completed_bucket_ts = data.index[5]
        last_partial_ts = data.index[10]
        assert first_ts not in strategy._5m_to_signal
        assert strategy._5m_to_signal[first_completed_bucket_ts] == 0
        assert strategy._5m_to_signal[last_partial_ts] == 0

    def test_bullish_immediate_entry(self):
        prices = _flat_then_trend()
        data = _make_5m_data(prices)
        strategy = _strategy({"adx_floor": 5})
        strategy.prepare(data)

        action = None
        for i in range(len(data)):
            action = strategy.on_bar(
                data.index[i],
                data.iloc[i],
                data.iloc[: i + 1],
                is_last_bar=False,
                pv=_pv(),
            )
            if action.action == ActionType.BUY:
                break

        assert action is not None
        assert action.action == ActionType.BUY
        assert "Immediate long entry" in action.details["reason"]

    def test_bearish_immediate_entry(self):
        prices = _flat_then_downtrend()
        data = _make_5m_data(prices)
        strategy = _strategy({"adx_floor": 5})
        strategy.prepare(data)

        action = None
        for i in range(len(data)):
            action = strategy.on_bar(
                data.index[i],
                data.iloc[i],
                data.iloc[: i + 1],
                is_last_bar=False,
                pv=_pv(),
            )
            if action.action == ActionType.SHORT:
                break

        assert action is not None
        assert action.action == ActionType.SHORT
        assert "Immediate short entry" in action.details["reason"]

    def test_armed_breakout_fill(self):
        strategy = _strategy()
        signal_idx = 10
        strategy._armed_direction = "long"
        strategy._armed_trigger_price = 105.0
        strategy._armed_signal_idx = signal_idx
        strategy._armed_expiry_idx = 12
        strategy._armed_stop_ref = 101.0
        strategy._armed_atr = 1.5
        strategy._indicators = pd.DataFrame({"atr": [1.5] * 20})

        row = pd.Series({"open": 104.0, "high": 105.2, "low": 103.8, "close": 105.1})
        action = strategy._check_intrabar_trigger(
            row,
            105.1,
            _pv(),
            signal_idx,
            {"signal_idx": signal_idx},
        )

        assert action is not None
        assert action.action == ActionType.BUY
        assert action.details["entry_reason"] == "armed_breakout_long"
        assert strategy._in_long is True

    def test_armed_breakout_expiry(self):
        strategy = _strategy()
        strategy._armed_direction = "long"
        strategy._armed_trigger_price = 105.0
        strategy._armed_signal_idx = 10
        strategy._armed_expiry_idx = 11
        strategy._armed_stop_ref = 101.0
        strategy._armed_atr = 1.0

        row = pd.Series({"open": 104.0, "high": 104.9, "low": 103.8, "close": 104.5})
        action = strategy._check_intrabar_trigger(
            row,
            104.5,
            _pv(),
            12,
            {"signal_idx": 12},
        )

        assert action is not None
        assert action.action == ActionType.HOLD
        assert strategy._armed_direction is None

    def test_rejects_hugging_vortex(self):
        prices = _flat_then_trend()
        data = _make_5m_data(prices)
        strategy = _strategy()
        strategy.prepare(data)
        signal_idx = max(strategy.macd_slow + strategy.macd_signal + 2, 40)
        strategy._set_alert("long", signal_idx, "macd_cross")
        strategy._indicators.loc[:, "vi_plus"] = 1.0
        strategy._indicators.loc[:, "vi_minus"] = 0.99
        strategy._indicators.loc[:, "vortex_spread"] = 0.01
        strategy._indicators.loc[:, "adx"] = 30.0
        setup = strategy._evaluate_setup(signal_idx)

        assert setup["vortex"]["classification"] == "hugging"

    def test_rejects_falling_adx(self):
        prices = _flat_then_trend()
        data = _make_5m_data(prices)
        strategy = _strategy()
        strategy.prepare(data)
        signal_idx = max(strategy.macd_slow + strategy.macd_signal + 2, 40)
        strategy._indicators.loc[:, "adx"] = 30.0
        strategy._indicators.iloc[signal_idx - 1, strategy._indicators.columns.get_loc("adx")] = 35.0
        ok, _, rising = strategy._adx_ok(signal_idx)

        assert not bool(ok)
        assert not bool(rising)

    def test_long_zero_line_filter_blocks_long_setup_below_zero(self):
        prices = _flat_then_trend()
        data = _make_5m_data(prices)
        strategy = _strategy({"require_macd_above_zero_for_long": True})
        strategy.prepare(data)
        signal_idx = max(strategy.macd_slow + strategy.macd_signal + 2, 40)
        strategy._set_alert("long", signal_idx, "macd_cross")
        strategy._indicators.loc[:, "adx"] = 30.0
        strategy._indicators.loc[:, "vi_plus"] = 1.2
        strategy._indicators.loc[:, "vi_minus"] = 0.8
        strategy._indicators.loc[:, "vortex_spread"] = 0.5
        strategy._indicators.iloc[signal_idx - 1, strategy._indicators.columns.get_loc("vortex_spread")] = 0.2
        strategy._indicators.iloc[signal_idx, strategy._indicators.columns.get_loc("macd")] = -0.01
        strategy._indicators.iloc[signal_idx, strategy._indicators.columns.get_loc("macd_signal")] = -0.02

        setup = strategy._evaluate_setup(signal_idx)

        assert not bool(setup["macd_zero_line_ok"])
        assert not bool(setup["confirmed"])

    def test_long_zero_line_filter_allows_long_setup_above_zero(self):
        prices = _flat_then_trend()
        data = _make_5m_data(prices)
        strategy = _strategy({"require_macd_above_zero_for_long": True})
        strategy.prepare(data)
        signal_idx = max(strategy.macd_slow + strategy.macd_signal + 2, 40)
        strategy._set_alert("long", signal_idx, "macd_cross")
        strategy._indicators.loc[:, "adx"] = 30.0
        strategy._indicators.loc[:, "vi_plus"] = 1.2
        strategy._indicators.loc[:, "vi_minus"] = 0.8
        strategy._indicators.loc[:, "vortex_spread"] = 0.5
        strategy._indicators.iloc[signal_idx - 1, strategy._indicators.columns.get_loc("vortex_spread")] = 0.2
        strategy._indicators.iloc[signal_idx, strategy._indicators.columns.get_loc("macd")] = 0.02
        strategy._indicators.iloc[signal_idx, strategy._indicators.columns.get_loc("macd_signal")] = 0.01

        setup = strategy._evaluate_setup(signal_idx)

        assert bool(setup["macd_zero_line_ok"])

    def test_initial_stop_exit(self):
        strategy = _strategy()
        strategy._indicators = pd.DataFrame({"atr": [1.0] * 20})
        strategy._set_position_state("long", 100.0, 10, 99.0, 1.0)
        row = pd.Series({"open": 100.0, "high": 100.5, "low": 97.0, "close": 98.0})

        action = strategy._check_position_exit(
            pd.Timestamp("2026-01-02 15:00:00"),
            row,
            98.0,
            _pv(position_qty=1.0),
            10,
            False,
            {"signal_idx": 10},
        )

        assert action is not None
        assert action.action == ActionType.SELL
        assert action.details["exit_reason"] == "initial_stop"

    def test_trailing_stop_exit(self):
        strategy = _strategy()
        strategy._indicators = pd.DataFrame({"atr": [1.0] * 20})
        strategy._set_position_state("long", 100.0, 10, 99.0, 1.0)
        strategy._peak_since_entry = 110.0
        row = pd.Series({"open": 109.0, "high": 109.2, "low": 108.4, "close": 108.5})

        action = strategy._check_position_exit(
            pd.Timestamp("2026-01-02 15:00:00"),
            row,
            108.5,
            _pv(position_qty=1.0),
            10,
            False,
            {"signal_idx": 10},
        )

        assert action is not None
        assert action.action == ActionType.SELL
        assert action.details["exit_reason"] == "trailing_stop"

    def test_trailing_stop_uses_close_instead_of_high_for_long(self):
        strategy = _strategy()
        strategy._indicators = pd.DataFrame({"atr": [1.0] * 20})
        strategy._set_position_state("long", 100.0, 10, 99.0, 1.0)
        strategy._peak_since_entry = 110.0
        row = pd.Series({"open": 110.5, "high": 120.0, "low": 109.8, "close": 111.0})

        action = strategy._check_position_exit(
            pd.Timestamp("2026-01-02 15:00:00"),
            row,
            111.0,
            _pv(position_qty=1.0),
            10,
            False,
            {"signal_idx": 10},
        )

        assert action is None
        assert strategy._peak_since_entry == 111.0

    def test_trailing_stop_ignored_outside_regular_hours_for_equity(self):
        strategy = _strategy(
            {
                "asset_class": "equity",
                "trailing_stop_rth_only_for_equities": True,
            }
        )
        strategy._indicators = pd.DataFrame({"atr": [1.0] * 20})
        strategy._set_position_state("long", 100.0, 10, 99.0, 1.0)
        strategy._peak_since_entry = 110.0
        row = pd.Series({"open": 109.0, "high": 109.2, "low": 108.4, "close": 108.5})

        action = strategy._check_position_exit(
            pd.Timestamp("2026-04-14 12:00:00"),
            row,
            108.5,
            _pv(position_qty=1.0),
            10,
            False,
            {"signal_idx": 10},
        )

        assert action is None

    def test_export_import_preserves_armed_breakout(self):
        strategy = _strategy()
        strategy._armed_direction = "short"
        strategy._armed_trigger_price = 95.0
        strategy._armed_signal_idx = 22
        strategy._armed_expiry_idx = 24
        strategy._armed_stop_ref = 101.0
        strategy._armed_atr = 1.25

        state = strategy.export_state()
        restored = _strategy()
        restored.import_state(state)

        assert restored._armed_direction == "short"
        assert restored._armed_trigger_price == 95.0
        assert restored._armed_expiry_idx == 24

    def test_opposite_confirmed_setup_exits_position(self):
        strategy = _strategy()
        strategy._indicators = pd.DataFrame({"atr": [1.0] * 40})
        strategy._resampled = pd.DataFrame(
            {
                "open": [100.0] * 40,
                "high": [101.0] * 40,
                "low": [99.0] * 40,
                "close": [100.0] * 40,
                "volume": [100.0] * 40,
            }
        )
        strategy._set_position_state("long", 100.0, 10, 99.0, 1.0)
        strategy._evaluate_setup = lambda idx: {
            "confirmed": True,
            "direction": "short",
            "source": "macd_cross",
            "vortex": {"classification": "strong"},
        }

        row = pd.Series({"open": 100.0, "high": 100.8, "low": 99.8, "close": 100.2})
        action = strategy._check_position_exit(
            pd.Timestamp("2026-01-02 15:30:00"),
            row,
            100.2,
            _pv(position_qty=1.0),
            12,
            True,
            {"signal_idx": 12},
        )

        assert action is not None
        assert action.action == ActionType.SELL
        assert action.details["exit_reason"] == "opposite_setup"
        assert strategy._alert_direction == "short"
