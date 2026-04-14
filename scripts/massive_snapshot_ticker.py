#!/usr/bin/env python3
"""GET Massive stock snapshot for one ticker (MASSIVE_API_KEY from .env).

Example::

  .venv/bin/python scripts/massive_snapshot_ticker.py
  .venv/bin/python scripts/massive_snapshot_ticker.py --symbol MRVL

Uses ``https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/{SYMBOL}``
with ``Authorization: Bearer`` (same as ``src/exchange/massive_rest.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT_TMPL = "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for p in (REPO / ".env", REPO / "data" / "yasmas" / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="AAPL", help="US stock ticker (default AAPL)")
    args = ap.parse_args()

    _load_dotenv()
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        print(
            "ERROR: MASSIVE_API_KEY not set. Add it to .env at repo root (or export it).",
            file=sys.stderr,
        )
        sys.exit(1)

    sym = args.symbol.strip().upper()
    url = SNAPSHOT_TMPL.format(symbol=sym)
    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    print(f"HTTP {r.status_code}  {url}")
    try:
        data = r.json()
    except Exception:
        print(r.text[:2000])
        r.raise_for_status()
        sys.exit(1)

    print(json.dumps(data, indent=2)[:50_000])
    r.raise_for_status()


if __name__ == "__main__":
    main()
