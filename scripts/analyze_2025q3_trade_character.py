#!/usr/bin/env python3
"""
Analyze trade characteristics across three ETH quarters to understand why
2025_Q3 showed minimal gains from trail-exit mechanisms.

Compares:
- 2024_Q3: trail exits added +33pp return gain
- 2025_Q1: trail exits added +29pp return gain
- 2025_Q3: trail exits added only +3.5pp return gain
"""

import csv
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import statistics

def load_trades(csv_path):
    """Extract BUY/SELL events from the full event log."""
    buy_events = []  # list of (timestamp, price)
    sell_events = []  # list of (timestamp, price)

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = row['action'].strip()
            timestamp = row['date']
            price = float(row['price'])

            if action == 'BUY':
                buy_events.append((timestamp, price))
            elif action == 'SELL':
                sell_events.append((timestamp, price))

    # Pair BUY/SELL into trades (FIFO)
    trades = []
    buy_idx = 0

    for sell_ts, sell_price in sell_events:
        if buy_idx < len(buy_events):
            buy_ts, buy_price = buy_events[buy_idx]
            buy_idx += 1

            gain_pct = (sell_price - buy_price) / buy_price * 100

            # Rough estimate of duration in 5m bars
            try:
                entry_dt = datetime.fromisoformat(buy_ts)
                exit_dt = datetime.fromisoformat(sell_ts)
                duration_bars = int((exit_dt - entry_dt).total_seconds() / 300)
            except:
                duration_bars = 0

            trades.append({
                'entry_ts': buy_ts,
                'exit_ts': sell_ts,
                'entry_price': buy_price,
                'exit_price': sell_price,
                'gain_pct': gain_pct,
                'duration_bars': duration_bars,
            })

    return trades

def analyze_trades(trades, quarter_name):
    """Compute trade character metrics."""

    if not trades:
        print(f"\n{quarter_name}: NO TRADES")
        return None

    # Parse key fields
    gains_pct = [t['gain_pct'] for t in trades]
    durations = [t['duration_bars'] for t in trades if t['duration_bars'] > 0]

    win_count = sum(1 for g in gains_pct if g > 0)
    loss_count = sum(1 for g in gains_pct if g < 0)
    break_even = len(gains_pct) - win_count - loss_count
    wr = win_count / len(gains_pct) * 100 if gains_pct else 0

    avg_duration = statistics.mean(durations) if durations else 0
    median_duration = statistics.median(durations) if durations else 0
    avg_gain = statistics.mean(gains_pct) if gains_pct else 0
    total_pnl = sum(gains_pct)

    # Winner distribution
    winners = [g for g in gains_pct if g > 0]
    big_winners = sum(1 for g in winners if g > 5.0)  # >5% trades
    small_winners = sum(1 for g in winners if 0 < g <= 5.0)  # 0-5% trades
    avg_winner = statistics.mean(winners) if winners else 0

    losers = [g for g in gains_pct if g < 0]
    avg_loser = statistics.mean(losers) if losers else 0

    return {
        'quarter': quarter_name,
        'num_trades': len(trades),
        'wins': win_count,
        'losses': loss_count,
        'breakeven': break_even,
        'win_rate_pct': wr,
        'avg_duration_bars': avg_duration,
        'median_duration_bars': median_duration,
        'avg_gain_pct': avg_gain,
        'total_pnl_pct': total_pnl,
        'avg_winner_pct': avg_winner,
        'avg_loser_pct': avg_loser,
        'big_winners_5pct': big_winners,
        'small_winners_5pct': small_winners,
        'trades': trades,
    }

def main():
    reports_dir = Path('/Users/yossi/Code/swinger-profit-exit/reports')

    # Expected trade logs
    logs = {
        '2024_Q3': reports_dir / 'LazySwing_ETH_2024_Q3_lazy_swing_v1.csv',
        '2025_Q1': reports_dir / 'LazySwing_ETH_2025_Q1_lazy_swing_v1.csv',
        '2025_Q3': reports_dir / 'LazySwing_ETH_2025_Q3_lazy_swing_v1.csv',
    }

    results = {}

    for quarter, log_path in logs.items():
        if not log_path.exists():
            print(f"WARNING: {log_path} not found")
            continue

        print(f"\nLoading {quarter} from {log_path}...")
        trades = load_trades(log_path)
        print(f"  Found {len(trades)} BUY/SELL pairs")
        metrics = analyze_trades(trades, quarter)
        results[quarter] = metrics

    # Print summary table
    print("\n" + "="*140)
    print("TRADE CHARACTER SUMMARY ACROSS QUARTERS")
    print("="*140)

    if results:
        quarters = list(results.keys())
        metrics_keys = [
            'num_trades', 'wins', 'losses', 'breakeven', 'win_rate_pct',
            'avg_duration_bars', 'median_duration_bars', 'avg_gain_pct',
            'total_pnl_pct', 'avg_winner_pct', 'avg_loser_pct',
            'big_winners_5pct', 'small_winners_5pct'
        ]

        # Header
        print(f"{'Metric':<35} | " + " | ".join(f"{q:>16}" for q in quarters))
        print("-" * 140)

        for metric in metrics_keys:
            values = []
            for q in quarters:
                if results[q] and metric in results[q]:
                    val = results[q][metric]
                    if isinstance(val, float):
                        values.append(f"{val:>16.2f}")
                    else:
                        values.append(f"{val:>16}")
                else:
                    values.append(f"{'N/A':>16}")
            print(f"{metric:<35} | " + " | ".join(values))

    # Print detailed winner/loser distribution for 2025_Q3
    print("\n" + "="*140)
    print("2025_Q3 DETAILED TRADE DISTRIBUTION")
    print("="*140)

    if results['2025_Q3']:
        trades_q3 = results['2025_Q3']['trades']
        gains = [t['gain_pct'] for t in trades_q3]
        gains_sorted = sorted(gains, reverse=True)

        print(f"\nTop 10 winning trades (% gain):")
        for i, gain in enumerate(gains_sorted[:10], 1):
            print(f"  {i:2d}. {gain:>8.3f}%")

        print(f"\nBottom 10 losing trades (% loss):")
        for i, gain in enumerate(gains_sorted[-10:], 1):
            print(f"  {i:2d}. {gain:>8.3f}%")

        # Histogram of trade outcomes
        bins = [-100, -5, -2, -1, -0.5, 0, 0.5, 1, 2, 5, 100]
        hist = defaultdict(int)
        for gain in gains:
            for j in range(len(bins)-1):
                if bins[j] <= gain < bins[j+1]:
                    hist[f"{bins[j]:>6.1f} to {bins[j+1]:<6.1f}"] += 1
                    break

        print(f"\nGain distribution (2025_Q3):")
        for key in sorted(hist.keys()):
            count = hist[key]
            pct = count / len(gains) * 100
            bar = "█" * int(pct / 2)
            print(f"  {key}: {count:4d} ({pct:5.1f}%) {bar}")

if __name__ == '__main__':
    main()
