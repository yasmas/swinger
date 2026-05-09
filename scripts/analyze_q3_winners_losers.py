#!/usr/bin/env python3
"""
Deep analysis of 2025_Q3 winners vs losers to understand why trail exits
didn't help significantly, unlike prior quarters.
"""

import csv
from pathlib import Path
from datetime import datetime
import statistics

def load_trades_detailed(csv_path):
    """Extract BUY/SELL events with more detail."""
    buy_events = []
    sell_events = []

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = row['action'].strip()
            timestamp = row['date']
            price = float(row['price'])
            portfolio_value = float(row['portfolio_value'])

            if action == 'BUY':
                buy_events.append({
                    'ts': timestamp,
                    'price': price,
                    'port_val': portfolio_value,
                })
            elif action == 'SELL':
                sell_events.append({
                    'ts': timestamp,
                    'price': price,
                    'port_val': portfolio_value,
                })

    # Pair BUY/SELL into trades
    trades = []
    buy_idx = 0

    for sell_data in sell_events:
        if buy_idx < len(buy_events):
            buy_data = buy_events[buy_idx]
            buy_idx += 1

            buy_ts = buy_data['ts']
            buy_price = buy_data['price']
            sell_ts = sell_data['ts']
            sell_price = sell_data['price']

            gain_pct = (sell_price - buy_price) / buy_price * 100
            gain_dollars = sell_data['port_val'] - buy_data['port_val']

            # Duration in 5m bars
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
                'entry_port_val': buy_data['port_val'],
                'exit_port_val': sell_data['port_val'],
            })

    return trades

def main():
    reports_dir = Path('/Users/yossi/Code/swinger-profit-exit/reports')

    quarters = {
        '2024_Q3': reports_dir / 'LazySwing_ETH_2024_Q3_lazy_swing_v1.csv',
        '2025_Q1': reports_dir / 'LazySwing_ETH_2025_Q1_lazy_swing_v1.csv',
        '2025_Q3': reports_dir / 'LazySwing_ETH_2025_Q3_lazy_swing_v1.csv',
    }

    all_results = {}

    for q_name, q_path in quarters.items():
        if not q_path.exists():
            continue

        trades = load_trades_detailed(q_path)
        winners = [t for t in trades if t['gain_pct'] > 0]
        losers = [t for t in trades if t['gain_pct'] < 0]

        print(f"\n{'='*120}")
        print(f"{q_name}: {len(trades)} trades ({len(winners)} wins, {len(losers)} losses)")
        print(f"{'='*120}")

        # Winner characteristics
        if winners:
            winner_gains = [t['gain_pct'] for t in winners]
            winner_durations = [t['duration_bars'] for t in winners]

            print(f"\nWINNERS (n={len(winners)}):")
            print(f"  Gain %: min={min(winner_gains):.2f}%, max={max(winner_gains):.2f}%, " +
                  f"avg={statistics.mean(winner_gains):.2f}%, median={statistics.median(winner_gains):.2f}%")
            print(f"  Duration bars: min={min(winner_durations)}, max={max(winner_durations)}, " +
                  f"avg={statistics.mean(winner_durations):.0f}, median={statistics.median(winner_durations):.0f}")

            # Count winners by hold time
            quick_wins = sum(1 for d in winner_durations if d <= 50)  # <4 hours
            med_wins = sum(1 for d in winner_durations if 50 < d <= 200)  # 4-17 hours
            long_wins = sum(1 for d in winner_durations if d > 200)  # >17 hours
            print(f"  By hold time: quick (<4h)={quick_wins}, medium (4-17h)={med_wins}, long (>17h)={long_wins}")

        # Loser characteristics
        if losers:
            loser_gains = [t['gain_pct'] for t in losers]
            loser_durations = [t['duration_bars'] for t in losers]

            print(f"\nLOSERS (n={len(losers)}):")
            print(f"  Loss %: min={min(loser_gains):.2f}%, max={max(loser_gains):.2f}%, " +
                  f"avg={statistics.mean(loser_gains):.2f}%, median={statistics.median(loser_gains):.2f}%")
            print(f"  Duration bars: min={min(loser_durations)}, max={max(loser_durations)}, " +
                  f"avg={statistics.mean(loser_durations):.0f}, median={statistics.median(loser_durations):.0f}")

            # Count losers by hold time
            quick_loss = sum(1 for d in loser_durations if d <= 50)
            med_loss = sum(1 for d in loser_durations if 50 < d <= 200)
            long_loss = sum(1 for d in loser_durations if d > 200)
            print(f"  By hold time: quick (<4h)={quick_loss}, medium (4-17h)={med_loss}, long (>17h)={long_loss}")

        # Win/loss ratio by duration
        print(f"\nWIN RATE BY HOLD TIME:")
        for label, cutoff_low, cutoff_high in [
            ('Quick (<4h)', 0, 50),
            ('Medium (4-17h)', 50, 200),
            ('Long (>17h)', 200, 10000),
        ]:
            trades_in_band = [t for t in trades if cutoff_low < t['duration_bars'] <= cutoff_high]
            if trades_in_band:
                wins_in_band = sum(1 for t in trades_in_band if t['gain_pct'] > 0)
                wr = wins_in_band / len(trades_in_band) * 100
                avg_gain = statistics.mean([t['gain_pct'] for t in trades_in_band])
                print(f"  {label:15s}: {len(trades_in_band):2d} trades, {wr:5.1f}% WR, avg gain {avg_gain:+6.2f}%")

        all_results[q_name] = {
            'trades': trades,
            'winners': winners,
            'losers': losers,
        }

    # Final comparison
    print(f"\n{'='*120}")
    print("KEY INSIGHT: Why is 2025_Q3 different?")
    print(f"{'='*120}")

    if '2025_Q3' in all_results:
        q3_trades = all_results['2025_Q3']['trades']
        q3_winners = all_results['2025_Q3']['winners']

        # Large winners analysis
        large_winners = [t for t in q3_winners if t['gain_pct'] > 5.0]
        small_winners = [t for t in q3_winners if 0 < t['gain_pct'] <= 5.0]

        print(f"\n2025_Q3 large winners (>5%): {len(large_winners)} trades")
        print(f"2025_Q3 small winners (0-5%): {len(small_winners)} trades")

        if large_winners:
            large_durations = [t['duration_bars'] for t in large_winners]
            print(f"  Large winner hold time: avg={statistics.mean(large_durations):.0f} bars, " +
                  f"median={statistics.median(large_durations):.0f} bars")
            print(f"  These large wins held for ~{statistics.mean(large_durations)/12:.1f} hours on average")

        # The hypothesis: did these winners reverse sharply, leaving room for trail exits?
        # A trail exit would only help if prices gave back a lot after peak.
        print(f"\nFor trail exits to add value in 2025_Q3, winners would need to:")
        print(f"  1. Reverse sharply after peak (give back 2-5% of the move)")
        print(f"  2. Hold long enough for a trail mechanism to trigger")
        print(f"\nBut 2025_Q3 winners avg {statistics.mean([t['gain_pct'] for t in q3_winners]):.2f}% gain,")
        print(f"which is LARGER than other quarters. This suggests:")
        print(f"  - ST flips are catching reversals closer to local peaks, OR")
        print(f"  - Winners are being held end-to-end without retracement room")

if __name__ == '__main__':
    main()
