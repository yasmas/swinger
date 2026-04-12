#!/usr/bin/env python3
"""Download one symbol’s 5m Massive range to a CSV and report ET session coverage.

Checks that bars span extended session (premarket + regular + after-hours) per trading day.
Run from repo root::

  PYTHONPATH=src .venv/bin/python scripts/verify_massive_5m_extended_hours.py \\
    --symbol AAPL --start 2025-04-07 --end-exclusive 2025-04-12

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
    for p in (REPO / ".env", REPO / "data" / "yasmas" / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument(
        "--start",
        default="2025-04-07",
        help="UTC calendar day start (inclusive), YYYY-MM-DD",
    )
    ap.add_argument(
        "--end-exclusive",
        default="2025-04-12",
        help="UTC calendar day end (exclusive), YYYY-MM-DD",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=REPO / "data" / "backtests" / "_verify_massive_5m.csv",
    )
    args = ap.parse_args()

    _load_dotenv()
    if not os.environ.get("MASSIVE_API_KEY", "").strip():
        print(
            "ERROR: MASSIVE_API_KEY not set. Add it to .env at repo root or export it.",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "src"))

    import download_swing_party_day as dsp

    ok = dsp.download_massive_5m_range(
        args.symbol, args.start, args.end_exclusive, args.out
    )
    if not ok:
        sys.exit(1)

    raw = pd.read_csv(args.out)
    if raw.empty:
        print("CSV empty")
        sys.exit(1)

    ts = pd.to_datetime(raw["open_time"].astype("int64"), unit="ms", utc=True).dt.tz_convert(
        "America/New_York"
    )

    df = pd.DataFrame({"t": ts})
    df["date_et"] = df["t"].dt.date
    df["dow"] = df["t"].dt.day_name()

    print("\n=== Per ET calendar day (America/New_York) ===\n")
    rth_open = pd.Timestamp("09:30").time()
    rth_close = pd.Timestamp("16:00").time()

    for day, g in df.groupby("date_et", sort=True):
        tmin, tmax = g["t"].min(), g["t"].max()
        n = len(g)
        # Premarket / after-hours vs regular session
        times = g["t"].dt.time
        pre = g["t"].apply(lambda x: x.time() < rth_open).any()
        after = g["t"].apply(lambda x: x.time() > rth_close).any()
        print(
            f"{day} ({g['dow'].iloc[0]}):  {n:4d} bars | "
            f"first {tmin.strftime('%H:%M')} ET  last {tmax.strftime('%H:%M')} ET | "
            f"pre-market (<9:30)={pre}  after-hours (>16:00)={after}"
        )

    print(
        "\n=== Interpretation ===\n"
        "If pre-market and after-hours are True on weekdays, SIP 5m bars include "
        "extended session (not only 9:30–16:00 ET).\n"
        "Expected ~4:00–20:00 ET span on liquid names (≈192 five-minute slots; "
        "actual count varies by halts and API).\n"
    )


if __name__ == "__main__":
    main()
