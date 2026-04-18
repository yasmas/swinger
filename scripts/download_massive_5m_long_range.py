#!/usr/bin/env python3
"""Download merged 5m OHLCV from Massive/Polygon for long windows (multi-year).

Uses pagination with a high total bar limit. Writes Binance-style CSVs for SwingParty.

Usage (repo root)::

  PYTHONPATH=src python scripts/download_massive_5m_long_range.py \\
    --symbols QQQ,IWM,EEM,BNO,CPER,SLV \\
    --start 2023-11-01 --end 2026-01-01 \\
    --out-dir data/backtests/etf-mix-2024-2025 \\
    --file-pattern '{symbol}-5m-{start_year}-{end_year}-combined.csv' \\
    --start-year 2024 --end-year 2025

Requires MASSIVE_API_KEY (see .env).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (REPO / ".env", REPO / "data" / "yasmas" / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


def _ensure_src() -> None:
    s = str(REPO / "src")
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> None:
    ap = argparse.ArgumentParser(description="Massive 5m OHLCV for long date ranges.")
    ap.add_argument("--symbols", required=True, help="Comma-separated tickers")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (UTC day start)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD exclusive (UTC)")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument(
        "--file-pattern",
        default="{symbol}-5m-{start_year}-{end_year}-combined.csv",
        help="Filename template (symbol, start_year, end_year)",
    )
    ap.add_argument("--start-year", default=None, help="Override {start_year} in pattern")
    ap.add_argument("--end-year", default=None, help="Override {end_year} in pattern")
    ap.add_argument(
        "--max-bars",
        type=int,
        default=600_000,
        help="Total bar cap across pagination (default 600k)",
    )
    args = ap.parse_args()

    _load_dotenv()
    _ensure_src()
    from exchange.massive_rest import MassiveRestClient

    try:
        client = MassiveRestClient({})
    except ValueError as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    start_day = args.start
    end_excl = args.end
    t0 = pd.Timestamp(f"{start_day}T00:00:00Z")
    t1 = pd.Timestamp(f"{end_excl}T00:00:00Z")
    start_ms = int(t0.timestamp() * 1000)
    end_ms = int(t1.timestamp() * 1000)

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    sy = args.start_year or start_day[:4]
    ey = args.end_year or (pd.Timestamp(end_excl) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")[:4]

    ok = 0
    for sym in syms:
        fname = args.file_pattern.format(symbol=sym, start_year=sy, end_year=ey)
        out_path = out_dir / fname
        print(f"{sym}: {start_day} .. {end_excl} (excl) → {out_path.name} ...", end=" ", flush=True)
        try:
            df = client.fetch_ohlcv(
                sym,
                "5m",
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                limit=args.max_bars,
            )
        except Exception as e:
            print(f"FAILED ({e})")
            continue
        if df is None or df.empty:
            print("no rows")
            continue
        out = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        out = out.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="first")
        out.to_csv(out_path, index=False)
        print(f"{len(out)} bars")
        ok += 1

    print(f"Done: {ok}/{len(syms)} symbols.", file=sys.stderr)
    if ok < len(syms):
        sys.exit(1)


if __name__ == "__main__":
    main()
