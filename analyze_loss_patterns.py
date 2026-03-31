"""Deep analysis of losing trade patterns in LazySwing dev/test sets.

Goals:
1. Characterize losing trades (holding period, vol regime, consecutive losses)
2. Test whether HMACD histogram at entry discriminates winners vs losers
3. Test whether a confirmation delay (require N consecutive hourly closes
   in the new ST direction) filters whipsaws without destroying returns
4. Extended ST parameter grid search (beyond Exp 2's range)
5. Position sizing based on realised volatility regime

Usage:
    PYTHONPATH=src python analyze_loss_patterns.py
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

TRADE_LOG_DEV = "reports/LazySwing_Dev_lazy_swing_v3.csv"
TRADE_LOG_TEST = "reports/LazySwing_Test_lazy_swing_v3.csv"
PRICE_DATA_DEV = "data/BTCUSDT-5m-2022-2024-combined.csv"
PRICE_DATA_TEST = "data/BTCUSDT-5m-test-combined.csv"


def load_price_data(path):
    df = pd.read_csv(path)
    ts = df["open_time"].astype(float)
    ms = ts.where(ts < 1e15, ts / 1000)
    df["date"] = pd.to_datetime(ms, unit="ms")
    df = df.set_index("date")
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    df["volume"] = df["volume"].astype(float)
    return df[["open", "high", "low", "close", "volume"]].sort_index()


def resample_hourly(df):
    return df.resample("1h").agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])


def compute_atr(h, period=14):
    tr = pd.concat([
        h["high"] - h["low"],
        (h["high"] - h["close"].shift(1)).abs(),
        (h["low"] - h["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_realised_vol(h, period=20):
    lr = np.log(h["close"] / h["close"].shift(1))
    return lr.rolling(period).std() * 100


def compute_supertrend(highs, lows, closes, atr_period, multiplier):
    from strategies.intraday_indicators import compute_supertrend as _cst
    return _cst(highs, lows, closes, atr_period, multiplier)


def compute_hmacd(closes, fast, slow, signal):
    from strategies.intraday_indicators import compute_hmacd as _ch
    return _ch(closes, fast, slow, signal)


def build_trades(path):
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    actions = df[df["action"].isin(["BUY", "SELL", "SHORT", "COVER"])].copy()
    actions["dp"] = actions["details"].apply(json.loads)

    trades = []
    pending = None
    for _, row in actions.iterrows():
        a = row["action"]
        if a in ("BUY", "SHORT"):
            pending = row
        elif a in ("SELL", "COVER") and pending is not None:
            d = row["dp"]
            ed = pending["dp"]
            pnl = d.get("pnl_pct", 0.0)
            bh = d.get("bars_held", 1)
            hours = bh * 5 / 60
            trades.append({
                "entry_date": pending["date"],
                "exit_date": row["date"],
                "direction": "long" if pending["action"] == "BUY" else "short",
                "entry_price": pending["price"],
                "exit_price": row["price"],
                "pnl_pct": pnl,
                "bars_held": bh,
                "hours_held": round(hours, 2),
                "exit_reason": d.get("exit_reason", ""),
                "is_winner": 1 if pnl > 0 else 0,
            })
            pending = None
    return pd.DataFrame(trades)


def lookup_indicator(trades, hourly_index, indicator_series, col_name):
    vals = []
    for entry_dt in trades["entry_date"]:
        floored = entry_dt.floor("h")
        idx = hourly_index.get_indexer([floored], method="ffill")[0]
        vals.append(indicator_series.iloc[idx] if idx >= 0 else np.nan)
    trades[col_name] = vals


def analyze_dataset(label, trade_log_path, price_path):
    print(f"\n{'=' * 80}")
    print(f"  DATASET: {label}")
    print(f"{'=' * 80}")

    price_5m = load_price_data(price_path)
    hourly = resample_hourly(price_5m)
    trades = build_trades(trade_log_path)

    print(f"  {len(trades)} round-trip trades, {len(hourly)} hourly bars")

    # ── 1. Basic win/loss characteristics ────────────────────────────────
    winners = trades[trades["is_winner"] == 1]
    losers = trades[trades["is_winner"] == 0]
    print(f"\n--- Win/Loss Characteristics ---")
    print(f"  Winners: {len(winners)} ({len(winners)/len(trades)*100:.1f}%), avg PnL = {winners['pnl_pct'].mean():.2f}%, avg hold = {winners['hours_held'].mean():.1f}h")
    print(f"  Losers:  {len(losers)} ({len(losers)/len(trades)*100:.1f}%), avg PnL = {losers['pnl_pct'].mean():.2f}%, avg hold = {losers['hours_held'].mean():.1f}h")

    # ── 2. Holding period analysis ───────────────────────────────────────
    print(f"\n--- Holding Period vs Outcome ---")
    buckets = [(0, 4, "<4h"), (4, 12, "4-12h"), (12, 24, "12-24h"),
               (24, 72, "1-3d"), (72, 168, "3-7d"), (168, 9999, ">7d")]
    for lo, hi, lbl in buckets:
        sub = trades[(trades["hours_held"] >= lo) & (trades["hours_held"] < hi)]
        if len(sub) == 0:
            continue
        wr = sub["is_winner"].mean() * 100
        avg_pnl = sub["pnl_pct"].mean()
        print(f"  {lbl:>8s}: n={len(sub):>4d}  WR={wr:5.1f}%  avg PnL={avg_pnl:+.2f}%")

    # ── 3. Consecutive loss streaks ──────────────────────────────────────
    print(f"\n--- Consecutive Loss Streaks ---")
    streaks = []
    current = 0
    for w in trades["is_winner"]:
        if w == 0:
            current += 1
        else:
            if current > 0:
                streaks.append(current)
            current = 0
    if current > 0:
        streaks.append(current)
    if streaks:
        streaks_s = pd.Series(streaks)
        print(f"  Total losing streaks: {len(streaks)}")
        print(f"  Max streak: {streaks_s.max()}")
        print(f"  Mean streak: {streaks_s.mean():.1f}")
        for s in range(1, min(8, streaks_s.max() + 1)):
            print(f"    Streaks of {s}: {(streaks_s == s).sum()}")

    # WR of the trade AFTER a losing streak of length N
    print(f"\n--- WR After N Consecutive Losses ---")
    for streak_len in [1, 2, 3, 4, 5]:
        indices = []
        cl = 0
        for i, w in enumerate(trades["is_winner"]):
            if w == 0:
                cl += 1
            else:
                cl = 0
            if cl == streak_len and i + 1 < len(trades):
                indices.append(i + 1)
        if indices:
            next_trades = trades.iloc[indices]
            wr = next_trades["is_winner"].mean() * 100
            avg = next_trades["pnl_pct"].mean()
            print(f"  After {streak_len} losses: n={len(indices)}, next WR={wr:.1f}%, next avg PnL={avg:+.2f}%")

    # ── 4. Compute indicators on hourly ──────────────────────────────────
    atr = compute_atr(hourly, 20)
    atr_pct = atr / hourly["close"] * 100
    rvol = compute_realised_vol(hourly)
    st_line, st_bull = compute_supertrend(hourly["high"], hourly["low"], hourly["close"], 20, 2.5)
    hmacd_line, hmacd_signal, hmacd_hist = compute_hmacd(hourly["close"], 24, 51, 12)

    lookup_indicator(trades, hourly.index, atr_pct, "atr_pct")
    lookup_indicator(trades, hourly.index, rvol, "rvol")
    lookup_indicator(trades, hourly.index, hmacd_hist, "hmacd_hist")

    # HMACD histogram: does sign agreement with direction help?
    trades["hmacd_agrees"] = 0
    for i, row in trades.iterrows():
        h = row.get("hmacd_hist", np.nan)
        if pd.isna(h):
            continue
        if row["direction"] == "long" and h > 0:
            trades.at[i, "hmacd_agrees"] = 1
        elif row["direction"] == "short" and h < 0:
            trades.at[i, "hmacd_agrees"] = 1

    # ── 5. HMACD analysis ───────────────────────────────────────────────
    print(f"\n--- HMACD Histogram at Entry ---")
    valid = trades.dropna(subset=["hmacd_hist"])
    agrees = valid[valid["hmacd_agrees"] == 1]
    disagrees = valid[valid["hmacd_agrees"] == 0]
    print(f"  HMACD agrees with direction:    n={len(agrees)}, WR={agrees['is_winner'].mean()*100:.1f}%, avg PnL={agrees['pnl_pct'].mean():+.2f}%")
    print(f"  HMACD disagrees with direction: n={len(disagrees)}, WR={disagrees['is_winner'].mean()*100:.1f}%, avg PnL={disagrees['pnl_pct'].mean():+.2f}%")

    # HMACD histogram magnitude
    print(f"\n  HMACD histogram magnitude (|hist| at entry):")
    valid["abs_hmacd"] = valid["hmacd_hist"].abs()
    for pct in [25, 50, 75]:
        threshold = valid["abs_hmacd"].quantile(pct / 100)
        above = valid[valid["abs_hmacd"] >= threshold]
        below = valid[valid["abs_hmacd"] < threshold]
        print(f"    |hist| >= p{pct} ({threshold:.1f}): n={len(above)}, WR={above['is_winner'].mean()*100:.1f}%, avg={above['pnl_pct'].mean():+.2f}%")
        print(f"    |hist| <  p{pct} ({threshold:.1f}): n={len(below)}, WR={below['is_winner'].mean()*100:.1f}%, avg={below['pnl_pct'].mean():+.2f}%")

    # ── 6. Realised volatility regime analysis ───────────────────────────
    print(f"\n--- Realised Volatility Regime at Entry ---")
    valid_rv = trades.dropna(subset=["rvol"])
    for pct in [20, 33, 50]:
        threshold = valid_rv["rvol"].quantile(pct / 100)
        low = valid_rv[valid_rv["rvol"] < threshold]
        high = valid_rv[valid_rv["rvol"] >= threshold]
        print(f"  RVol < p{pct} ({threshold:.3f}%): n={len(low)}, WR={low['is_winner'].mean()*100:.1f}%, avg PnL={low['pnl_pct'].mean():+.2f}%")
        print(f"  RVol >= p{pct} ({threshold:.3f}%): n={len(high)}, WR={high['is_winner'].mean()*100:.1f}%, avg PnL={high['pnl_pct'].mean():+.2f}%")
        print()

    # ── 7. Simulate ST confirmation delay ────────────────────────────────
    print(f"\n--- ST Confirmation Delay Simulation ---")
    print(f"  (Require N consecutive hourly closes confirming new ST direction before trading)")

    for delay in [1, 2, 3, 4]:
        # Walk the hourly bars, detect ST flips, require delay bars of confirmation
        st_bull_arr = st_bull.values
        confirmed_flips = []
        prev_confirmed = None
        pending_dir = None
        pending_count = 0
        pending_start_idx = None

        for i in range(1, len(st_bull_arr)):
            if pd.isna(st_bull_arr[i]):
                continue
            current = bool(st_bull_arr[i])

            if pending_dir is not None:
                if current == pending_dir:
                    pending_count += 1
                    if pending_count >= delay:
                        confirmed_flips.append({
                            "idx": i,
                            "time": hourly.index[i],
                            "direction": "long" if pending_dir else "short",
                            "flip_time": hourly.index[pending_start_idx],
                        })
                        prev_confirmed = pending_dir
                        pending_dir = None
                else:
                    pending_dir = None
                    pending_count = 0

            if prev_confirmed is None:
                prev_confirmed = current
                continue

            if current != prev_confirmed and pending_dir is None:
                pending_dir = current
                pending_count = 1
                pending_start_idx = i
                if delay == 1:
                    confirmed_flips.append({
                        "idx": i,
                        "time": hourly.index[i],
                        "direction": "long" if current else "short",
                        "flip_time": hourly.index[i],
                    })
                    prev_confirmed = current
                    pending_dir = None

        # Pair confirmed flips into trades
        sim_trades = []
        for j in range(len(confirmed_flips) - 1):
            entry = confirmed_flips[j]
            exit_ = confirmed_flips[j + 1]
            entry_price = hourly["close"].iloc[entry["idx"]]
            exit_price = hourly["close"].iloc[exit_["idx"]]
            if entry["direction"] == "long":
                pnl = (exit_price / entry_price - 1) * 100
            else:
                pnl = (entry_price / exit_price - 1) * 100
            sim_trades.append({"pnl": pnl, "direction": entry["direction"]})

        if sim_trades:
            sim_df = pd.DataFrame(sim_trades)
            n = len(sim_df)
            wr = (sim_df["pnl"] > 0).mean() * 100
            avg = sim_df["pnl"].mean()
            # Approximate compounded return
            compound = 1.0
            for p in sim_df["pnl"]:
                compound *= (1 + p / 100)
            total_ret = (compound - 1) * 100
            print(f"  Delay={delay}h: trades={n}, WR={wr:.1f}%, avg PnL={avg:+.2f}%, compound={(compound-1)*100:.0f}%")
        else:
            print(f"  Delay={delay}h: no confirmed flips")

    # baseline (delay=0 = current behavior)
    # simulate from ST directly
    sim_base = []
    prev_b = None
    entry_idx = None
    entry_dir = None
    for i in range(len(st_bull)):
        if pd.isna(st_bull.iloc[i]):
            continue
        b = bool(st_bull.iloc[i])
        if prev_b is not None and b != prev_b:
            if entry_idx is not None:
                ep = hourly["close"].iloc[entry_idx]
                xp = hourly["close"].iloc[i]
                if entry_dir == "long":
                    pnl = (xp / ep - 1) * 100
                else:
                    pnl = (ep / xp - 1) * 100
                sim_base.append({"pnl": pnl})
            entry_idx = i
            entry_dir = "long" if b else "short"
        prev_b = b
    if sim_base:
        sb = pd.DataFrame(sim_base)
        comp = 1.0
        for p in sb["pnl"]:
            comp *= (1 + p / 100)
        print(f"  Delay=0h (baseline): trades={len(sb)}, WR={(sb['pnl']>0).mean()*100:.1f}%, avg PnL={sb['pnl'].mean():+.2f}%, compound={(comp-1)*100:.0f}%")

    # ── 8. Extended ST parameter grid search ─────────────────────────────
    print(f"\n--- Extended ST Parameter Grid Search ---")
    print(f"  (Simulated on hourly bars, always-in-market)")

    results = []
    for atr_p in [20, 24, 28, 32, 36]:
        for mult in [2.5, 3.0, 3.5, 4.0, 4.5, 5.0]:
            stl, stb = compute_supertrend(hourly["high"], hourly["low"], hourly["close"], atr_p, mult)
            sim = []
            prev = None
            e_idx = None
            e_dir = None
            for i in range(len(stb)):
                if pd.isna(stb.iloc[i]):
                    continue
                b = bool(stb.iloc[i])
                if prev is not None and b != prev:
                    if e_idx is not None:
                        ep = hourly["close"].iloc[e_idx]
                        xp = hourly["close"].iloc[i]
                        pnl = ((xp / ep - 1) if e_dir == "long" else (ep / xp - 1)) * 100
                        sim.append(pnl)
                    e_idx = i
                    e_dir = "long" if b else "short"
                prev = b
            if sim:
                sa = np.array(sim)
                compound = np.prod(1 + sa / 100)
                results.append({
                    "atr": atr_p, "mult": mult,
                    "trades": len(sa),
                    "wr": (sa > 0).mean() * 100,
                    "avg_pnl": sa.mean(),
                    "avg_loss": sa[sa <= 0].mean() if (sa <= 0).any() else 0,
                    "avg_win": sa[sa > 0].mean() if (sa > 0).any() else 0,
                    "compound": (compound - 1) * 100,
                })

    rdf = pd.DataFrame(results).sort_values("wr", ascending=False)
    print(f"  {'ATR':>4s} {'Mult':>5s} {'Trades':>7s} {'WR':>6s} {'AvgPnL':>8s} {'AvgWin':>8s} {'AvgLoss':>8s} {'Compound':>14s}")
    for _, r in rdf.head(20).iterrows():
        print(f"  {r['atr']:>4.0f} {r['mult']:>5.1f} {r['trades']:>7.0f} {r['wr']:>5.1f}% {r['avg_pnl']:>+7.2f}% {r['avg_win']:>+7.2f}% {r['avg_loss']:>+7.2f}% {r['compound']:>13.0f}%")

    # Print current v3 baseline for comparison
    v3 = rdf[(rdf["atr"] == 20) & (rdf["mult"] == 2.5)]
    if not v3.empty:
        r = v3.iloc[0]
        print(f"\n  Current v3 (ATR=20, Mult=2.5): WR={r['wr']:.1f}%, avg={r['avg_pnl']:+.2f}%, compound={r['compound']:.0f}%")

    return trades


def main():
    print("=" * 80)
    print("  LAZYSWING LOSS PATTERN ANALYSIS")
    print("=" * 80)

    for label, tlog, pdata in [
        ("DEV (2022-2024)", TRADE_LOG_DEV, PRICE_DATA_DEV),
        ("TEST (2020-2026)", TRADE_LOG_TEST, PRICE_DATA_TEST),
    ]:
        analyze_dataset(label, tlog, pdata)


if __name__ == "__main__":
    main()
