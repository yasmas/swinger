#!/usr/bin/env python3
"""
Replay two swing-trend trade CSVs through one shared cash pool.

Rules (per user spec):
- Merge all entry/exit events in time order (exits before entries at same timestamp
  so cash frees before new entries compete).
- On entry: if the *other* asset is flat, allow (first signal wins full allocation).
- On entry: if the other asset is in the *opposite* direction (long vs short), allow
  opening the second leg (hedge).
- On entry: if the other asset is in the *same* direction (long-long or short-short),
  skip this entry (and skip the paired exit later) — cash constrained.
- Long entries are clipped to available cash (partial fill).
- Second leg of an opposite-direction hedge uses 50% of the CSV quantity (single pool).
- Covers may draw cash negative (margin-style) so the toy book does not abort mid-replay.

Usage:
  python3 scripts/model_pair_from_trade_csvs.py \\
    reports/BTC_Swing_Trend_Dev_swing_trend_v14.csv \\
    reports/Silver_Swing_Trend_Test_swing_trend_v15-silver-thresholds.csv
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# Repo root = parent of scripts/
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class _Pos:
    qty: float
    avg: float
    is_short: bool


class LooseBook:
    """Cash + long/short per symbol; cover may draw cash negative (margin-style)."""

    def __init__(self, cash: float):
        self.cash = cash
        self.pos: dict[str, _Pos] = {}

    def buy(self, sym: str, qty: float, price: float) -> None:
        cost = qty * price
        if cost > self.cash + 1e-9:
            raise ValueError(f"Insufficient cash: need {cost:.2f}, have {self.cash:.2f}")
        self.cash -= cost
        if sym in self.pos and not self.pos[sym].is_short:
            p = self.pos[sym]
            nq = p.qty + qty
            p.avg = (p.avg * p.qty + price * qty) / nq
            p.qty = nq
        else:
            self.pos[sym] = _Pos(qty=qty, avg=price, is_short=False)

    def sell(self, sym: str, qty: float, price: float) -> None:
        p = self.pos[sym]
        self.cash += qty * price
        p.qty -= qty
        if p.qty < 1e-12:
            del self.pos[sym]

    def short(self, sym: str, qty: float, price: float) -> None:
        self.cash += qty * price
        if sym in self.pos and self.pos[sym].is_short:
            p = self.pos[sym]
            nq = p.qty + qty
            p.avg = (p.avg * p.qty + price * qty) / nq
            p.qty = nq
        else:
            self.pos[sym] = _Pos(qty=qty, avg=price, is_short=True)

    def cover(self, sym: str, qty: float, price: float) -> None:
        cost = qty * price
        self.cash -= cost
        p = self.pos[sym]
        p.qty -= qty
        if p.qty < 1e-12:
            del self.pos[sym]

    def mtm_value(self, px: dict[str, float]) -> float:
        v = self.cash
        for s, p in self.pos.items():
            pr = px.get(s, 0.0)
            if not p.is_short:
                v += p.qty * pr
            else:
                v -= p.qty * pr
        return v


@dataclass
class RoundTrip:
    asset: str  # "BTC" or "SI"
    symbol: str
    entry_ts: pd.Timestamp
    exit_ts: pd.Timestamp
    entry_action: str  # BUY or SHORT
    exit_action: str  # SELL or COVER
    qty: float
    entry_price: float
    exit_price: float


def _pair_round_trips(path: Path, asset: str, symbol: str) -> list[RoundTrip]:
    df = pd.read_csv(path, usecols=["date", "action", "symbol", "quantity", "price"])
    df["ts"] = pd.to_datetime(df["date"], utc=True)
    trips: list[RoundTrip] = []
    pending: deque[tuple[pd.Timestamp, str, float, float]] = deque()

    for _, row in df.iterrows():
        a = row["action"]
        if a not in ("BUY", "SHORT", "SELL", "COVER"):
            continue
        ts = row["ts"]
        qty = float(row["quantity"])
        price = float(row["price"])
        if a in ("BUY", "SHORT"):
            pending.append((ts, a, qty, price))
        else:
            if not pending:
                continue
            entry_ts, entry_action, q, ep = pending.popleft()
            if entry_action == "BUY" and a != "SELL":
                continue
            if entry_action == "SHORT" and a != "COVER":
                continue
            trips.append(
                RoundTrip(
                    asset=asset,
                    symbol=symbol,
                    entry_ts=entry_ts,
                    exit_ts=ts,
                    entry_action=entry_action,
                    exit_action=a,
                    qty=q,
                    entry_price=ep,
                    exit_price=price,
                )
            )
    return trips


def _direction_long(action: str) -> bool:
    return action == "BUY"


def _other_position(portfolio: LooseBook, other_symbol: str) -> str:
    """'flat', 'long', or 'short' for the other symbol."""
    if other_symbol not in portfolio.pos:
        return "flat"
    return "short" if portfolio.pos[other_symbol].is_short else "long"


def _can_open(
    portfolio: LooseBook,
    symbol: str,
    entry_action: str,
    other_symbol: str,
) -> bool:
    want = "long" if _direction_long(entry_action) else "short"
    o = _other_position(portfolio, other_symbol)
    if o == "flat":
        return True
    if o == want:
        return False
    return True  # opposite


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("btc_csv", type=Path)
    ap.add_argument("si_csv", type=Path)
    ap.add_argument("--initial-cash", type=float, default=100_000.0)
    args = ap.parse_args()

    BTC_sym = "BTCUSDT"
    SI_sym = "SI"

    btc_trips = _pair_round_trips(args.btc_csv, "BTC", BTC_sym)
    si_trips = _pair_round_trips(args.si_csv, "SI", SI_sym)

    events: list[tuple[pd.Timestamp, int, int, int, RoundTrip, str]] = []
    # Sort: all exits before entries at same ts. Among exits: SELL (long exit) before
    # COVER (short exit) so cash from selling long is available to buy back shorts.
    # Among same-kind: BTC before SI.
    for t in btc_trips:
        exit_kind = 0 if t.exit_action == "SELL" else 1
        events.append((t.entry_ts, 1, 0, 0, t, "entry"))
        events.append((t.exit_ts, 0, exit_kind, 0, t, "exit"))
    for t in si_trips:
        exit_kind = 0 if t.exit_action == "SELL" else 1
        events.append((t.entry_ts, 1, 1, 1, t, "entry"))
        events.append((t.exit_ts, 0, exit_kind, 1, t, "exit"))

    events.sort(key=lambda x: (x[0], x[1], x[2], x[3]))  # exit→entry; sell→cover; BTC→SI

    executed: set[int] = set()
    exec_qty: dict[int, float] = {}
    id_map: dict[tuple[str, pd.Timestamp, pd.Timestamp], int] = {}
    tid = 0
    for t in btc_trips:
        tid += 1
        id_map[(t.asset, t.entry_ts, t.exit_ts)] = tid
    for t in si_trips:
        tid += 1
        id_map[(t.asset, t.entry_ts, t.exit_ts)] = tid

    skipped: list[str] = []
    skipped_no_cash: list[str] = []
    accepted_entries = 0

    p = LooseBook(args.initial_cash)

    for ts, _, _ek, _ao, trip, phase in events:
        tid = id_map[(trip.asset, trip.entry_ts, trip.exit_ts)]
        sym = trip.symbol

        if phase == "exit":
            if tid not in executed:
                continue
            q = exec_qty[tid]
            if trip.entry_action == "BUY":
                p.sell(sym, q, trip.exit_price)
            else:
                p.cover(sym, q, trip.exit_price)
            del exec_qty[tid]
            executed.discard(tid)
            continue

        # entry
        other = SI_sym if sym == BTC_sym else BTC_sym
        if not _can_open(p, sym, trip.entry_action, other):
            skipped.append(
                f"{trip.asset} {trip.entry_action} @ {trip.entry_ts} "
                f"(other {other} {_other_position(p, other)})"
            )
            continue

        oth = _other_position(p, other)
        want = "long" if _direction_long(trip.entry_action) else "short"
        # Second leg of a hedge: use half the CSV size so one cash pool can support
        # long+short overlap without futures margin modelling.
        q = trip.qty
        if oth != "flat" and (
            (oth == "long" and want == "short") or (oth == "short" and want == "long")
        ):
            q = trip.qty * 0.5

        try:
            if trip.entry_action == "BUY":
                max_q = p.cash / trip.entry_price if trip.entry_price > 0 else 0.0
                q = min(q, max_q * 0.9999)
                if q * trip.entry_price < 1.0:
                    skipped_no_cash.append(
                        f"{trip.asset} BUY @ {trip.entry_ts}: cash too low for min size"
                    )
                    continue
                p.buy(sym, q, trip.entry_price)
            else:
                p.short(sym, q, trip.entry_price)
            executed.add(tid)
            exec_qty[tid] = q
            accepted_entries += 1
        except ValueError as e:
            skipped_no_cash.append(
                f"{trip.asset} {trip.entry_action} @ {trip.entry_ts}: {e}"
            )

    final_prices: dict[str, float] = {}
    if BTC_sym in p.pos:
        final_prices[BTC_sym] = float(btc_trips[-1].exit_price) if btc_trips else 0.0
    if SI_sym in p.pos:
        final_prices[SI_sym] = float(si_trips[-1].exit_price) if si_trips else 0.0

    ev = p.mtm_value(
        {BTC_sym: final_prices.get(BTC_sym, 0.0), SI_sym: final_prices.get(SI_sym, 0.0)}
    )

    n_trips = len(btc_trips) + len(si_trips)
    open_warn = ""
    if p.pos:
        open_warn = " (WARNING: open positions at end — mark incomplete)"

    print("=== Pair model (shared cash book) ===")
    print(f"  Round trips (BTC + SI): {n_trips}")
    print(f"  Entries executed (accepted): {accepted_entries}")
    print(f"  Initial cash: ${args.initial_cash:,.2f}")
    print(f"  Final equity (mark at last exit prices if still open): ${ev:,.2f}{open_warn}")
    print(f"  Total return: {(ev / args.initial_cash - 1) * 100:+.2f}%")
    print(f"  Skipped entries (same-direction conflict): {len(skipped)}")
    print(f"  Skipped entries (insufficient cash / min size): {len(skipped_no_cash)}")
    print()
    print("  Notes:")
    print("    - First-in-time signal uses CSV size; hedge second leg uses 50% size.")
    print("    - Long entries are clipped to available cash (partial fill).")
    print("    - This is a cash-only toy model; not comparable to either standalone report.")
    if skipped:
        print("\n  First 10 same-direction skips:")
        for s in skipped[:10]:
            print("   ", s)
    if skipped_no_cash:
        print("\n  First 5 cash skips:")
        for s in skipped_no_cash[:5]:
            print("   ", s)


if __name__ == "__main__":
    main()
