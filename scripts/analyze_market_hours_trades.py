#!/usr/bin/env python3
"""Analyze backtest trades by market-hours session at entry and exit.

Groups:
  A — entered RTH,      exited RTH       (9:30–16:00 ET)
  B — entered RTH,      exited extended  (pre/after-hours)
  C — entered extended, exited RTH
  D — entered extended, exited extended

Usage:
  python scripts/analyze_market_hours_trades.py <trades.csv>
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
RTH_OPEN  = (9, 30)   # 9:30 AM ET
RTH_CLOSE = (16, 0)   # 4:00 PM ET


def session(ts: pd.Timestamp) -> str:
    """Classify a UTC timestamp as 'rth' or 'extended'."""
    et = ts.tz_localize("UTC").tz_convert(ET)
    if et.weekday() >= 5:
        return "extended"
    h, m = et.hour, et.minute
    t = h * 60 + m
    rth_open_min  = RTH_OPEN[0]  * 60 + RTH_OPEN[1]
    rth_close_min = RTH_CLOSE[0] * 60 + RTH_CLOSE[1]
    return "rth" if rth_open_min <= t < rth_close_min else "extended"


def group_label(entry_sess: str, exit_sess: str) -> str:
    if entry_sess == "rth"      and exit_sess == "rth":      return "A"
    if entry_sess == "rth"      and exit_sess == "extended":  return "B"
    if entry_sess == "extended" and exit_sess == "rth":       return "C"
    return "D"


def load_trades(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], format="mixed", utc=False)
    df["details"] = df["details"].apply(lambda x: json.loads(x) if isinstance(x, str) else {})
    return df


def pair_trades(df: pd.DataFrame) -> list[dict]:
    """FIFO-pair BUY→SELL and SHORT→COVER per symbol, return one dict per completed trade."""
    long_lots:  dict[str, deque] = defaultdict(deque)   # (qty, price, ts)
    short_lots: dict[str, deque] = defaultdict(deque)

    pairs = []
    for _, r in df.iterrows():
        act = str(r["action"]).upper()
        if act in ("HOLD", "EVICT"):
            continue
        sym  = r["symbol"]
        qty  = abs(float(r["quantity"]))
        px   = float(r["price"])
        ts   = r["date"]
        sess = session(ts)

        if act == "BUY":
            long_lots[sym].append({"qty": qty, "price": px, "ts": ts, "sess": sess})

        elif act == "SELL":
            remaining = qty
            avg_entry = 0.0
            w = 0.0
            entry_sess = None
            entry_ts   = None
            while remaining > 1e-9 and long_lots[sym]:
                lot = long_lots[sym][0]
                take = min(remaining, lot["qty"])
                avg_entry = (avg_entry * w + lot["price"] * take) / (w + take)
                w += take
                if entry_ts is None:
                    entry_ts   = lot["ts"]
                    entry_sess = lot["sess"]
                lot["qty"] -= take
                remaining  -= take
                if lot["qty"] <= 1e-9:
                    long_lots[sym].popleft()

            if entry_ts is None:
                continue  # orphan exit
            pnl_pct = (px - avg_entry) / avg_entry * 100 if avg_entry > 0 else None
            pnl_dollar = qty * (px - avg_entry)
            pairs.append({
                "symbol":      sym,
                "direction":   "long",
                "entry_ts":    entry_ts,
                "exit_ts":     ts,
                "entry_price": avg_entry,
                "exit_price":  px,
                "qty":         qty,
                "pnl_pct":     pnl_pct,
                "pnl_dollar":  pnl_dollar,
                "entry_sess":  entry_sess,
                "exit_sess":   sess,
                "group":       group_label(entry_sess, sess),
            })

        elif act == "SHORT":
            short_lots[sym].append({"qty": qty, "price": px, "ts": ts, "sess": sess})

        elif act == "COVER":
            remaining = qty
            avg_entry = 0.0
            w = 0.0
            entry_sess = None
            entry_ts   = None
            while remaining > 1e-9 and short_lots[sym]:
                lot = short_lots[sym][0]
                take = min(remaining, lot["qty"])
                avg_entry = (avg_entry * w + lot["price"] * take) / (w + take)
                w += take
                if entry_ts is None:
                    entry_ts   = lot["ts"]
                    entry_sess = lot["sess"]
                lot["qty"] -= take
                remaining  -= take
                if lot["qty"] <= 1e-9:
                    short_lots[sym].popleft()

            if entry_ts is None:
                continue
            pnl_pct    = (avg_entry - px) / avg_entry * 100 if avg_entry > 0 else None
            pnl_dollar = qty * (avg_entry - px)
            pairs.append({
                "symbol":      sym,
                "direction":   "short",
                "entry_ts":    entry_ts,
                "exit_ts":     ts,
                "entry_price": avg_entry,
                "exit_price":  px,
                "qty":         qty,
                "pnl_pct":     pnl_pct,
                "pnl_dollar":  pnl_dollar,
                "entry_sess":  entry_sess,
                "exit_sess":   sess,
                "group":       group_label(entry_sess, sess),
            })

    return pairs


def print_group_stats(pairs: list[dict]) -> None:
    by_group: dict[str, list] = defaultdict(list)
    for p in pairs:
        by_group[p["group"]].append(p)

    descs = {
        "A": "Entry RTH   → Exit RTH       (9:30–16:00 ET both)",
        "B": "Entry RTH   → Exit extended  (pre/after hours exit)",
        "C": "Entry ext'd → Exit RTH       (pre/after hours entry)",
        "D": "Entry ext'd → Exit extended  (pre/after hours both)",
    }

    total = len(pairs)
    print(f"\n{'='*72}")
    print(f"  Nasdaq Momentum 2025 Backtest — Market Hours Trade Analysis")
    print(f"  Total completed trades: {total}")
    print(f"{'='*72}\n")

    all_rows = []
    for grp in ["A", "B", "C", "D"]:
        trades = by_group.get(grp, [])
        n = len(trades)
        if n == 0:
            print(f"Group {grp}: {descs[grp]}")
            print(f"  No trades\n")
            continue

        wins    = [t for t in trades if (t["pnl_pct"] or 0) > 0]
        losses  = [t for t in trades if (t["pnl_pct"] or 0) <= 0]
        wr      = len(wins) / n * 100
        avg_pnl = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] is not None) / n
        avg_win  = sum(t["pnl_pct"] for t in wins)  / len(wins)  if wins   else 0
        avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        total_dollar = sum(t["pnl_dollar"] for t in trades)
        pf = (sum(t["pnl_dollar"] for t in wins) /
              abs(sum(t["pnl_dollar"] for t in losses))) if losses else float("inf")

        print(f"Group {grp}: {descs[grp]}")
        print(f"  Trades:       {n:>6}  ({n/total*100:.1f}% of total)")
        print(f"  Win rate:     {wr:>6.1f}%  ({len(wins)}W / {len(losses)}L)")
        print(f"  Avg PnL%:     {avg_pnl:>+7.2f}%")
        print(f"  Avg win%:     {avg_win:>+7.2f}%")
        print(f"  Avg loss%:    {avg_loss:>+7.2f}%")
        print(f"  Profit factor:{pf:>7.2f}")
        print(f"  Total $PnL:  ${total_dollar:>+12,.0f}")
        print()
        all_rows.append({
            "Group": grp, "Desc": descs[grp].split("(")[0].strip(),
            "N": n, "WR%": f"{wr:.1f}", "Avg PnL%": f"{avg_pnl:+.2f}",
            "Avg Win%": f"{avg_win:+.2f}", "Avg Loss%": f"{avg_loss:+.2f}",
            "PF": f"{pf:.2f}", "Total $": f"${total_dollar:+,.0f}",
        })

    # Per-symbol breakdown
    print(f"\n{'─'*72}")
    print("  Per-symbol trade count by group:\n")
    syms = sorted(set(p["symbol"] for p in pairs))
    header = f"  {'Symbol':<8}" + "".join(f"{'Grp '+g:>8}" for g in ["A","B","C","D"]) + f"{'Total':>8}"
    print(header)
    print("  " + "─" * (len(header)-2))
    for sym in syms:
        sym_trades = [p for p in pairs if p["symbol"] == sym]
        counts = {g: sum(1 for p in sym_trades if p["group"] == g) for g in ["A","B","C","D"]}
        row = f"  {sym:<8}" + "".join(f"{counts[g]:>8}" for g in ["A","B","C","D"]) + f"{len(sym_trades):>8}"
        print(row)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "reports/swing_party/Nasdaq_Momentum_2025_Backtest_swing_party_v1.csv"
    print(f"Loading: {path}")
    df = load_trades(path)
    print(f"Rows: {len(df)}")
    pairs = pair_trades(df)
    print_group_stats(pairs)

    # Save pairs for further analysis
    out = Path(path).with_name("trades_by_session.csv")
    pd.DataFrame(pairs).to_csv(out, index=False)
    print(f"\nDetailed pairs saved to: {out}")


if __name__ == "__main__":
    main()
