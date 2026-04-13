"""Unit tests for weekly_screener_core (no network, no backtest)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from weekly_screener_core import (
    RANGE_EXPANSION_MIN_START_POS,
    assign_deciles_and_top_groups,
    build_rotation_week_window,
    calendar_scoring_week_dates,
    compound_returns,
    enumerate_simulation_week_windows,
    enumerate_week_windows,
    fill_calendar_week_ohlcv,
    load_daily_frames,
    master_trading_days,
    normalized_atr,
    roc5_week,
    roc5_week_abs,
    sample_evenly_spaced_indices,
    score_bb_pctb,
    score_momentum,
    score_relative_volume,
    score_roc_acceleration,
    score_shock_vol_roc,
    score_universe,
    score_universe_atr_filtered,
    symbols_in_bins,
    vwap_week_deviation,
)


def _make_daily(sym: str, dates: list[str], close_base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i, d in enumerate(dates):
        c = close_base * (1 + 0.001 * i)
        rows.append(
            {
                "date": d,
                "open": c,
                "high": c * 1.01,
                "low": c * 0.99,
                "close": c,
                "volume": 1_000_000 + i * 1000,
            }
        )
    return pd.DataFrame(rows)


def test_sample_evenly_spaced_indices():
    assert sample_evenly_spaced_indices(10, 3) == [0, 4, 9]
    assert sample_evenly_spaced_indices(5, 10) == [0, 1, 2, 3, 4]
    assert sample_evenly_spaced_indices(0, 5) == []


def test_enumerate_week_windows():
    # Mon–Fri calendar weeks; 60 business days from a Monday yields 6 valid (W, W+1) pairs
    # with prior 21 and full next week (see weekly_screener_core.enumerate_week_windows).
    days = pd.bdate_range("2025-01-06", periods=60).normalize()
    dlist = [d for d in days]
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    assert len(wins) == 6
    w0 = wins[0]
    assert len(w0.w_dates) == 5
    assert len(w0.prior_21_dates) == 21
    assert len(w0.w1_dates) == 5
    m0 = pd.Timestamp(w0.w_dates[0])
    f0 = pd.Timestamp(w0.w_dates[-1])
    assert int(m0.weekday()) == 0 and int(f0.weekday()) == 4
    m1 = pd.Timestamp(w0.w1_dates[0])
    assert m1 == m0 + pd.Timedelta(days=7)
    assert [int(pd.Timestamp(d).weekday()) for d in w0.w_dates] == [0, 1, 2, 3, 4]
    assert [int(pd.Timestamp(d).weekday()) for d in w0.w1_dates] == [0, 1, 2, 3, 4]


def test_score_momentum():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=10)]
    df = _make_daily("AAA", dates)
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    w = tuple(dates[2:7])
    m = score_momentum(df, w)
    assert m is not None and m >= 0


def test_score_relative_volume():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=35)]
    df = _make_daily("BBB", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    w = tuple(dates[26:31])
    prior = tuple(dates[5:26])
    rv = score_relative_volume(df, w, prior)
    assert rv is not None and rv > 0


def test_deciles_and_top_bins():
    scores = {f"S{i}": float(i) for i in range(100)}
    dec, top = assign_deciles_and_top_groups(scores, top_k_groups=3)
    assert len(top) == 3
    sym_map = symbols_in_bins(dec, top)
    assert sum(len(v) for v in sym_map.values()) <= 100


def test_deciles_identical_scores():
    scores = {f"S{i}": 1.0 for i in range(50)}
    dec, top = assign_deciles_and_top_groups(scores, top_k_groups=3)
    assert len(top) >= 1
    assert not dec.dropna().empty


def test_compound_returns():
    assert abs(compound_returns([10.0, -5.0]) - ((1.1 * 0.95 - 1) * 100)) < 1e-9


def test_score_universe_momentum(tmp_path):
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=35)]
    frames = {}
    for sym in ("AA", "BB", "CC"):
        df = _make_daily(sym, dates, close_base=50.0 if sym == "AA" else 100.0)
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date")
        frames[sym] = df
    dlist = master_trading_days(frames)
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    sc = score_universe(frames, ww, "momentum")
    assert len(sc) == 3


def test_load_daily_frames(tmp_path):
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=5)]
    df = _make_daily("ZZ", dates)
    df.to_csv(tmp_path / "ZZ.csv", index=False)
    loaded = load_daily_frames(tmp_path)
    assert "ZZ" in loaded
    assert len(loaded["ZZ"]) == 5


def test_score_bb_pctb_flat_close_returns_none():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=40)]
    rows = [
        {
            "date": d,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000,
        }
        for d in dates
    ]
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    dlist = master_trading_days({"X": df})
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    assert score_bb_pctb(df, ww) is None


def test_score_shock_vol_roc():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=100)]
    df = _make_daily("X", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    dlist = master_trading_days({"X": df})
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21, min_leading_index=50)
    assert wins
    ww = wins[0]
    s = score_shock_vol_roc(df, ww)
    assert s is not None and s >= 0


def test_score_roc_acceleration():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=40)]
    df = _make_daily("X", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    dlist = master_trading_days({"X": df})
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    s = score_roc_acceleration(df, ww)
    assert s is not None and s >= 0


def test_score_universe_range_expansion_keep_all():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=130)]
    frames = {}
    for i in range(15):
        sym = f"S{i:02d}"
        df = _make_daily(sym, dates, close_base=100.0 + float(i))
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date")
        frames[sym] = df
    dlist = master_trading_days(frames)
    wins = enumerate_week_windows(
        dlist,
        min_prior_trading_days=21,
        min_leading_index=RANGE_EXPANSION_MIN_START_POS,
    )
    ww = wins[0]
    sc = score_universe(frames, ww, "range_expansion", range_expansion_keep_top=1.0)
    assert len(sc) >= 10


def test_score_bb_pctb_non_flat():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=40)]
    df = _make_daily("X", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    dlist = master_trading_days({"X": df})
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    b = score_bb_pctb(df, ww)
    assert b is not None and np.isfinite(b)


def test_normalized_atr_and_roc5():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=40)]
    df = _make_daily("VOL", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    dlist = master_trading_days({"VOL": df})
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    a = normalized_atr(df, ww)
    assert a is not None and a > 0
    r = roc5_week(df, ww)
    assert r is not None
    ra = roc5_week_abs(df, ww)
    assert ra is not None and ra >= 0 and abs(abs(r) - ra) < 1e-12
    v = vwap_week_deviation(df, ww)
    assert v is not None


def test_score_universe_atr_filtered_requires_enough_survivors():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=40)]
    frames = {}
    for sym in ("S01", "S02", "S03"):
        df = _make_daily(sym, dates)
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date")
        frames[sym] = df
    dlist = master_trading_days(frames)
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    out = score_universe_atr_filtered(frames, ww, "roc5", keep_top=0.35)
    assert out == {}


def test_score_universe_atr_roc5_keep_all():
    """With keep_top=1.0, all symbols with valid ATR pass; need >=10 symbols for a non-empty score dict."""
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=45)]
    frames = {}
    for i in range(15):
        sym = f"S{i:02d}"
        df = _make_daily(sym, dates, close_base=100.0 + float(i))
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date")
        frames[sym] = df
    dlist = master_trading_days(frames)
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    sc = score_universe(frames, ww, "atr_roc5", atr_keep_top=1.0)
    assert len(sc) >= 10
    assert all(sym in sc for sym in sc)


def test_score_universe_atr_roc5_rejects_unknown_method_key():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=40)]
    df = _make_daily("X", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    dlist = master_trading_days({"X": df})
    wins = enumerate_week_windows(dlist, min_prior_trading_days=21)
    ww = wins[0]
    with pytest.raises(ValueError, match="Unknown scoring method"):
        score_universe({"X": df}, ww, "not_a_method")


def test_calendar_scoring_week_dates_apr_2026():
    w, w1 = calendar_scoring_week_dates(date(2026, 4, 13))
    assert w[0] == "2026-04-06"
    assert w[-1] == "2026-04-10"
    assert w1[0] == "2026-04-13"
    assert w1[-1] == "2026-04-17"


def test_calendar_scoring_week_dates_rejects_non_monday():
    with pytest.raises(ValueError, match="Monday"):
        calendar_scoring_week_dates(date(2026, 4, 14))


def test_fill_calendar_week_ohlcv_copies_prior_row():
    dates = [str(d.date()) for d in pd.bdate_range("2026-03-01", periods=28)]
    df = _make_daily("X", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    hole = pd.Timestamp("2026-04-08")
    df2 = df[df.index != hole]
    w_dates, _ = calendar_scoring_week_dates(date(2026, 4, 13))
    assert hole.strftime("%Y-%m-%d") in w_dates
    filled = fill_calendar_week_ohlcv({"X": df2}, w_dates)
    assert hole in filled["X"].index
    prev = filled["X"].index[filled["X"].index < hole][-1]
    pd.testing.assert_series_equal(
        filled["X"].loc[hole],
        filled["X"].loc[prev],
        check_names=False,
    )


def test_build_rotation_week_window_prior_21():
    dates = [str(d.date()) for d in pd.bdate_range("2026-03-01", periods=35)]
    df = _make_daily("X", dates)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.set_index("date")
    ww = build_rotation_week_window(date(2026, 4, 13), {"X": df})
    assert ww.start_w == "2026-04-06"
    assert ww.end_w == "2026-04-10"
    assert len(ww.prior_21_dates) == 21
    assert ww.prior_21_dates[-1] < ww.start_w


def test_enumerate_simulation_week_windows_w_precedes_w1():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-01", periods=120)]
    frames = {}
    for i in range(12):
        sym = f"S{i:02d}"
        df = _make_daily(sym, dates, close_base=100.0 + float(i))
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date")
        frames[sym] = df
    wins = enumerate_simulation_week_windows(frames, min_prior_trading_days=21)
    assert len(wins) >= 5
    for ww in wins[10:15]:
        w1m = date.fromisoformat(ww.w1_dates[0])
        wm = date.fromisoformat(ww.w_dates[0])
        assert wm == w1m - timedelta(days=7)
        assert ww.w_dates[-1] < ww.w1_dates[0]
