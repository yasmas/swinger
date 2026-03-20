"""
Fill gaps in CME futures data using spot BTC prices.

CME micro bitcoin futures (MBT) have daily 1-hour gaps (5-6 PM ET)
and weekend gaps (~49 hours, Fri 5 PM → Sun 6 PM ET).  This utility
fills those gaps with flat-fill synthetic bars derived from spot BTC,
using a basis ratio anchored at each gap boundary.

Synthetic bars have volume=0 and is_synthetic=1 so the backtester
can update indicators without executing trades.

Usage:
    python src/data_sources/fill_cme_gaps.py \\
        --futures data/MBT-5m-2025.csv \\
        --spot data/BTCUSDT-5m-test-combined.csv \\
        --output data/MBT-5m-2025-filled.csv
"""

import argparse
import sys
from pathlib import Path

import pandas as pd


# Binance switched to microsecond timestamps on 2025-01-01
_MICROSECOND_THRESHOLD = 1e15


def _load_csv(path: str, label: str) -> pd.DataFrame:
    """Load a CSV with open_time (epoch ms) into a DatetimeIndex DataFrame."""
    df = pd.read_csv(path)
    timestamps = df["open_time"].astype(float)
    # Handle both ms and us timestamps (Binance switched in 2025)
    ms_timestamps = timestamps.where(
        timestamps < _MICROSECOND_THRESHOLD,
        timestamps / 1000,
    )
    df.index = pd.to_datetime(ms_timestamps, unit="ms", utc=True)
    df.index.name = "dt"
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    print(f"  {label}: {len(df)} bars, {df.index.min()} → {df.index.max()}")
    return df


def fill_gaps(
    futures_csv: str,
    spot_csv: str,
    output_csv: str,
    gap_threshold_min: int = 10,
    weekend_tradable: bool = False,
) -> pd.DataFrame:
    """Fill gaps in futures data with synthetic bars derived from spot prices.

    Args:
        futures_csv:  Path to futures 5m CSV (open_time, OHLCV).
        spot_csv:     Path to spot BTC 5m CSV (Binance format).
        output_csv:   Path for output CSV with is_synthetic column.
        gap_threshold_min:  Minimum gap in minutes to fill (default 10).
        weekend_tradable:   If True, weekend gap bars are marked as real
                            (is_synthetic=0) except for the daily 1-hour
                            maintenance window.  Simulates CME 24/7 trading
                            with only the daily 1-hour gap blocked.

    Returns:
        Combined DataFrame with real + synthetic bars.
    """
    print("Loading data ...")
    fut = _load_csv(futures_csv, "Futures")
    spot = _load_csv(spot_csv, "Spot")

    # Keep only OHLCV columns
    for df in [fut, spot]:
        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)
        df["volume"] = df["volume"].astype(float)

    fut["is_synthetic"] = 0

    # Detect gaps
    time_diffs = fut.index.to_series().diff()
    threshold = pd.Timedelta(minutes=gap_threshold_min)
    gap_mask = time_diffs > threshold
    gap_indices = fut.index[gap_mask]

    print(f"Found {len(gap_indices)} gaps (> {gap_threshold_min} min)")

    # Build synthetic bars for each gap
    synthetic_frames = []
    for gap_end_ts in gap_indices:
        # gap_start_ts = last real bar before the gap
        gap_pos = fut.index.get_loc(gap_end_ts)
        gap_start_ts = fut.index[gap_pos - 1]
        gap_duration = gap_end_ts - gap_start_ts

        # Find the futures close at the gap start
        fut_close = fut.loc[gap_start_ts, "close"]

        # Find the nearest spot bar at or before gap_start_ts for the ratio
        spot_before = spot.index[spot.index <= gap_start_ts]
        if len(spot_before) == 0:
            continue
        nearest_spot_ts = spot_before[-1]
        spot_close = spot.loc[nearest_spot_ts, "close"]

        if spot_close == 0:
            continue

        ratio = fut_close / spot_close

        # Select spot bars strictly inside the gap
        gap_spot = spot[(spot.index > gap_start_ts) & (spot.index < gap_end_ts)]

        if gap_spot.empty:
            continue

        # Create synthetic bars: flat fill (O=H=L=C = spot_close * ratio)
        synth = pd.DataFrame(index=gap_spot.index)
        synth["open"] = gap_spot["close"] * ratio
        synth["high"] = gap_spot["high"] * ratio
        synth["low"] = gap_spot["low"] * ratio
        synth["close"] = gap_spot["close"] * ratio
        synth["volume"] = 0.0
        synth["is_synthetic"] = 1

        is_weekend = gap_duration > pd.Timedelta(hours=4)

        if weekend_tradable and is_weekend:
            # Mark weekend bars as tradable (real OHLCV, real volume)
            # except during the daily 1-hour maintenance window.
            # Maintenance = 22:00-23:00 UTC each day (≈ 5-6 PM ET).
            synth["volume"] = gap_spot["volume"].values * ratio  # scale volume too
            bar_hours_utc = synth.index.hour
            in_maintenance = (bar_hours_utc == 22)
            synth.loc[~in_maintenance, "is_synthetic"] = 0
            # Maintenance bars stay is_synthetic=1, volume=0
            synth.loc[in_maintenance, "volume"] = 0.0

        synthetic_frames.append(synth)

        if is_weekend:
            tradable_count = (synth["is_synthetic"] == 0).sum()
            label = f"WEEKEND (tradable={tradable_count})" if weekend_tradable else "WEEKEND"
        else:
            label = "daily"
        print(f"  {label} gap: {gap_start_ts} → {gap_end_ts} "
              f"({gap_duration}), filled {len(synth)} bars, ratio={ratio:.6f}")

    # Combine
    if synthetic_frames:
        all_synthetic = pd.concat(synthetic_frames)
        combined = pd.concat([fut, all_synthetic])
    else:
        combined = fut

    combined = combined.sort_index()
    combined = combined[~combined.index.duplicated(keep="first")]

    # Verify no remaining gaps
    remaining_diffs = combined.index.to_series().diff()
    remaining_gaps = remaining_diffs[remaining_diffs > threshold]
    if len(remaining_gaps) > 0:
        print(f"\nWARNING: {len(remaining_gaps)} gaps remain after filling:")
        for ts, gap in remaining_gaps.items():
            print(f"  {ts - gap} → {ts} ({gap})")
    else:
        print(f"\nNo gaps remain (all ≤ {gap_threshold_min} min)")

    # Write output CSV
    out = pd.DataFrame()
    # Convert to epoch milliseconds
    out["open_time"] = (
        combined.index.tz_localize(None).astype("datetime64[ms]").astype("int64")
    )
    out["open"] = combined["open"].values
    out["high"] = combined["high"].values
    out["low"] = combined["low"].values
    out["close"] = combined["close"].values
    out["volume"] = combined["volume"].values
    out["is_synthetic"] = combined["is_synthetic"].astype(int).values

    out.to_csv(output_csv, index=False)

    real_count = (combined["is_synthetic"] == 0).sum()
    synth_count = (combined["is_synthetic"] == 1).sum()
    size_mb = Path(output_csv).stat().st_size / (1024 * 1024)
    print(f"\nOutput: {output_csv}")
    print(f"  Total: {len(combined)} bars ({real_count} real + {synth_count} synthetic)")
    print(f"  Size: {size_mb:.2f} MB")

    return combined


def main():
    parser = argparse.ArgumentParser(
        description="Fill CME futures gaps with spot BTC data"
    )
    parser.add_argument("--futures", required=True, help="Futures 5m CSV path")
    parser.add_argument("--spot", required=True, help="Spot BTC 5m CSV path")
    parser.add_argument("--output", required=True, help="Output CSV path")
    parser.add_argument(
        "--gap-threshold",
        type=int,
        default=10,
        help="Min gap in minutes to fill (default: 10)",
    )
    parser.add_argument(
        "--weekend-tradable",
        action="store_true",
        help="Mark weekend gap bars as tradable (is_synthetic=0) "
             "except during the daily 1-hour maintenance window. "
             "Simulates CME 24/7 trading.",
    )
    args = parser.parse_args()

    fill_gaps(
        args.futures, args.spot, args.output,
        args.gap_threshold, args.weekend_tradable,
    )


if __name__ == "__main__":
    main()
