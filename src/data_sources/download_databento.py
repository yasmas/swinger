"""
Download historical OHLCV data from Databento for US equities/ETFs.

Databento only offers 1m bars natively, so we download 1m and resample
to 5m (or any target interval).  Monthly chunks are cached locally so
re-runs skip already-downloaded months.

API key is read from .env (DATABENTO_API_KEY).

Usage:
    source .venv/bin/activate
    python src/data_sources/download_databento.py
"""

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SYMBOLS = ["IBIT", "BITI", "MBT"]   # add more tickers here
TARGET_INTERVAL = "5min"            # resample target (pandas freq string)

# Dataset per exchange — add entries as needed
SYMBOL_DATASET = {
    "IBIT": "XNAS.ITCH",           # NASDAQ
    "BITI": "ARCX.PILLAR",         # NYSE Arca
    "MBT":  "GLBX.MDP3",           # CME Globex micro BTC futures
}
DEFAULT_DATASET = "XNAS.ITCH"

# Symbols that use continuous front-month contracts
CONTINUOUS_SYMBOLS = {"MBT"}  # stype_in='continuous', symbol='MBT.c.0'
START_YEAR, START_MONTH = 2025, 1
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Databento returns prices in dollars already for equities
PRICE_SCALE = 1.0

# CSV columns we output (same structure our backtester can ingest)
OUT_COLUMNS = [
    "open_time",   # epoch-ms UTC
    "open",
    "high",
    "low",
    "close",
    "volume",
]


def _load_api_key() -> str:
    """Load DATABENTO_API_KEY from .env or environment."""
    # Try python-dotenv first
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
        load_dotenv(env_path)
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


def _download_month(client, symbol: str, dataset: str, year: int, month: int, cache_dir: Path) -> Path | None:
    """Download one month of 1m bars from Databento.  Returns path to cached parquet, or None."""
    cache_file = cache_dir / f"{symbol}-1m-{year}-{month:02d}.parquet"

    if cache_file.exists():
        print(f"  [{year}-{month:02d}] Cached, skipping.")
        return cache_file

    # Build date range for this month
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"

    print(f"  [{year}-{month:02d}] Downloading 1m bars ...", end="", flush=True)
    try:
        import databento as db
        # Use continuous front-month for futures symbols
        if symbol in CONTINUOUS_SYMBOLS:
            query_symbol = f"{symbol}.c.0"
            stype_in = "continuous"
        else:
            query_symbol = symbol
            stype_in = "raw_symbol"

        data = client.timeseries.get_range(
            dataset=dataset,
            symbols=[query_symbol],
            stype_in=stype_in,
            schema="ohlcv-1m",
            start=start,
            end=end,
        )
        df = data.to_df()
    except Exception as e:
        print(f" FAILED: {e}")
        return None

    if df.empty:
        print(f" no data.")
        return None

    # Save raw 1m data as parquet for fast reload
    df.to_parquet(cache_file)
    print(f" {len(df)} bars.")
    return cache_file


def _resample_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Resample 1m OHLCV bars to 5m."""
    # Index is ts_event (datetime64[ns, UTC])
    ohlcv = df_1m[["open", "high", "low", "close", "volume"]].copy()
    df_5m = ohlcv.resample("5min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    return df_5m


def download_symbol(symbol: str):
    """Download all months for one symbol, resample to 5m, write combined CSV."""
    key = _load_api_key()
    import databento as db
    client = db.Historical(key)

    today = date.today()
    end_year, end_month = today.year, today.month
    # Skip current month (incomplete)
    if end_month == 1:
        end_year -= 1
        end_month = 12
    else:
        end_month -= 1

    dataset = SYMBOL_DATASET.get(symbol, DEFAULT_DATASET)
    cache_dir = DATA_DIR / "databento" / symbol / "1m"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print(f"Downloading {symbol} 1m data from {dataset} ({START_YEAR}-{START_MONTH:02d} → {end_year}-{end_month:02d})")
    print(f"{'=' * 60}")

    parquet_files = []
    for year, month in _months_range(START_YEAR, START_MONTH, end_year, end_month):
        pq = _download_month(client, symbol, dataset, year, month, cache_dir)
        if pq:
            parquet_files.append(pq)

    if not parquet_files:
        print(f"No data for {symbol}.")
        return

    # Combine all months
    print(f"\nCombining {len(parquet_files)} months ...")
    frames = [pd.read_parquet(f) for f in sorted(parquet_files)]
    df_1m = pd.concat(frames).sort_index()
    # Drop duplicates (month boundaries)
    df_1m = df_1m[~df_1m.index.duplicated(keep="first")]

    # Resample to 5m
    print(f"Resampling {len(df_1m)} × 1m bars → 5m ...")
    df_5m = _resample_to_5m(df_1m)
    print(f"  {len(df_5m)} × 5m bars.")

    # Write CSV in backtester-friendly format
    out = pd.DataFrame()
    out["open_time"] = (df_5m.index.tz_localize(None).astype("datetime64[ms]").astype("int64"))  # epoch ms
    out["open"] = df_5m["open"].values
    out["high"] = df_5m["high"].values
    out["low"] = df_5m["low"].values
    out["close"] = df_5m["close"].values
    out["volume"] = df_5m["volume"].values

    out_path = DATA_DIR / f"{symbol}-5m-2025.csv"
    out.to_csv(out_path, index=False)
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"Done: {out_path} ({size_mb:.2f} MB, {len(out)} rows)")


def main():
    for symbol in SYMBOLS:
        download_symbol(symbol)

    print("\nAll downloads complete.")
    print("\nFinal files:")
    for symbol in SYMBOLS:
        p = DATA_DIR / f"{symbol}-5m-2025.csv"
        if p.exists():
            print(f"  {p}  ({p.stat().st_size / (1024 * 1024):.2f} MB)")


if __name__ == "__main__":
    main()
