#!/usr/bin/env python3
"""Download ETH-PERP-INTX 5m OHLCV from Coinbase International (public API).

Writes Binance-compatible CSV columns (``open_time`` ms + OHLCV) so existing
LazySwing configs can keep ``parser: binance_kline``.

Example (repo root, full year 2025)::

    python scripts/download_eth_perp_intx_coinbase.py \\
        --start 2025-01-01 --end 2026-01-01

Requires network access to ``api.coinbase.com``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOWNLOADER = REPO / "src" / "data_sources" / "download_coinbase_perp.py"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2025-01-01", help="UTC start date (YYYY-MM-DD)")
    ap.add_argument(
        "--end",
        default="2026-01-01",
        help="UTC end date (YYYY-MM-DD), exclusive",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=REPO / "data" / "backtests" / "eth" / "coinbase" / "ETH-PERP-INTX-5m-all.csv",
        help="Output CSV path",
    )
    args = ap.parse_args()

    cmd = [
        sys.executable,
        str(DOWNLOADER),
        "--product",
        "ETH-PERP-INTX",
        "--start",
        args.start,
        "--end",
        args.end,
        "--out",
        str(args.out.resolve()),
    ]
    raise SystemExit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
