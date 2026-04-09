"""Download Mar-Apr 2026 data for TSLA, PDD, MU, AMD and rebuild combined CSVs.

Usage:
    source .venv/bin/activate
    DATABENTO_API_KEY=<key> python download_2026_update.py
"""

import os
import sys
from pathlib import Path
from datetime import date

import pandas as pd

SYMBOLS = ["TSLA", "PDD", "MU", "AMD"]
DATASET = "XNAS.ITCH"
DATA_DIR = Path("data/backtests")

# Full history start (existing files cover 2023-04 to 2026-02)
FULL_START = (2023, 4)
# Today for end bound
TODAY = date.today()
END_YEAR, END_MONTH = TODAY.year, TODAY.month


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
            m, y = 1, y + 1


def download_symbol(client, symbol: str) -> None:
    cache_dir = DATA_DIR / "databento" / symbol / "1m"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*50}")
    print(f"  {symbol}: fetching {FULL_START[0]}-{FULL_START[1]:02d} → {END_YEAR}-{END_MONTH:02d}")

    parquet_files = []
    for year, month in _months_range(*FULL_START, END_YEAR, END_MONTH):
        cache_file = cache_dir / f"{symbol}-1m-{year}-{month:02d}.parquet"

        if cache_file.exists():
            print(f"  [{year}-{month:02d}] cached")
            parquet_files.append(cache_file)
            continue

        start = f"{year}-{month:02d}-01"
        natural_end = f"{year}-{month+1:02d}-01" if month < 12 else f"{year+1}-01-01"
        # Cap at today (Databento end is exclusive, so today → data through yesterday).
        # This avoids 422 when the current month isn't fully published yet.
        today_str = TODAY.strftime("%Y-%m-%d")
        end = min(natural_end, today_str)

        print(f"  [{year}-{month:02d}] downloading ...", end="", flush=True)
        try:
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
            continue

        if df.empty:
            print(" no data")
            continue

        df.to_parquet(cache_file)
        print(f" {len(df)} bars saved")
        parquet_files.append(cache_file)

    if not parquet_files:
        print(f"  {symbol}: no data at all, skipping")
        return

    print(f"  Combining {len(parquet_files)} months ...", end="", flush=True)
    frames = [pd.read_parquet(f) for f in sorted(parquet_files)]
    df_1m = pd.concat(frames).sort_index()
    df_1m = df_1m[~df_1m.index.duplicated(keep="first")]
    print(f" {len(df_1m)} x 1m bars")

    print(f"  Resampling to 5m ...", end="", flush=True)
    ohlcv = df_1m[["open", "high", "low", "close", "volume"]].copy()
    df_5m = ohlcv.resample("5min").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    print(f" {len(df_5m)} x 5m bars")

    out = pd.DataFrame()
    out["open_time"] = df_5m.index.tz_localize(None).astype("datetime64[ms]").astype("int64")
    out["open"]   = df_5m["open"].values
    out["high"]   = df_5m["high"].values
    out["low"]    = df_5m["low"].values
    out["close"]  = df_5m["close"].values
    out["volume"] = df_5m["volume"].values

    out_path = DATA_DIR / f"{symbol}-5m-2023-2026.csv"
    out.to_csv(out_path, index=False)
    print(f"  Written: {out_path} ({out_path.stat().st_size / 1024 / 1024:.1f} MB, {len(out)} rows)")


def main():
    import databento as db

    key = _load_api_key()
    client = db.Historical(key)

    print(f"Updating data through {END_YEAR}-{END_MONTH:02d} for: {', '.join(SYMBOLS)}")
    for sym in SYMBOLS:
        download_symbol(client, sym)

    print("\nAll done.")


if __name__ == "__main__":
    main()
