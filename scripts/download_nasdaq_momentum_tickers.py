#!/usr/bin/env python3
"""Download 5m historical data for the nasdaq-momentum-apr13-26 tickers.

Downloads 2025-01-01 through today for: AVGO AXON INTC INTU LRCX MPWR MRVL PLTR TEAM ZS

Output: data/backtests/nasdaq-momentum-2025/{SYMBOL}-5m-2025-2026.csv
        (binance_kline format: open_time, open, high, low, close, volume)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv
load_dotenv(REPO / ".env", override=False)
load_dotenv(REPO / "data" / "yasmas" / ".env", override=False)

from exchange.massive_rest import MassiveRestClient

TICKERS = ["AVGO", "AXON", "INTC", "INTU", "LRCX", "MPWR", "MRVL", "PLTR", "TEAM", "ZS"]
OUT_DIR = REPO / "data" / "backtests" / "nasdaq-momentum-2025"
OUT_DIR.mkdir(parents=True, exist_ok=True)

START_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END_MS   = int(datetime(2026, 4, 16, tzinfo=timezone.utc).timestamp() * 1000)


def main():
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        print("ERROR: MASSIVE_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = MassiveRestClient({"api_key": key, "product_id": "AVGO"})

    for sym in TICKERS:
        out_path = OUT_DIR / f"{sym}-5m-2025-2026.csv"
        if out_path.exists():
            print(f"[{sym}] already exists ({out_path.stat().st_size // 1024}KB), skipping")
            continue

        print(f"[{sym}] downloading 2025-01-01 → 2026-04-15 …", end=" ", flush=True)
        try:
            df = client.fetch_ohlcv(
                sym, "5m",
                start_time_ms=START_MS,
                end_time_ms=END_MS,
                limit=200_000,
            )
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        if df.empty:
            print("empty response — skipping")
            continue

        # Save only the columns the backtest parser needs
        df[["open_time", "open", "high", "low", "close", "volume"]].to_csv(
            out_path, index=False
        )
        bars = len(df)
        date_range = f"{df['open_time'].iloc[0]} → {df['open_time'].iloc[-1]}"
        print(f"{bars:,} bars  [{date_range}]")
        time.sleep(0.5)  # be polite to the API

    print("\nDone. Files in:", OUT_DIR)


if __name__ == "__main__":
    main()
