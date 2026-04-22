#!/usr/bin/env python3
"""Compare paper_eth trades.csv round-trips to a LazySwing backtest trade log.

Reads:
  - data/yasmas/paper_eth/trades.csv  (live paper-sim fills)
  - reports/ETH_PERP_INTX_eth_live_Apr2026_paper_compare_lazy_swing_v2.csv (or pass --backtest)

Writes: docs/reports/paper_eth_vs_backtest_eth_live_apr2026.md
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]


@dataclass
class RoundTrip:
    side: str  # "long" | "short"
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    exit_px: float
    pnl_pct: float
    source: str


def _parse_details(details_s: str) -> dict:
    if not details_s or details_s == "{}":
        return {}
    try:
        return json.loads(details_s)
    except json.JSONDecodeError:
        return {}


def _fill_ts_utc(row) -> pd.Timestamp:
    """Prefer UTC fill_time from details; CSV `date` is local wall time without TZ."""
    det = _parse_details(str(row.get("details", "")))
    ft = det.get("fill_time")
    if ft:
        x = pd.Timestamp(ft).tz_convert("UTC")
        return pd.Timestamp(x.replace(tzinfo=None))
    return pd.to_datetime(row["date"])


def extract_round_trips_paper(df: pd.DataFrame) -> list[RoundTrip]:
    """Paper log: alternating BUY/SHORT entries with SELL/COVER exits."""
    trips: list[RoundTrip] = []
    pos: str | None = None
    entry_t = None
    entry_px = None
    for _, row in df.iterrows():
        act = str(row["action"]).upper()
        t = _fill_ts_utc(row)
        price = float(row["price"])
        if act == "BUY" and pos is None:
            pos = "long"
            entry_t, entry_px = t, price
        elif act == "SELL" and pos == "long":
            pnl = (price / entry_px - 1.0) * 100
            trips.append(
                RoundTrip("long", entry_t, t, entry_px, price, pnl, "paper")
            )
            pos = None
            entry_t = entry_px = None
        elif act == "SHORT" and pos is None:
            pos = "short"
            entry_t, entry_px = t, price
        elif act == "COVER" and pos == "short":
            pnl = (entry_px / price - 1.0) * 100
            trips.append(
                RoundTrip("short", entry_t, t, entry_px, price, pnl, "paper")
            )
            pos = None
            entry_t = entry_px = None
        else:
            # tolerate unexpected — reset
            pos = None
            entry_t = entry_px = None
    return trips


def extract_round_trips_backtest(df: pd.DataFrame) -> list[RoundTrip]:
    """Backtest log: BUY/SHORT then SELL/COVER; pnl_pct may be in details on exit."""
    trips: list[RoundTrip] = []
    pos: str | None = None
    entry_t = entry_px = None
    for _, row in df.iterrows():
        act = str(row["action"]).upper()
        if act == "HOLD":
            continue
        t = pd.to_datetime(row["date"])
        price = float(row["price"])
        det = _parse_details(str(row.get("details", "")))
        if act == "BUY" and pos is None:
            pos = "long"
            entry_t, entry_px = t, price
        elif act == "SELL" and pos == "long":
            pnl = det.get("pnl_pct")
            if pnl is None:
                pnl = (price / entry_px - 1.0) * 100
            else:
                pnl = float(pnl)
            trips.append(
                RoundTrip("long", entry_t, t, entry_px, price, pnl, "backtest")
            )
            pos = None
            entry_t = entry_px = None
        elif act == "SHORT" and pos is None:
            pos = "short"
            entry_t, entry_px = t, price
        elif act == "COVER" and pos == "short":
            pnl = det.get("pnl_pct")
            if pnl is None:
                pnl = (entry_px / price - 1.0) * 100
            else:
                pnl = float(pnl)
            trips.append(
                RoundTrip("short", entry_t, t, entry_px, price, pnl, "backtest")
            )
            pos = None
            entry_t = entry_px = None
        else:
            pos = None
            entry_t = entry_px = None
    return trips


def align_sequential(
    paper: list[RoundTrip], bt: list[RoundTrip], start: pd.Timestamp | None = None
) -> list[tuple[RoundTrip | None, RoundTrip | None]]:
    """Pair Nth paper round-trip (chronological) with Nth backtest round-trip from the cutoff."""
    if start is not None:
        paper = sorted(
            [p for p in paper if p.entry_time >= start],
            key=lambda x: x.entry_time,
        )
        bt = sorted(
            [b for b in bt if b.entry_time >= start],
            key=lambda x: x.entry_time,
        )

    n = max(len(paper), len(bt))
    pairs: list[tuple[RoundTrip | None, RoundTrip | None]] = []
    for i in range(n):
        p = paper[i] if i < len(paper) else None
        b = bt[i] if i < len(bt) else None
        pairs.append((p, b))
    return pairs


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--paper",
        type=Path,
        default=REPO / "data/yasmas/paper_eth/trades.csv",
    )
    ap.add_argument(
        "--backtest",
        type=Path,
        default=REPO
        / "reports/ETH_PERP_INTX_eth_live_Apr2026_paper_compare_lazy_swing_v2.csv",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=REPO / "docs/analyze-live-paper_eth_vs_backtest_eth_apr2026.md",
    )
    ap.add_argument(
        "--since",
        default="2026-04-07",
        help="Only compare round-trips with entry on/after this date (paper bot start).",
    )
    args = ap.parse_args()

    paper_df = pd.read_csv(args.paper)
    bt_df = pd.read_csv(args.backtest)

    paper_trips = extract_round_trips_paper(paper_df)
    bt_trips = extract_round_trips_backtest(bt_df)

    since = pd.Timestamp(args.since, tz=None)
    pairs = align_sequential(paper_trips, bt_trips, start=since)

    args.out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Paper ETH (simulated fills) vs LazySwing backtest (eth\\_live)")
    lines.append("")
    lines.append("**Backtest config:** `config/strategies/lazy_swing/eth_live.yaml` (30m, ST 20/1.5) ")
    lines.append(f"on `data/yasmas/paper_eth/ETH-PERP-INTX-5m-2026-04.csv`. **Trade log:** `{args.backtest.name}`.")
    lines.append("")
    lines.append(f"**Comparison window:** round-trips with entry (UTC) ≥ **{args.since}** 00:00. ")
    lines.append("Paper timestamps use **`fill_time` from each row’s JSON `details`** (UTC). The CSV `date` column is ")
    lines.append("local wall time and is not used when `fill_time` exists. Rows pair **#1 vs #1**, **#2 vs #2** by ")
    lines.append(" chronological entry UTC.")
    lines.append("")
    lines.append("")
    lines.append(
        "| # | Side | Paper entry → exit (UTC) | BT entry → exit (UTC) | Δ entry (m) | Side match | "
        "Paper PnL% | BT PnL% | Δ PnL% | Δ entry px | Δ exit px |"
    )
    lines.append("|---:|---|---|---|---:|---|---:|---:|---:|---:|---:|")

    n = 0
    for a, b in pairs:
        if a is None and b is None:
            continue
        n += 1
        side = (a or b).side.upper()[:1]  # L/S
        if a and b:
            d_ent = abs((b.entry_time - a.entry_time).total_seconds() / 60.0)
            side_ok = "Y" if a.side == b.side else "N"
            pap = f"{a.entry_time} → {a.exit_time}"
            bts = f"{b.entry_time} → {b.exit_time}"
            dpx_e = b.entry_px - a.entry_px
            dpx_x = b.exit_px - a.exit_px
            dp = b.pnl_pct - a.pnl_pct
            lines.append(
                f"| {n} | {side} | {pap} | {bts} | {d_ent:.0f} | {side_ok} | {a.pnl_pct:+.2f} | {b.pnl_pct:+.2f} | {dp:+.2f} | {dpx_e:+.2f} | {dpx_x:+.2f} |"
            )
        elif a is not None:
            pap = f"{a.entry_time} → {a.exit_time}"
            lines.append(
                f"| {n} | {side} | {pap} | — | — | — | {a.pnl_pct:+.2f} | — | — | — | — |"
            )
        else:
            assert b is not None
            bts = f"{b.entry_time} → {b.exit_time}"
            lines.append(
                f"| {n} | {side} | — | {bts} | — | — | — | {b.pnl_pct:+.2f} | — | — | — | — |"
            )

    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Paper round-trips (entry ≥ {args.since}): **{len([p for p in paper_trips if p.entry_time >= since])}**")
    lines.append(f"- Backtest round-trips (same filter): **{len([t for t in bt_trips if t.entry_time >= since])}**")
    lines.append(f"- Rows in this table: **{n}**")
    lines.append("")

    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
