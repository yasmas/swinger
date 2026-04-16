"""
Download QQQ and QLD 5m intraday data from Databento (XNAS.ITCH).

Downloads 1m bars, resamples to 5m, and writes two combined CSV files:
  data/QQQ-5m-2024-2025.csv   (dev set: 2024-01-01 → 2025-12-31)
  data/QLD-5m-2024-2025.csv
  data/QQQ-5m-2026.csv        (live set: 2026-01-01 → today)
  data/QLD-5m-2026.csv

API key is read from .env (DATABENTO_API_KEY).

Usage:
    source .venv/bin/activate
    python scripts/download_qqq_qld.py
"""

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Add src to path so dotenv can be loaded if available
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "databento"

SYMBOLS = ["QQQ", "QLD"]
DATASET = "XNAS.ITCH"

# Dev: 2024-01 → 2025-12
DEV_START = (2024, 1)
DEV_END = (2025, 12)

# Live: 2026-01 → last complete month before today
today = date.today()
LIVE_START = (2026, 1)
live_end_month = today.month - 1
live_end_year = today.year
if live_end_month == 0:
    live_end_month = 12
    live_end_year -= 1
LIVE_END = (live_end_year, live_end_month)


def _load_api_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY not set in .env or environment.")
        sys.exit(1)
    return key


def _months_range(start_ym, end_ym):
    year, month = start_ym
    ey, em = end_ym
    while (year, month) <= (ey, em):
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def _download_month(client, symbol: str, year: int, month: int) -> Path | None:
    cache_dir = CACHE_DIR / symbol / "1m"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{symbol}-1m-{year}-{month:02d}.parquet"

    if cache_file.exists():
        print(f"  [{year}-{month:02d}] cached")
        return cache_file

    start = f"{year}-{month:02d}-01"
    end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"

    print(f"  [{year}-{month:02d}] downloading...", end="", flush=True)
    try:
        import databento as db
        data = client.timeseries.get_range(
            dataset=DATASET,
            symbols=[symbol],
            stype_in="raw_symbol",
            schema="ohlcv-1m",
            start=start,
            end=end,
        )
        df = data.to_df()
    except Exception as e:
        print(f" FAILED: {e}")
        return None

    if df.empty:
        print(" no data")
        return None

    df.to_parquet(cache_file)
    print(f" {len(df)} rows")
    return cache_file


def _resample_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    ohlcv = df_1m[["open", "high", "low", "close", "volume"]].copy()
    return ohlcv.resample("5min").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["open"])


def _write_csv(df_5m: pd.DataFrame, out_path: Path) -> None:
    out = pd.DataFrame()
    out["open_time"] = df_5m.index.tz_localize(None).astype("datetime64[ms]").astype("int64")
    out["open"] = df_5m["open"].values
    out["high"] = df_5m["high"].values
    out["low"] = df_5m["low"].values
    out["close"] = df_5m["close"].values
    out["volume"] = df_5m["volume"].values
    out.to_csv(out_path, index=False)
    mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  → {out_path.name}  ({mb:.2f} MB, {len(out)} rows)")


def download_symbol(client, symbol: str):
    print(f"\n{'=' * 55}")
    print(f"  {symbol}")
    print(f"{'=' * 55}")

    # -- Dev: 2024-01 → 2025-12 --
    print(f"Dev period {DEV_START[0]}-{DEV_START[1]:02d} → {DEV_END[0]}-{DEV_END[1]:02d}")
    dev_files = []
    for y, m in _months_range(DEV_START, DEV_END):
        pq = _download_month(client, symbol, y, m)
        if pq:
            dev_files.append(pq)

    if dev_files:
        frames = [pd.read_parquet(f) for f in sorted(dev_files)]
        df_1m = pd.concat(frames).sort_index()
        df_1m = df_1m[~df_1m.index.duplicated(keep="first")]
        df_5m = _resample_to_5m(df_1m)
        _write_csv(df_5m, DATA_DIR / f"{symbol}-5m-2024-2025.csv")

    # -- Live: 2026-01 → last complete month --
    if LIVE_END >= LIVE_START:
        print(f"Live period {LIVE_START[0]}-{LIVE_START[1]:02d} → {LIVE_END[0]}-{LIVE_END[1]:02d}")
        live_files = []
        for y, m in _months_range(LIVE_START, LIVE_END):
            pq = _download_month(client, symbol, y, m)
            if pq:
                live_files.append(pq)

        if live_files:
            frames = [pd.read_parquet(f) for f in sorted(live_files)]
            df_1m = pd.concat(frames).sort_index()
            df_1m = df_1m[~df_1m.index.duplicated(keep="first")]
            df_5m = _resample_to_5m(df_1m)
            _write_csv(df_5m, DATA_DIR / f"{symbol}-5m-2026.csv")
    else:
        print("Live period: no complete months yet in 2026, skipping.")


def main():
    key = _load_api_key()
    import databento as db
    client = db.Historical(key)

    for symbol in SYMBOLS:
        download_symbol(client, symbol)

    print("\nDone.")


if __name__ == "__main__":
    main()
