"""
Download split-adjusted QQQ and QLD 5m bars from Massive (Polygon).

Writes:
  data/QQQ-5m-2024-2025.csv   (dev: 2024-01-01 → 2025-12-31)
  data/QLD-5m-2024-2025.csv
  data/QQQ-5m-2026.csv        (live: 2026-01-01 → 2026-04-16)
  data/QLD-5m-2026.csv

Massive returns adjusted=true so split events are handled correctly.
Overwrites any existing files (replacing unadjusted Databento data).

Usage:
    source .venv/bin/activate
    PYTHONPATH=src python scripts/download_qqq_qld_massive.py
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from exchange.massive_rest import MassiveRestClient

DATA_DIR = ROOT / "data"

SYMBOLS = ["QQQ", "QLD"]

PERIODS = [
    ("2024-01-01", "2025-12-31", "{sym}-5m-2024-2025.csv"),
    ("2026-01-01", "2026-04-16", "{sym}-5m-2026.csv"),
]

# Massive/Polygon limit per request: 50,000 bars.
# 5m bars per trading day ≈ 78 (6.5h × 12). Chunk by month to stay well under.
CHUNK_DAYS = 30


def _date_chunks(start: str, end: str, chunk_days: int):
    """Yield (start_ms, end_ms) pairs in chunk_days increments."""
    s = pd.Timestamp(start, tz="UTC")
    e = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)  # inclusive end
    while s < e:
        chunk_end = min(s + pd.Timedelta(days=chunk_days), e)
        yield int(s.timestamp() * 1000), int(chunk_end.timestamp() * 1000)
        s = chunk_end


def download(client: MassiveRestClient, symbol: str, start: str, end: str, out_path: Path) -> None:
    print(f"  {symbol}  {start} → {end}  →  {out_path.name}")

    chunks = list(_date_chunks(start, end, CHUNK_DAYS))
    frames = []

    for i, (start_ms, end_ms) in enumerate(chunks, 1):
        print(f"    chunk {i}/{len(chunks)}...", end="", flush=True)
        try:
            df = client.fetch_ohlcv(symbol, "5m", start_ms, end_ms, limit=50_000)
        except Exception as e:
            print(f" FAILED: {e}")
            continue

        if df.empty:
            print(" no data")
            continue

        frames.append(df)
        print(f" {len(df)} bars")
        time.sleep(0.3)  # be polite to the API

    if not frames:
        print(f"  WARNING: no data downloaded for {symbol} {start}→{end}")
        return

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["open_time"]).sort_values("open_time")

    combined.to_csv(out_path, index=False)
    mb = out_path.stat().st_size / (1024 * 1024)
    print(f"  → wrote {len(combined)} bars  ({mb:.2f} MB)")


def main():
    client = MassiveRestClient()

    for start, end, filename_tpl in PERIODS:
        print(f"\n{'=' * 60}")
        print(f"Period: {start} → {end}")
        print(f"{'=' * 60}")
        for symbol in SYMBOLS:
            out_path = DATA_DIR / filename_tpl.format(sym=symbol)
            download(client, symbol, start, end, out_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
