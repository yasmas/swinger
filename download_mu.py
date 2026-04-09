"""Download MU (Micron) 1m bars from Databento, resample to 5m, write combined CSV.

Same approach as TSLA: XNAS.ITCH dataset, 2023-04 to 2026-02.

Usage:
    source .venv/bin/activate
    DATABENTO_API_KEY=<key> python download_mu.py
"""

import os
import sys
from pathlib import Path

import pandas as pd


SYMBOL = "MU"
DATASET = "XNAS.ITCH"
DATA_DIR = Path("data/backtests")
CACHE_DIR = DATA_DIR / "databento" / SYMBOL / "1m"

# Match TSLA range
START_YEAR, START_MONTH = 2023, 4
END_YEAR, END_MONTH = 2026, 2


def _load_api_key() -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: Set DATABENTO_API_KEY in environment or .env")
        sys.exit(1)
    return key


def _months_range(sy, sm, ey, em):
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def main():
    import databento as db

    key = _load_api_key()
    client = db.Historical(key)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {SYMBOL} 1m bars from {DATASET}")
    print(f"  Range: {START_YEAR}-{START_MONTH:02d} to {END_YEAR}-{END_MONTH:02d}")

    parquet_files = []
    for year, month in _months_range(START_YEAR, START_MONTH, END_YEAR, END_MONTH):
        cache_file = CACHE_DIR / f"{SYMBOL}-1m-{year}-{month:02d}.parquet"

        if cache_file.exists():
            print(f"  [{year}-{month:02d}] Cached, skipping.")
            parquet_files.append(cache_file)
            continue

        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1}-01-01"
        else:
            end = f"{year}-{month + 1:02d}-01"

        print(f"  [{year}-{month:02d}] Downloading ...", end="", flush=True)
        try:
            data = client.timeseries.get_range(
                dataset=DATASET,
                symbols=[SYMBOL],
                stype_in="raw_symbol",
                schema="ohlcv-1m",
                start=start,
                end=end,
            )
            df = data.to_df()
        except Exception as e:
            print(f" FAILED: {e}")
            continue

        if df.empty:
            print(" no data.")
            continue

        df.to_parquet(cache_file)
        print(f" {len(df)} bars.")
        parquet_files.append(cache_file)

    if not parquet_files:
        print("No data downloaded.")
        return

    # Combine and resample to 5m
    print(f"\nCombining {len(parquet_files)} months ...")
    frames = [pd.read_parquet(f) for f in sorted(parquet_files)]
    df_1m = pd.concat(frames).sort_index()
    df_1m = df_1m[~df_1m.index.duplicated(keep="first")]

    print(f"Resampling {len(df_1m)} x 1m bars -> 5m ...")
    ohlcv = df_1m[["open", "high", "low", "close", "volume"]].copy()
    df_5m = ohlcv.resample("5min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    print(f"  {len(df_5m)} x 5m bars.")

    # Write CSV
    out = pd.DataFrame()
    out["open_time"] = df_5m.index.tz_localize(None).astype("datetime64[ms]").astype("int64")
    out["open"] = df_5m["open"].values
    out["high"] = df_5m["high"].values
    out["low"] = df_5m["low"].values
    out["close"] = df_5m["close"].values
    out["volume"] = df_5m["volume"].values

    out_path = DATA_DIR / f"{SYMBOL}-5m-2023-2026.csv"
    out.to_csv(out_path, index=False)
    print(f"\nDone: {out_path} ({out_path.stat().st_size / 1024 / 1024:.2f} MB, {len(out)} rows)")


if __name__ == "__main__":
    main()
