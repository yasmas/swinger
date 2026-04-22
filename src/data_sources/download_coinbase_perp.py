"""
Download Coinbase International Exchange (INTX) perpetual OHLCV from the
Advanced Trade **public** market endpoint (no API key).

Default product is BTC-PERP-INTX; use ``--product ETH-PERP-INTX`` for Ether.

Products share the same candle API; earliest history is typically from
2023-08-31 (INTX launch) — use ``--start`` / ``--end`` for your window.

Max 300 candles per request at 5-minute granularity (~25 hours per chunk).

Usage (repo root)::

    python src/data_sources/download_coinbase_perp.py --product ETH-PERP-INTX \\
        --start 2026-01-01 --end 2026-04-01 --out data/backtests/eth/coinbase/ETH-PERP-INTX-5m-all.csv

    python src/data_sources/download_coinbase_perp.py --preset dev
"""

import sys
import time
import argparse
import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────

DEFAULT_PRODUCT_ID = "BTC-PERP-INTX"
GRANULARITY  = "FIVE_MINUTE"
BAR_SECONDS  = 5 * 60          # 300 s per bar
MAX_BARS     = 300              # API max per request
CHUNK_SECS   = BAR_SECONDS * MAX_BARS   # ~25 hours per request
REQUEST_DELAY = 0.15            # seconds between requests

BASE_URL = "https://api.coinbase.com/api/v3/brokerage/market/products"

# INTX launched 2023-08-31; no data before this date
DATA_START = datetime(2023, 8, 31, tzinfo=timezone.utc)

# Preset date ranges mirroring our dev/test split
PRESETS = {
    "dev":  ("2023-08-31", "2024-12-31"),   # dev set overlap (INTX only has data from Aug 2023)
    "test": ("2025-01-01", "2026-01-31"),   # test set portion with available data
}

# Output CSV columns (matches Binance format for easy interop)
COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]
HEADER  = ",".join(COLUMNS) + "\n"


# ── API ─────────────────────────────────────────────────────────────────────

def fetch_candles(product_id: str, start_ts: int, end_ts: int) -> list[dict]:
    """Fetch up to 300 candles for [start_ts, end_ts] (Unix seconds)."""
    params = urllib.parse.urlencode({
        "granularity": GRANULARITY,
        "start": start_ts,
        "end": end_ts,
    })
    url = f"{BASE_URL}/{product_id}/candles?{params}"

    req = urllib.request.Request(url, headers={"User-Agent": "swinger-downloader/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    candles = data.get("candles", [])
    # API returns newest-first; reverse to oldest-first
    candles.sort(key=lambda c: int(c["start"]))
    return candles


# ── Download loop ────────────────────────────────────────────────────────────

def download_range(
    product_id: str,
    start_dt: datetime,
    end_dt: datetime,
    output_path: Path,
) -> int:
    """Download all 5-minute candles for [start_dt, end_dt) and save to CSV."""

    # Clamp to data availability
    if start_dt < DATA_START:
        print(f"  Note: clamping start to {DATA_START.date()} (earliest available data)")
        start_dt = DATA_START

    # API returns 400 if end is in the future — clamp to "now" UTC.
    now_utc = datetime.now(timezone.utc)
    if end_dt > now_utc:
        print(f"  Note: clamping end to now UTC ({now_utc.strftime('%Y-%m-%d %H:%M')}Z); API rejects future ranges")
        end_dt = now_utc

    if start_dt >= end_dt:
        print("  No data to download in this range.")
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_bars = int((end_dt - start_dt).total_seconds() / BAR_SECONDS)
    total_chunks = -(-total_bars // MAX_BARS)  # ceiling division

    print(f"  Product: {product_id}")
    print(f"  Range : {start_dt.date()} → {end_dt.date()}")
    print(f"  Bars  : ~{total_bars:,}  ({total_chunks} requests)")
    print(f"  Output: {output_path}")

    rows_written = 0
    chunk_start = int(start_dt.timestamp())
    end_ts      = int(end_dt.timestamp())
    chunk_num   = 0

    with open(output_path, "w") as f:
        f.write(HEADER)

        while chunk_start < end_ts:
            chunk_end = min(chunk_start + CHUNK_SECS, end_ts)
            chunk_num += 1

            # Progress
            pct = (chunk_start - int(start_dt.timestamp())) / (end_ts - int(start_dt.timestamp())) * 100
            cur = datetime.fromtimestamp(chunk_start, timezone.utc).strftime("%Y-%m-%d")
            sys.stdout.write(f"\r  [{chunk_num}/{total_chunks}] {cur}  {pct:.1f}%  ")
            sys.stdout.flush()

            retries = 0
            while True:
                try:
                    candles = fetch_candles(product_id, chunk_start, chunk_end)
                    break
                except Exception as e:
                    retries += 1
                    if retries > 5:
                        raise RuntimeError(f"Failed after 5 retries: {e}")
                    wait = retries * 2
                    sys.stdout.write(f"  [retry {retries} in {wait}s: {e}]")
                    sys.stdout.flush()
                    time.sleep(wait)

            for c in candles:
                ts_ms = int(c["start"]) * 1000   # convert to milliseconds
                f.write(f"{ts_ms},{c['open']},{c['high']},{c['low']},{c['close']},{c['volume']}\n")
                rows_written += 1

            chunk_start = chunk_end
            time.sleep(REQUEST_DELAY)

    sys.stdout.write("\n")
    return rows_written


# ── Entry point ──────────────────────────────────────────────────────────────

def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main():
    parser = argparse.ArgumentParser(
        description="Download INTX perpetual candles from Coinbase (public API)"
    )
    parser.add_argument(
        "--product",
        default=DEFAULT_PRODUCT_ID,
        help=f"Product id (default: {DEFAULT_PRODUCT_ID})",
    )
    parser.add_argument("--preset", choices=list(PRESETS.keys()),
                        help="Use a preset date range (dev or test)")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end",   help="End date YYYY-MM-DD (exclusive)")
    parser.add_argument("--out",   help="Output CSV path (default: auto)")
    args = parser.parse_args()

    product_id = args.product.strip().upper()
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"

    if args.preset:
        ranges = [args.preset]
    elif args.start and args.end:
        ranges = None
    else:
        # Default: download both dev and test presets
        ranges = list(PRESETS.keys())

    if ranges is None:
        # Single custom range
        start_dt = parse_date(args.start)
        end_dt   = parse_date(args.end)
        label    = f"{args.start[:4]}-{args.end[:4]}"
        out_path = Path(args.out) if args.out else data_dir / f"{product_id}-5m-{label}.csv"

        print(f"\n{'='*60}")
        print(f"{product_id}  {GRANULARITY}  custom range")
        print(f"{'='*60}")
        n = download_range(product_id, start_dt, end_dt, out_path)
        print(f"  Wrote {n:,} rows → {out_path}\n")
    else:
        for preset_name in ranges:
            start_str, end_str = PRESETS[preset_name]
            start_dt = parse_date(start_str)
            end_dt   = parse_date(end_str) + timedelta(days=1)  # make end inclusive
            label    = f"{start_str[:4]}-{end_str[:4]}" if start_str[:4] != end_str[:4] else start_str[:4]
            out_path = data_dir / f"{product_id}-5m-{label}.csv"

            print(f"\n{'='*60}")
            print(f"{product_id}  {GRANULARITY}  preset={preset_name}")
            print(f"{'='*60}")
            n = download_range(product_id, start_dt, end_dt, out_path)
            print(f"  Wrote {n:,} rows → {out_path}")

    print("\nDone.")


if __name__ == "__main__":
    main()
