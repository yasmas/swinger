"""
Download historical silver futures OHLCV from Databento and resample to 5m.

Downloads SI continuous front-month 1m bars from GLBX.MDP3 for 2022-2024,
then writes monthly and combined 5m CSVs under data/silver.

Usage:
    python src/data_sources/download_binance_silver.py
"""

import os
import sys
from pathlib import Path

import pandas as pd

SYMBOL = "SI"
DATASET = "GLBX.MDP3"
START_YEAR, START_MONTH = 2022, 1
END_YEAR, END_MONTH = 2024, 12
ROOT = Path(__file__).resolve().parent.parent.parent
SILVER_DIR = ROOT / "data" / "silver"
CACHE_DIR = SILVER_DIR / "databento" / "SI" / "1m"
MONTHLY_OUT_DIR = SILVER_DIR / "5m" / "monthly"
COMBINED_OUT = SILVER_DIR / "SI-5m-2022-2024.csv"


def _load_api_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("ERROR: DATABENTO_API_KEY not found in .env or environment.")
        sys.exit(1)
    return key


def _months_range(start_year, start_month, end_year, end_month):
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def _download_month(client, year: int, month: int) -> Path | None:
    cache_file = CACHE_DIR / f"SI-1m-{year}-{month:02d}.parquet"
    if cache_file.exists():
        print(f"  [{year}-{month:02d}] Cached, skipping.")
        return cache_file

    start = f"{year}-{month:02d}-01"
    end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"

    print(f"  [{year}-{month:02d}] Downloading 1m bars ...", end="", flush=True)
    try:
        data = client.timeseries.get_range(
            dataset=DATASET,
            symbols=[f"{SYMBOL}.c.0"],
            stype_in="continuous",
            schema="ohlcv-1m",
            start=start,
            end=end,
        )
        df = data.to_df()
    except Exception as exc:
        print(f" FAILED: {exc}")
        return None

    if df.empty:
        print(" no data.")
        return None

    df.to_parquet(cache_file)
    print(f" {len(df)} bars.")
    return cache_file


def _resample_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    ohlcv = df_1m[["open", "high", "low", "close", "volume"]].copy()
    df_5m = ohlcv.resample("5min").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna(subset=["open"])
    return df_5m


def main():
    import databento as db

    SILVER_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    MONTHLY_OUT_DIR.mkdir(parents=True, exist_ok=True)

    key = _load_api_key()
    client = db.Historical(key)

    print(f"\n{'=' * 60}")
    print("Downloading SI 1m data from Databento GLBX.MDP3 (2022-01 to 2024-12)")
    print(f"{'=' * 60}")

    parquet_files: list[Path] = []
    for year, month in _months_range(START_YEAR, START_MONTH, END_YEAR, END_MONTH):
        pq = _download_month(client, year, month)
        if pq:
            parquet_files.append(pq)

    if not parquet_files:
        print("No data downloaded.")
        return

    print(f"\nCombining {len(parquet_files)} months ...")
    frames = [pd.read_parquet(p) for p in sorted(parquet_files)]
    df_1m = pd.concat(frames).sort_index()
    df_1m = df_1m[~df_1m.index.duplicated(keep="first")]

    print(f"Resampling {len(df_1m)} x 1m bars -> 5m ...")
    df_5m = _resample_to_5m(df_1m)
    print(f"  {len(df_5m)} x 5m bars.")

    out = pd.DataFrame()
    out["open_time"] = (
        df_5m.index.tz_localize(None).astype("datetime64[ms]").astype("int64")
    )
    out["open"] = df_5m["open"].values
    out["high"] = df_5m["high"].values
    out["low"] = df_5m["low"].values
    out["close"] = df_5m["close"].values
    out["volume"] = df_5m["volume"].values

    out.to_csv(COMBINED_OUT, index=False)

    out_dt = pd.to_datetime(out["open_time"], unit="ms", utc=True)
    out["ym"] = out_dt.dt.strftime("%Y-%m")
    for ym, chunk in out.groupby("ym", sort=True):
        month_path = MONTHLY_OUT_DIR / f"SI-5m-{ym}.csv"
        chunk.drop(columns=["ym"]).to_csv(month_path, index=False)

    size_mb = COMBINED_OUT.stat().st_size / (1024 * 1024)
    print(f"Done: {COMBINED_OUT} ({size_mb:.2f} MB, {len(out)} rows)")
    print(f"Monthly files: {out['ym'].nunique()}")


if __name__ == "__main__":
    main()
