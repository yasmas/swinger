"""
Download historical BTCUSDT kline data from Binance Data Vision.

Downloads monthly ZIP files, extracts CSVs, and concatenates them into
a single file per resolution for easy parsing.

Usage:
    python download_binance.py
"""

import os
import sys
import zipfile
import urllib.request
from datetime import date
from pathlib import Path

SYMBOL = "BTCUSDT"
RESOLUTIONS = ["5m", "1m"]
START_YEAR, START_MONTH = 2024, 1
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
]

HEADER = ",".join(COLUMNS) + "\n"


def months_range(start_year, start_month, end_year, end_month):
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def download_file(url, dest_path):
    def progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        if total_size > 0:
            pct = min(downloaded / total_size * 100, 100)
            sys.stdout.write(f"\r  {pct:.1f}%  ({downloaded // 1024}KB / {total_size // 1024}KB)")
            sys.stdout.flush()

    urllib.request.urlretrieve(url, dest_path, reporthook=progress)
    print()


def download_resolution(resolution, data_dir):
    today = date.today()
    end_year, end_month = today.year, today.month
    # Don't request the current month -- it may not be complete
    if end_month == 1:
        end_year -= 1
        end_month = 12
    else:
        end_month -= 1

    out_dir = data_dir / resolution
    out_dir.mkdir(parents=True, exist_ok=True)

    zip_dir = out_dir / "zips"
    csv_dir = out_dir / "monthly"
    zip_dir.mkdir(exist_ok=True)
    csv_dir.mkdir(exist_ok=True)

    combined_path = data_dir / f"BTCUSDT-{resolution}.csv"

    print(f"\n{'='*60}")
    print(f"Downloading BTCUSDT {resolution} data ({START_YEAR}-{START_MONTH:02d} to {end_year}-{end_month:02d})")
    print(f"{'='*60}")

    downloaded_csvs = []

    for year, month in months_range(START_YEAR, START_MONTH, end_year, end_month):
        filename = f"{SYMBOL}-{resolution}-{year}-{month:02d}"
        zip_path = zip_dir / f"{filename}.zip"
        csv_path = csv_dir / f"{filename}.csv"

        if csv_path.exists():
            print(f"  [{year}-{month:02d}] Already exists, skipping.")
            downloaded_csvs.append(csv_path)
            continue

        url = f"{BASE_URL}/{SYMBOL}/{resolution}/{filename}.zip"
        print(f"  [{year}-{month:02d}] Downloading ...", flush=True)

        try:
            download_file(url, zip_path)
        except Exception as e:
            print(f"  [{year}-{month:02d}] WARNING: Download failed: {e}")
            if zip_path.exists():
                zip_path.unlink()
            continue

        # Extract CSV from ZIP
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                csv_name = f"{filename}.csv"
                zf.extract(csv_name, csv_dir)
            zip_path.unlink()  # Remove ZIP after extraction
            print(f"  [{year}-{month:02d}] Extracted -> {csv_path.name}")
            downloaded_csvs.append(csv_path)
        except Exception as e:
            print(f"  [{year}-{month:02d}] WARNING: Extraction failed: {e}")
            continue

    if not downloaded_csvs:
        print(f"No data downloaded for {resolution}.")
        return

    # Concatenate all monthly CSVs into one file with a header
    print(f"\nCombining {len(downloaded_csvs)} monthly files into {combined_path.name} ...")
    downloaded_csvs.sort()

    with open(combined_path, "w") as out_f:
        out_f.write(HEADER)
        for csv_path in downloaded_csvs:
            with open(csv_path, "r") as in_f:
                for line in in_f:
                    # Skip any existing header lines (rare but safe)
                    if line.startswith("open_time"):
                        continue
                    out_f.write(line)

    size_mb = combined_path.stat().st_size / (1024 * 1024)
    print(f"Done: {combined_path} ({size_mb:.1f} MB)")


def main():
    data_dir = Path(__file__).parent
    for resolution in RESOLUTIONS:
        download_resolution(resolution, data_dir)

    print("\nAll downloads complete.")
    print("\nFinal files:")
    for resolution in RESOLUTIONS:
        p = data_dir / f"BTCUSDT-{resolution}.csv"
        if p.exists():
            print(f"  {p}  ({p.stat().st_size / (1024*1024):.1f} MB)")


if __name__ == "__main__":
    main()
