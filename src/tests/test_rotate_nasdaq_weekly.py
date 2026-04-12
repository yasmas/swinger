"""Tests for rotate_nasdaq_weekly helpers (imported from repo root script)."""

from datetime import date

import pandas as pd
import pytest

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _rt():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "rotate_nasdaq_weekly",
        REPO_ROOT / "rotate_nasdaq_weekly.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_monday_date_tag():
    rt = _rt()
    assert rt.monday_date_tag(date(2026, 4, 14)) == "apr14-26"
    assert rt.monday_date_tag(date(2025, 1, 5)) == "jan5-25"


def test_next_monday():
    rt = _rt()
    assert rt.next_monday(date(2026, 4, 11)) == date(2026, 4, 13)  # Sat -> Mon
    assert rt.next_monday(date(2026, 4, 12)) == date(2026, 4, 13)  # Sun -> Mon
    assert rt.next_monday(date(2026, 4, 13)) == date(2026, 4, 13)  # Mon -> same
    assert rt.next_monday(date(2026, 4, 15)) == date(2026, 4, 20)  # Wed -> next Mon


def test_last_friday_before_equity_week():
    rt = _rt()
    assert rt.last_friday_before_equity_week(date(2026, 4, 14)) == date(2026, 4, 11)


def test_verify_last_friday_daily_closes_ok(tmp_path):
    rt = _rt()
    last_fri = date(2026, 4, 10)
    df = pd.DataFrame(
        {
            "date": ["2026-04-09", "2026-04-10"],
            "open": [1.0, 1.0],
            "high": [1.0, 1.0],
            "low": [1.0, 1.0],
            "close": [1.0, 2.0],
            "volume": [1, 1],
        }
    )
    (tmp_path / "AAA.csv").write_text(df.to_csv(index=False))
    rt.verify_last_friday_daily_closes(tmp_path, last_fri)


def test_verify_last_friday_daily_closes_missing(tmp_path):
    rt = _rt()
    last_fri = date(2026, 4, 10)
    df = pd.DataFrame(
        {
            "date": ["2026-04-09"],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1],
        }
    )
    (tmp_path / "AAA.csv").write_text(df.to_csv(index=False))
    with pytest.raises(SystemExit):
        rt.verify_last_friday_daily_closes(tmp_path, last_fri)


def test_load_multi_asset_flat_data_dir(tmp_path):
    """Rotation + live bot: all symbols' CSVs in one data_dir (no per-symbol subfolders)."""
    from multi_asset_controller import load_multi_asset_datasets

    sym = "TSLA"
    csv_path = tmp_path / f"{sym}-5m-2026-04-01_2026-04-18.csv"
    csv_path.write_text(
        "open_time,open,high,low,close,volume\n"
        "1773004800000,100,101,99,100.5,1000\n"
    )

    cfg = {
        "backtest": {
            "start_date": "2026-04-14",
            "end_date": "2026-04-18",
        },
        "data_source": {
            "type": "csv_file",
            "parser": "binance_kline",
            "params": {
                "data_dir": str(tmp_path),
                "file_pattern": f"{{symbol}}-5m-2026-04-01_2026-04-18.csv",
            },
        },
        "strategy": {
            "type": "swing_party",
            "assets": [sym],
            "supertrend_atr_period": 10,
            "supertrend_multiplier": 2.0,
            "scorer": {
                "type": "volume_breakout",
                "params": {"short_window": 8, "long_window": 100},
            },
        },
    }
    ds = load_multi_asset_datasets(cfg)
    assert sym in ds
    assert len(ds[sym]) >= 1
