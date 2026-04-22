"""LazySwing entry persistence and SwingParty eviction-ratio wiring."""

import pandas as pd

from strategies.base import ActionType, PortfolioView
from strategies.lazy_swing import LazySwingStrategy
from strategies.swing_party import SwingPartyCoordinator


def test_swing_party_coordinator_min_eviction_defaults():
    cfg = {
        "assets": ["A"],
        "scorer": {"type": "volume_breakout", "params": {}},
    }
    c = SwingPartyCoordinator(cfg)
    assert c.min_eviction_volume_ratio == 0.2
    assert c._scorer_type == "volume_breakout"


def test_persist_evaluate_enters_on_positive_roc():
    s = LazySwingStrategy({
        "symbol": "TEST",
        "entry_persist_max_bars": 4,
        "entry_persist_max_price_drift": 0.05,
        "entry_persist_roc_lookback": 1,
    })
    ix = pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")
    s._hourly = pd.DataFrame({"close": [100.0, 100.5, 100.6, 100.7, 100.8]}, index=ix)
    s._arm_entry_persist("long", 1)
    pv = PortfolioView(cash=1_000_000.0)
    mode, act = s._persist_evaluate(1, 100.5, pv, True, {})
    assert mode == "entered"
    assert act.action == ActionType.BUY
    assert s._persist_direction is None
    assert s._in_long is True


def test_persist_evaluate_expires_after_n_bars():
    s = LazySwingStrategy({
        "symbol": "TEST",
        "entry_persist_max_bars": 2,
        "entry_persist_max_price_drift": 0.5,
        "entry_persist_roc_lookback": 1,
    })
    ix = pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")
    s._hourly = pd.DataFrame({"close": [100.0, 100.1, 100.2, 100.3, 100.4]}, index=ix)
    s._arm_entry_persist("long", 0)
    pv = PortfolioView(cash=1_000_000.0)
    mode, act = s._persist_evaluate(2, 100.2, pv, True, {})
    assert mode == "continue"
    assert act is None
    assert s._persist_direction is None


def test_persist_evaluate_clears_on_price_drift():
    s = LazySwingStrategy({
        "symbol": "TEST",
        "entry_persist_max_bars": 8,
        "entry_persist_max_price_drift": 0.01,
        "entry_persist_roc_lookback": 1,
    })
    ix = pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")
    s._hourly = pd.DataFrame({"close": [100.0, 102.0, 100.5, 100.6, 100.7]}, index=ix)
    s._arm_entry_persist("long", 0)
    pv = PortfolioView(cash=1_000_000.0)
    mode, act = s._persist_evaluate(1, 102.0, pv, True, {})
    assert mode == "continue"
    assert s._persist_direction is None


def test_flip_vol_ratio_allows_only_when_above_threshold():
    s = LazySwingStrategy({
        "symbol": "TEST",
        "flip_vol_ratio_enabled": True,
        "flip_vol_ratio_min": 1.1,
    })
    ix = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    s._flip_vol_short = pd.Series([1.0, 1.2, 1.4], index=ix)
    s._flip_vol_long_mean = pd.Series([1.0, 1.0, 1.0], index=ix)
    s._flip_vol_ratio = pd.Series([1.0, 1.2, 1.4], index=ix)

    allowed, info = s._flip_vol_ratio_allows(0)
    assert allowed is False
    assert info["ratio"] == 1.0
    assert info["ready"] is True

    allowed, info = s._flip_vol_ratio_allows(1)
    assert allowed is True
    assert info["ratio"] == 1.2


def test_held_flip_stop_triggers_from_rejected_flip_price():
    s = LazySwingStrategy({
        "symbol": "TEST",
        "flip_vol_ratio_enabled": True,
        "flip_vol_ratio_safety_stop_pct": 2.0,
    })
    s._arm_held_flip("short", 10, 100.0, 0.02)
    stop_hit, adverse_move = s._held_flip_stop_triggered("short", 97.5)
    assert stop_hit is True
    assert round(adverse_move, 4) == 0.025

    s._arm_held_flip("long", 11, 100.0, 0.02)
    stop_hit, adverse_move = s._held_flip_stop_triggered("long", 102.5)
    assert stop_hit is True
    assert round(adverse_move, 4) == 0.025


def test_flip_vol_squared_regime_biases_toward_loose_until_high_vol():
    s = LazySwingStrategy(
        {
            "symbol": "TEST",
            "adaptive_st_enter_ratio_threshold": 1.0,
            "adaptive_st_exit_ratio_threshold": 0.85,
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_regime_mode": "squared",
            "flip_vol_ratio_regime_low_min": 0.75,
            "flip_vol_ratio_regime_high_min": 1.0,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 2.5,
        }
    )
    ix = pd.date_range("2026-01-01", periods=1, freq="h", tz="UTC")
    s._vol_regime_high = pd.Series([False], index=ix)
    s._vol_regime_ratio = pd.Series([0.925], index=ix)

    params = s._active_flip_vol_params(0)

    assert round(params["regime_weight"], 4) == 0.25
    assert round(params["active_ratio_min_decimal"], 4) == 0.8125
    assert round(params["active_stop_pct_decimal"], 4) == 0.0138


def test_flip_vol_squared_regime_allows_power_tuning():
    s = LazySwingStrategy(
        {
            "symbol": "TEST",
            "adaptive_st_enter_ratio_threshold": 1.0,
            "adaptive_st_exit_ratio_threshold": 0.85,
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_regime_mode": "squared",
            "flip_vol_ratio_regime_power": 1.5,
            "flip_vol_ratio_regime_low_min": 0.75,
            "flip_vol_ratio_regime_high_min": 1.0,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 2.5,
        }
    )
    ix = pd.date_range("2026-01-01", periods=1, freq="h", tz="UTC")
    s._vol_regime_high = pd.Series([False], index=ix)
    s._vol_regime_ratio = pd.Series([0.925], index=ix)

    params = s._active_flip_vol_params(0)

    assert round(params["regime_weight"], 4) == 0.3536
    assert round(params["active_ratio_min_decimal"], 4) == 0.8384
    assert round(params["active_stop_pct_decimal"], 4) == 0.0153


def test_flip_vol_ratio_allows_uses_dynamic_active_threshold():
    s = LazySwingStrategy(
        {
            "symbol": "TEST",
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_regime_mode": "squared",
            "adaptive_st_enter_ratio_threshold": 1.0,
            "adaptive_st_exit_ratio_threshold": 0.85,
            "flip_vol_ratio_regime_low_min": 0.75,
            "flip_vol_ratio_regime_high_min": 1.0,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 2.5,
        }
    )
    ix = pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC")
    s._vol_regime_high = pd.Series([False, True], index=ix)
    s._vol_regime_ratio = pd.Series([0.925, 1.1], index=ix)
    s._flip_vol_short = pd.Series([1.0, 1.0], index=ix)
    s._flip_vol_long_mean = pd.Series([1.0, 1.0], index=ix)
    s._flip_vol_ratio = pd.Series([0.8, 0.9], index=ix)

    allowed_low, info_low = s._flip_vol_ratio_allows(0)
    allowed_high, info_high = s._flip_vol_ratio_allows(1)

    assert allowed_low is False
    assert round(info_low["active_ratio_min"], 4) == 0.8125
    assert allowed_high is False
    assert info_high["active_ratio_min"] == 1.0


def test_flip_vol_squared_regime_can_run_with_fixed_st():
    s = LazySwingStrategy(
        {
            "symbol": "TEST",
            "flip_vol_ratio_enabled": True,
            "flip_vol_ratio_regime_mode": "squared",
            "adaptive_st_enter_ratio_threshold": 1.0,
            "adaptive_st_exit_ratio_threshold": 0.85,
            "flip_vol_ratio_regime_low_min": 0.75,
            "flip_vol_ratio_regime_high_min": 1.0,
            "flip_vol_ratio_regime_low_stop_pct": 1.0,
            "flip_vol_ratio_regime_high_stop_pct": 2.5,
        }
    )
    ix = pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC")
    s._vol_regime_high = pd.Series([False, True], index=ix)
    s._vol_regime_ratio = pd.Series([0.9, 1.1], index=ix)
    s._flip_vol_short = pd.Series([1.0, 1.0], index=ix)
    s._flip_vol_long_mean = pd.Series([1.0, 1.0], index=ix)
    s._flip_vol_ratio = pd.Series([0.8, 0.9], index=ix)

    low = s._active_flip_vol_params(0)
    high = s._active_flip_vol_params(1)

    assert round(low["active_ratio_min_decimal"], 4) == 0.7778
    assert round(low["active_stop_pct_decimal"], 4) == 0.0117
    assert high["active_ratio_min_decimal"] == 1.0
    assert high["active_stop_pct_decimal"] == 0.025


def test_vol_regime_uses_hysteresis_and_min_dwell():
    s = LazySwingStrategy({
        "symbol": "TEST",
        "adaptive_st_enter_ratio_threshold": 0.9,
        "adaptive_st_exit_ratio_threshold": 0.8,
        "adaptive_st_min_high_bars": 4,
    })
    ix = pd.date_range("2026-01-01", periods=8, freq="h", tz="UTC")
    vol_ratio = pd.Series([0.85, 0.95, 0.75, 0.78, 0.79, 0.81, 0.77, 0.76], index=ix)
    regime = s._build_vol_regime(vol_ratio)
    assert regime.tolist() == [False, True, True, True, False, False, False, False]
