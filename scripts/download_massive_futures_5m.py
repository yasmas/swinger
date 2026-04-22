#!/usr/bin/env python3
"""Download 5m OHLCV for a CME-style futures root+expiry ticker from Massive (Polygon-compatible).

Uses the **futures** REST API (``GET /futures/v1/aggs/{ticker}``), not ``/v2/aggs/ticker/...``
(which is for stocks and returns no rows for contracts like METJ6).

Example — Micro Ether April 2026, April 2026 through 18 Apr (exclusive end 19 Apr UTC date filter)::

  python scripts/download_massive_futures_5m.py \\
    --ticker METJ6 \\
    --window-start-gte 2026-04-01 --window-start-lt 2026-04-19 \\
    --out data/massive/futures/METJ6-5m-2026-04.csv

Requires ``MASSIVE_API_KEY`` with **futures** entitlement (Stocks SIP alone is not enough).

Resolution is passed as ``5min`` per Massive futures docs.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (REPO / ".env", REPO / "data" / "yasmas" / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


def _fetch_all(
    base_url: str,
    api_key: str,
    ticker: str,
    params: dict,
    timeout: int = 60,
) -> list[dict]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    url = f"{base_url.rstrip('/')}/futures/v1/aggs/{requests.utils.quote(ticker, safe=':')}"
    all_rows: list[dict] = []
    page = 0
    while url:
        r = requests.get(url, headers=headers, params=params if page == 0 else None, timeout=timeout)
        if r.status_code in (401, 403):
            try:
                msg = r.json().get("message", r.text)
            except Exception:
                msg = r.text
            raise RuntimeError(f"HTTP {r.status_code}: {msg}")
        r.raise_for_status()
        data = r.json()
        chunk = data.get("results") or []
        all_rows.extend(chunk)
        next_url = data.get("next_url")
        url = next_url if next_url else None
        params = {}
        page += 1
        if page > 500:
            raise RuntimeError("Pagination exceeded 500 pages — aborting")
    return all_rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Massive futures v1 5m OHLCV download.")
    ap.add_argument("--ticker", required=True, help="e.g. METJ6 (Micro Ether Apr 2026)")
    ap.add_argument(
        "--window-start-gte",
        required=True,
        help="YYYY-MM-DD (inclusive), passed as window_start.gte",
    )
    ap.add_argument(
        "--window-start-lt",
        required=True,
        help="YYYY-MM-DD (exclusive upper bound), passed as window_start.lt",
    )
    ap.add_argument("--out", type=Path, required=True, help="Output CSV path")
    ap.add_argument(
        "--base-url",
        default=os.getenv("MASSIVE_BASE_URL", "https://api.massive.com"),
        help="Default api.massive.com; override if your key is pinned to polygon.io",
    )
    args = ap.parse_args()

    _load_dotenv()
    key = os.getenv("MASSIVE_API_KEY", "")
    if not key:
        print("MASSIVE_API_KEY missing (.env)", file=sys.stderr)
        sys.exit(1)

    q = {
        "resolution": "5min",
        "limit": 50_000,
        "window_start.gte": args.window_start_gte,
        "window_start.lt": args.window_start_lt,
    }
    print(
        f"{args.ticker}: futures 5m {args.window_start_gte} .. {args.window_start_lt} (lt excl) → {args.out} ...",
        flush=True,
    )
    try:
        rows = _fetch_all(args.base_url, key, args.ticker.upper(), q)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("no rows returned", file=sys.stderr)
        sys.exit(1)

    # window_start is nanoseconds since epoch
    recs = []
    for bar in rows:
        ws = int(bar["window_start"])
        open_ms = ws // 1_000_000
        recs.append({
            "open_time": open_ms,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume") or 0),
        })
    df = pd.DataFrame(recs)
    df = df.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="first")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print(f"wrote {len(df)} bars → {args.out}")


if __name__ == "__main__":
    main()
