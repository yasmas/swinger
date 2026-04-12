"""Tests for download_nasdaq_daily helpers."""

from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _import_mod():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "download_nasdaq_daily",
        REPO_ROOT / "download_nasdaq_daily.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_last_date_in_daily_csv_small_file(tmp_path):
    mod = _import_mod()
    p = tmp_path / "A.csv"
    p.write_text(
        "date,open,high,low,close,volume\n"
        "2020-01-02,1,1,1,1,1\n"
        "2020-01-15,2,2,2,2,2\n"
    )
    assert mod.last_date_in_daily_csv(p) == date(2020, 1, 15)


def test_last_date_in_daily_csv_header_only(tmp_path):
    mod = _import_mod()
    p = tmp_path / "B.csv"
    p.write_text("date,open,high,low,close,volume\n")
    assert mod.last_date_in_daily_csv(p) is None


def test_last_date_in_daily_csv_large_tail(tmp_path):
    mod = _import_mod()
    p = tmp_path / "C.csv"
    lines = ["date,open,high,low,close,volume"]
    for i in range(3000):
        lines.append(f"2020-01-{1 + (i % 28):02d},1,1,1,1,1")
    lines.append("2021-06-30,9,9,9,9,9")
    p.write_text("\n".join(lines) + "\n")
    assert mod.last_date_in_daily_csv(p) == date(2021, 6, 30)
