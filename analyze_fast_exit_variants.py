"""Aggregate per-quarter return + min-q for fast_exit experiment variants.

Reads each CSV under reports/ matching a prefix, computes per-quarter PV
delta (first->last pv in quarter), then compound across 2024Q1..2026Q2.
"""
import csv, json, glob, sys, math
from datetime import datetime
from collections import defaultdict

def parse_ts(s):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try: return datetime.strptime(s, fmt)
        except ValueError: pass
    return None

def quarter_of(dt):
    return f"{dt.year}Q{(dt.month-1)//3 + 1}"

def load_pv_series(csv_path):
    rows = []
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            try: pv = float(r.get("portfolio_value", 0))
            except: pv = 0
            ts = parse_ts(r.get("date",""))
            if pv > 0 and ts is not None:
                rows.append((ts, pv))
    return rows

def per_quarter(rows):
    q_first, q_last = {}, {}
    for ts, pv in rows:
        q = quarter_of(ts)
        if q not in q_first: q_first[q] = pv
        q_last[q] = pv
    out = {}
    for q in sorted(q_first):
        out[q] = (q_first[q], q_last[q], (q_last[q]/q_first[q] - 1) * 100)
    return out

def summary(label, csv_path):
    rows = load_pv_series(csv_path)
    if not rows:
        return None
    pq = per_quarter(rows)
    quarters = sorted(pq)
    compound = 1.0
    rets = []
    for q in quarters:
        _, _, ret = pq[q]
        compound *= (1 + ret/100)
        rets.append(ret)
    mean = sum(rets)/len(rets)
    var = sum((r-mean)**2 for r in rets)/max(len(rets)-1,1)
    sd = math.sqrt(var) if var > 0 else 0
    sharpe = (mean/sd) * math.sqrt(4) if sd > 0 else float("nan")  # quarterly -> annualized
    min_q = min(rets)
    print(f"\n== {label} ==")
    print(f"  CSV: {csv_path}")
    for q in quarters:
        s, e, r = pq[q]
        print(f"  {q}: {r:+8.2f}%   pv {s:>12,.0f} -> {e:>12,.0f}")
    print(f"  Compound: {(compound-1)*100:+.2f}%   MinQ: {min_q:+.2f}%   Sharpe(ann from quarterly): {sharpe:.2f}")
    return {"label": label, "compound_pct": (compound-1)*100, "min_q": min_q, "sharpe": sharpe, "quarters": pq}

if __name__ == "__main__":
    targets = sys.argv[1:] or [
        ("HOF_v5_baseline", "reports/ETH_30m_2024-2026_(combined_bc_+_PP4_+_ER)_—_HOF_v5_ported_lazy_swing_live_+_combined_bc_+_windowed_giveback_+_adx_lb=12_+_PP4_+_ER48_T0.32.csv"),
    ]
    # If user passes paths, treat each as (basename, path)
    if sys.argv[1:]:
        targets = [(p.rsplit("/",1)[-1][:60], p) for p in sys.argv[1:]]
    results = []
    for label, path in targets:
        r = summary(label, path)
        if r: results.append(r)

    if len(results) > 1:
        print("\n=== SIDE BY SIDE ===")
        print(f"{'Variant':<50} {'Compound':>10} {'MinQ':>9} {'Sharpe':>8}")
        for r in results:
            print(f"{r['label'][:50]:<50} {r['compound_pct']:>+9.1f}% {r['min_q']:>+8.1f}% {r['sharpe']:>8.2f}")
