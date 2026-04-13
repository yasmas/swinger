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
