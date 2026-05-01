import pandas as pd

from strategies.base import ActionType, PortfolioView
from strategies.registry import STRATEGY_REGISTRY
from strategies.st_vortex_adx import SupertrendVortexADXStrategy


def test_registry_exposes_st_vortex_adx():
    assert STRATEGY_REGISTRY["st_vortex_adx"] is SupertrendVortexADXStrategy


def test_direction_specific_vol_ratio_gates():
    strategy = SupertrendVortexADXStrategy(
        {
            "long_vol_ratio_enabled": True,
            "short_vol_ratio_enabled": True,
            "long_vol_ratio_min": 2.0,
            "short_vol_ratio_min": 1.5,
        }
    )
    strategy._indicators = pd.DataFrame(
        {
            "vol_ratio": [1.7],
        }
    )

    assert not strategy._vol_allows(0, "long")
    assert strategy._vol_allows(0, "short")


def test_short_context_requires_bearish_context_with_adx_floor():
    strategy = SupertrendVortexADXStrategy(
        {
            "short_require_context_bearish": True,
            "short_context_adx_floor": 20,
        }
    )
    ts = pd.Timestamp("2026-02-03 16:25:00")
    strategy._context = pd.DataFrame(
        {
            "st_bull": [False, False, True],
            "adx": [19.9, 20.0, 40.0],
        }
    )
    strategy._5m_to_context = {
        ts: 1,
        pd.Timestamp("2026-02-03 16:55:00"): 0,
        pd.Timestamp("2026-02-03 17:25:00"): 2,
    }

    assert strategy._short_context_allows(ts)
    assert not strategy._short_context_allows(pd.Timestamp("2026-02-03 16:55:00"))
    assert not strategy._short_context_allows(pd.Timestamp("2026-02-03 17:25:00"))


def test_outside_rth_core_flip_can_cover_short_without_opening_long():
    strategy = SupertrendVortexADXStrategy(
        {
            "rth_only_flips": True,
            "exit_outside_rth_on_core_flip": True,
            "equity_session_timezone": "America/New_York",
            "equity_regular_session_start": "09:30",
            "equity_regular_session_end": "16:00",
        }
    )
    strategy._resampled = pd.DataFrame(
        {"close": [100.0, 101.0]},
        index=pd.to_datetime(["2026-02-18 00:00", "2026-02-18 00:30"]),
    )
    strategy._indicators = pd.DataFrame(
        {
            "st_line": [99.0, 99.5],
            "st_bull": [False, True],
            "vi_plus": [1.0, 1.2],
            "vi_minus": [1.2, 1.0],
            "vi_plus_ema": [1.0, 1.2],
            "vi_minus_ema": [1.2, 1.0],
            "vortex_diff": [-0.2, 0.2],
            "vortex_diff_slope": [0.0, 0.4],
            "adx": [25.0, 26.0],
            "adx_slope": [1.0, 1.0],
            "vol_ratio": [0.1, 0.1],
        },
        index=strategy._resampled.index,
    )
    date = pd.Timestamp("2026-02-18 00:55:00")
    strategy._5m_to_signal = {date: 1}
    strategy._prev_signal_idx = 0
    strategy._prev_st_bullish = False

    action = strategy.on_bar(
        date=date,
        row=pd.Series({"close": 101.0}),
        data_so_far=pd.DataFrame(),
        is_last_bar=False,
        pv=PortfolioView(cash=1000.0, short_qty=5.0, short_avg_cost=105.0),
    )

    assert action.action is ActionType.COVER
    assert action.quantity == 5.0
    assert action.details["reason"] == "outside_rth_core_bull_exit"


def test_outside_rth_core_flip_does_not_open_new_position_when_flat():
    strategy = SupertrendVortexADXStrategy(
        {
            "rth_only_flips": True,
            "exit_outside_rth_on_core_flip": True,
            "equity_session_timezone": "America/New_York",
        }
    )
    strategy._resampled = pd.DataFrame(
        {"close": [100.0, 101.0]},
        index=pd.to_datetime(["2026-02-18 00:00", "2026-02-18 00:30"]),
    )
    strategy._indicators = pd.DataFrame(
        {
            "st_line": [99.0, 99.5],
            "st_bull": [False, True],
            "vi_plus": [1.0, 1.2],
            "vi_minus": [1.2, 1.0],
            "vi_plus_ema": [1.0, 1.2],
            "vi_minus_ema": [1.2, 1.0],
            "vortex_diff": [-0.2, 0.2],
            "vortex_diff_slope": [0.0, 0.4],
            "adx": [25.0, 26.0],
            "adx_slope": [1.0, 1.0],
            "vol_ratio": [0.1, 0.1],
        },
        index=strategy._resampled.index,
    )
    date = pd.Timestamp("2026-02-18 00:55:00")
    strategy._5m_to_signal = {date: 1}
    strategy._prev_signal_idx = 0
    strategy._prev_st_bullish = False

    action = strategy.on_bar(
        date=date,
        row=pd.Series({"close": 101.0}),
        data_so_far=pd.DataFrame(),
        is_last_bar=False,
        pv=PortfolioView(cash=1000.0),
    )

    assert action.action is ActionType.HOLD
    assert action.details["reason"] == "Outside RTH"
