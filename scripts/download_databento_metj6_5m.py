#!/usr/bin/env python3
"""Download CME Globex futures 5m OHLCV from Databento (1m → 5m resample).

Defaults target Micro Ether April 2026 (METJ6) on ``GLBX.MDP3`` via ``raw_symbol``.

Usage (repo root)::

  .venv/bin/python scripts/download_databento_metj6_5m.py \\
    --start 2026-04-01 \\
    --out data/databento/futures/METJ6-5m-2026-04.csv

(``--end`` defaults to **UTC now minus 20 minutes** if omitted; set explicitly if Databento returns 422.)

``end`` is exclusive. You can pass ``YYYY-MM-DD`` (midnight UTC) or a full ISO datetime.

Requires ``DATABENTO_API_KEY`` and a ``GLBX.MDP3`` license. Delayed/live CME boundaries may force a **lower** ``--end`` than calendar month-end (422 → pass an earlier ISO ``--end``).

Databento may emit a ``BentoWarning`` for individual **degraded** session days (metadata still retrievable).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]

DATASET = "GLBX.MDP3"


def _index_to_open_time_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    if idx.tz is not None:
        idx = idx.tz_convert("UTC")
    return (idx.astype("int64") // 1_000_000).astype(np.int64)


def _load_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO / ".env")
        load_dotenv(REPO / "data" / "yasmas" / ".env")
    except ImportError:
        pass
    k = os.environ.get("DATABENTO_API_KEY", "")
    if not k:
        print("DATABENTO_API_KEY missing (.env)", file=sys.stderr)
        sys.exit(1)
    return k


def main() -> None:
    ap = argparse.ArgumentParser(description="Databento GLBX 5m OHLCV (METJ6 default).")
    ap.add_argument("--symbol", default="METJ6", help="Globex raw symbol, e.g. METJ6")
    ap.add_argument(
        "--start",
        default="2026-04-01",
        help="Start (YYYY-MM-DD or ISO datetime, UTC if zone omitted)",
    )
    ap.add_argument(
        "--end",
        default=None,
        metavar="ISO_OR_DATE",
        help="Exclusive end (YYYY-MM-DD or ISO). Default: UTC now − 20 minutes.",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=REPO / "data/databento/futures/METJ6-5m-2026-04.csv",
    )
    args = ap.parse_args()

    import databento as db

    sym = args.symbol.strip().upper()
    key = _load_key()
    client = db.Historical(key)

    def _iso(s: str) -> str:
        s = s.strip()
        if "T" in s or s.endswith("Z"):
            return s.replace("Z", "+00:00")
        return f"{s}T00:00:00"

    start_s = _iso(args.start)
    if args.end is None:
        end_s = (
            datetime.now(timezone.utc) - timedelta(minutes=20)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    else:
        end_s = _iso(args.end)

    print(
        f"{sym} GLBX.MDP3 1m→5m: {start_s} .. {end_s} (excl) → {args.out} ...",
        flush=True,
    )
    try:
        data = client.timeseries.get_range(
            dataset=DATASET,
            symbols=[sym],
            stype_in="raw_symbol",
            schema="ohlcv-1m",
            start=start_s,
            end=end_s,
        )
        df = data.to_df()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if df is None or df.empty:
        print("no rows", file=sys.stderr)
        sys.exit(1)

    ohlcv = df[["open", "high", "low", "close", "volume"]].copy()
    df_5m = ohlcv.resample("5min").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna(subset=["open"])

    out = pd.DataFrame()
    out["open_time"] = _index_to_open_time_ms(df_5m.index)
    out["open"] = df_5m["open"].values
    out["high"] = df_5m["high"].values
    out["low"] = df_5m["low"].values
    out["close"] = df_5m["close"].values
    out["volume"] = df_5m["volume"].values
    out = out.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="first")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"wrote {len(out)} bars → {args.out}")


if __name__ == "__main__":
    main()
