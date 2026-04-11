"""Manages local data files: gap detection, backfill, live append, and 5m→1h resampling."""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from exchange.base import ExchangeClient

logger = logging.getLogger(__name__)

CSV_HEADER = (
    "open_time,open,high,low,close,volume,close_time,"
    "quote_asset_volume,number_of_trades,taker_buy_base_volume,"
    "taker_buy_quote_volume,ignore"
)

FIVE_MIN_MS = 5 * 60 * 1000
ONE_HOUR_MS = 60 * 60 * 1000
ONE_DAY_MS = 24 * ONE_HOUR_MS
BARS_PER_DAY_5M = 288


class DataManager:
    """Manages monthly CSV files for 5m and 1h OHLCV data.

    File naming: {symbol}-{interval}-{YYYY}-{MM}.csv
    """

    def __init__(self, exchange: ExchangeClient, symbol: str, data_dir: str,
                 warm_up_hours: int = 250, now_fn=None):
        self.exchange = exchange
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.warm_up_hours = warm_up_hours
        self.has_gap = False
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._log = logging.getLogger(f"{__name__}.{symbol}")

    def _monthly_path(self, interval: str, year: int, month: int) -> Path:
        return self.data_dir / f"{self.symbol}-{interval}-{year:04d}-{month:02d}.csv"

    def _months_in_range(self, start_ms: int, end_ms: int) -> list[tuple[int, int]]:
        """Return list of (year, month) tuples covering the time range."""
        start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
        months = []
        y, m = start_dt.year, start_dt.month
        while (y, m) <= (end_dt.year, end_dt.month):
            months.append((y, m))
            m += 1
            if m > 12:
                m = 1
                y += 1
        return months

    def _read_csv(self, path: Path) -> pd.DataFrame:
        """Read a monthly CSV, returning empty DataFrame if file doesn't exist."""
        if not path.exists():
            return pd.DataFrame()
        try:
            df = pd.read_csv(path)
            if df.empty:
                return df
            df["open_time"] = df["open_time"].astype(int)
            self._validate_csv(df, path)
            return df
        except Exception as e:
            self._log.error("Failed to read %s: %s", path, e)
            raise

    def _validate_csv(self, df: pd.DataFrame, path: Path):
        """Check monotonic timestamps and data integrity."""
        if "open_time" not in df.columns:
            raise ValueError(f"{path}: missing 'open_time' column")
        times = df["open_time"]
        if not times.is_monotonic_increasing:
            first_bad = times[times.diff() <= 0].index[0]
            raise ValueError(
                f"{path}: non-monotonic open_time at row {first_bad} "
                f"(value {times.iloc[first_bad]})"
            )

    def _append_rows(self, path: Path, df: pd.DataFrame):
        """Append rows to a CSV file, deduplicating and sorting by open_time."""
        if path.exists() and path.stat().st_size > 0:
            try:
                existing = pd.read_csv(path)
                combined = pd.concat([existing, df], ignore_index=True)
                combined["open_time"] = combined["open_time"].astype(int)
                combined = combined.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
                combined.to_csv(path, index=False)
                return
            except Exception as e:
                self._log.warning("Could not merge-append to %s: %s — falling back to raw append", path, e)
        df.to_csv(path, mode="a", header=True, index=False)

    def _get_last_timestamp(self, interval: str) -> int | None:
        """Read the last open_time from the most recent monthly file for this interval."""
        now = self._now_fn()
        for month_offset in range(0, 13):
            m = now.month - month_offset
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            path = self._monthly_path(interval, y, m)
            if path.exists() and path.stat().st_size > 0:
                try:
                    df = pd.read_csv(path)
                    if not df.empty:
                        return int(df["open_time"].iloc[-1])
                except Exception:
                    continue
        return None

    def _repair_tail(self, path: Path) -> bool:
        """Attempt to repair a truncated last line. Returns True if repair was needed."""
        if not path.exists():
            return False
        with open(path, "rb") as f:
            content = f.read()
        if not content:
            return False
        if not content.endswith(b"\n"):
            last_newline = content.rfind(b"\n")
            if last_newline == -1:
                return False
            truncated = content[last_newline + 1:]
            self._log.warning(
                "Truncated tail in %s (%d bytes), repairing", path, len(truncated)
            )
            with open(path, "wb") as f:
                f.write(content[:last_newline + 1])
            return True
        return False

    def startup(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Run full startup sequence: repair, gap-fill, load data.

        Returns:
            (df_5m, df_1h) — DataFrames with all loaded data for strategy warm-up.
        """
        now_ms = int(self._now_fn().timestamp() * 1000)

        self._repair_recent_files()

        last_5m = self._get_last_timestamp("5m")
        warm_up_ms = self.warm_up_hours * ONE_HOUR_MS

        if last_5m is None:
            start_ms = now_ms - warm_up_ms
            self._log.info(
                "No local data found. Backfilling %d hours from exchange.",
                self.warm_up_hours,
            )
        else:
            needed_start = now_ms - warm_up_ms
            if last_5m < needed_start:
                start_ms = needed_start
                self._log.info(
                    "Local data too old (last: %s). Backfilling from %s.",
                    datetime.fromtimestamp(last_5m / 1000, tz=timezone.utc),
                    datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
                )
            else:
                start_ms = last_5m + FIVE_MIN_MS
                if start_ms < now_ms - FIVE_MIN_MS:
                    gap_hours = (now_ms - start_ms) / ONE_HOUR_MS
                    self._log.warning(
                        "Data gap detected: %.1f hours missing. Backfilling.",
                        gap_hours,
                    )

        last_closed_ms = (now_ms // FIVE_MIN_MS) * FIVE_MIN_MS
        if last_5m is None or start_ms < last_closed_ms:
            self._backfill(start_ms, last_closed_ms - 1)

        df_5m = self._load_recent("5m")

        # Scan for and fill any internal gaps in the data (e.g. from a
        # previous outage where the old fill_gap missed interior holes).
        filled = self._fill_internal_gaps(df_5m)
        if filled > 0:
            df_5m = self._load_recent("5m")

        df_1h = self._resample_all(df_5m)

        self._log.info(
            "Data ready: %d 5m bars, %d 1h bars, latest: %s",
            len(df_5m),
            len(df_1h),
            df_5m.index[-1] if len(df_5m) > 0 else "none",
        )
        return df_5m, df_1h

    def _repair_recent_files(self):
        """Check the current and previous month files for tail corruption."""
        now = self._now_fn()
        for offset in range(2):
            m = now.month - offset
            y = now.year
            if m <= 0:
                m += 12
                y -= 1
            for interval in ("5m", "1h"):
                path = self._monthly_path(interval, y, m)
                self._repair_tail(path)

    def _backfill(self, start_ms: int, end_ms: int):
        """Fetch historical data one day at a time and store to monthly files.

        API errors are logged and skipped so that a maintenance window or
        transient outage does not prevent startup — the bot proceeds with
        whatever local data it already has.
        """
        # For short gaps (< 1 day), start from the exact gap start rather
        # than rounding down to midnight — avoids re-fetching a full day.
        span = end_ms - start_ms
        if span < ONE_DAY_MS:
            day_start = start_ms
        else:
            day_start = (start_ms // ONE_DAY_MS) * ONE_DAY_MS
        total_days = (end_ms - day_start) // ONE_DAY_MS + 1
        fetched_days = 0
        failed_days = 0

        while day_start < end_ms:
            day_end = day_start + ONE_DAY_MS - 1
            dt = datetime.fromtimestamp(day_start / 1000, tz=timezone.utc)

            try:
                df = self.exchange.fetch_ohlcv(
                    self.symbol, "5m",
                    start_time_ms=day_start,
                    end_time_ms=min(day_end, end_ms),
                    limit=BARS_PER_DAY_5M,
                )
            except Exception as exc:
                failed_days += 1
                self._log.warning(
                    "Backfill: exchange unavailable for %s — skipping day (will use cached data). Error: %s",
                    dt.strftime("%Y-%m-%d"), exc,
                )
                day_start += ONE_DAY_MS
                continue

            if not df.empty:
                df = self._deduplicate(df, "5m", dt.year, dt.month)
                if not df.empty:
                    self._append_rows(
                        self._monthly_path("5m", dt.year, dt.month), df
                    )

            fetched_days += 1
            if fetched_days % 5 == 0 or fetched_days == total_days:
                self._log.info("Backfill progress: %d/%d days", fetched_days, total_days)

            day_start += ONE_DAY_MS

        if failed_days:
            self._log.warning(
                "Backfill complete with %d day(s) skipped due to exchange errors. "
                "Bot will run on available cached data.",
                failed_days,
            )

    def _deduplicate(self, new_df: pd.DataFrame, interval: str,
                     year: int, month: int) -> pd.DataFrame:
        """Remove rows from new_df whose open_time already exists in the monthly file."""
        path = self._monthly_path(interval, year, month)
        if not path.exists() or path.stat().st_size == 0:
            return new_df
        try:
            existing = pd.read_csv(path)
            existing_times = set(existing["open_time"].astype(int))
            mask = ~new_df["open_time"].isin(existing_times)
            return new_df[mask]
        except Exception:
            return new_df

    def _load_recent(self, interval: str) -> pd.DataFrame:
        """Load enough monthly files to cover the warmup period."""
        now = self._now_fn()
        # How many months back do we need? At least warm_up_hours, plus current partial month.
        months_needed = max(2, (self.warm_up_hours // (24 * 30)) + 2)
        frames = []

        for offset in range(months_needed - 1, -1, -1):
            m = now.month - offset
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            path = self._monthly_path(interval, y, m)
            df = self._read_csv(path)
            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["open_time"], keep="last")
        combined = combined.sort_values("open_time")

        from data_sources.parsers.binance import _MICROSECOND_THRESHOLD
        timestamps = combined["open_time"].astype(float)
        ms_timestamps = timestamps.where(
            timestamps < _MICROSECOND_THRESHOLD,
            timestamps / 1000,
        )
        combined["date"] = pd.to_datetime(ms_timestamps, unit="ms", utc=True)
        combined["date"] = combined["date"].dt.tz_localize(None)
        combined = combined.set_index("date")

        for col in ["open", "high", "low", "close"]:
            combined[col] = combined[col].astype(float)
        combined["volume"] = combined["volume"].astype(float)

        return combined[["open", "high", "low", "close", "volume"]]

    def _resample_all(self, df_5m: pd.DataFrame) -> pd.DataFrame:
        """Resample 5m data to 1h, matching the backtester's resample_ohlcv()."""
        if df_5m.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        resampled = df_5m.resample("1h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()
        return resampled

    def fetch_and_append_5m(self) -> pd.DataFrame | None:
        """Fetch the latest closed 5m bar and append to the monthly file.

        If multiple bars were missed (e.g. after a slow API retry during a
        network outage), sets has_gap so the caller triggers a full backfill
        and indicator recalculation via fill_gap().

        Returns the new bar as a single-row DataFrame, or None if no new bar.
        """
        now_ms = int(self._now_fn().timestamp() * 1000)
        last_closed_start = ((now_ms // FIVE_MIN_MS) - 1) * FIVE_MIN_MS

        last_local = self._get_last_timestamp("5m")
        if last_local is not None and last_local >= last_closed_start:
            return None

        # Detect gap: if we're more than one bar behind, flag it so the
        # caller (swing_bot._on_new_5m_bar) triggers fill_gap() which
        # backfills all missing bars, reloads data, and recalculates indicators.
        if last_local is not None:
            missed_bars = (last_closed_start - last_local) // FIVE_MIN_MS - 1
            if missed_bars > 0:
                gap_minutes = missed_bars * 5
                self._log.warning(
                    "Gap detected: %d bars (%d min) behind — "
                    "will backfill after fetching latest bar.",
                    missed_bars, gap_minutes,
                )
                self.has_gap = True

        try:
            df = self.exchange.fetch_ohlcv(
                self.symbol, "5m",
                start_time_ms=last_closed_start,
                end_time_ms=last_closed_start + FIVE_MIN_MS - 1,
                limit=1,
            )
        except Exception as exc:
            self.has_gap = True
            self._log.warning(
                "Exchange unavailable — skipping bar fetch, holding current position. Error: %s", exc,
            )
            return None

        if df.empty:
            return None

        bar = df.iloc[[0]]
        dt = datetime.fromtimestamp(last_closed_start / 1000, tz=timezone.utc)
        bar = self._deduplicate(bar, "5m", dt.year, dt.month)

        if bar.empty:
            return None

        self._append_rows(self._monthly_path("5m", dt.year, dt.month), bar)
        self._log.debug(
            "Appended 5m bar: %s close=%.2f",
            dt.strftime("%Y-%m-%d %H:%M"), float(df.iloc[0]["close"]),
        )
        return bar

    def is_hour_boundary(self, bar_5m: pd.DataFrame) -> bool:
        """Check if this 5m bar completes an hourly boundary (XX:55 bar)."""
        open_time = int(bar_5m.iloc[0]["open_time"])
        bar_minute = (open_time // (60 * 1000)) % 60
        return bar_minute == 55

    def resample_latest_hour(self, df_5m: pd.DataFrame) -> pd.DataFrame | None:
        """Resample the most recent complete hour from 5m data.

        Returns a single-row 1h DataFrame, or None if incomplete.
        """
        if df_5m.empty:
            return None

        last_hour = df_5m.index[-1].floor("h")
        hour_bars = df_5m[(df_5m.index >= last_hour) & (df_5m.index < last_hour + pd.Timedelta(hours=1))]

        if len(hour_bars) < 12:
            return None

        hourly = hour_bars.resample("1h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna()

        return hourly if not hourly.empty else None

    def append_1h(self, hourly_bar: pd.DataFrame):
        """Append a resampled 1h bar to the monthly 1h file."""
        if hourly_bar is None or hourly_bar.empty:
            return

        dt = hourly_bar.index[0].to_pydatetime()
        open_time_ms = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        close_time_ms = open_time_ms + ONE_HOUR_MS - 1

        row = hourly_bar.iloc[0]
        out = pd.DataFrame([{
            "open_time": open_time_ms,
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "close_time": close_time_ms,
            "quote_asset_volume": 0,
            "number_of_trades": 0,
            "taker_buy_base_volume": 0,
            "taker_buy_quote_volume": 0,
            "ignore": 0,
        }])

        year, month = dt.year, dt.month
        out = self._deduplicate(out, "1h", year, month)
        if not out.empty:
            self._append_rows(self._monthly_path("1h", year, month), out)
            self._log.debug(
                "Appended 1h bar: %s O=%.0f H=%.0f L=%.0f C=%.0f V=%.1f",
                dt.strftime("%Y-%m-%d %H:%M"),
                row["open"], row["high"], row["low"], row["close"], row["volume"],
            )

    def _fill_internal_gaps(self, df_5m: pd.DataFrame) -> int:
        """Scan loaded 5m data for internal gaps and backfill each one.

        df_5m has a DatetimeIndex (UTC-naive) from _load_recent(). We derive
        millisecond timestamps from the index to find gaps.

        Returns the total number of bars backfilled (0 if no gaps found).
        """
        if df_5m.empty or len(df_5m) < 2:
            return 0

        # Convert DatetimeIndex → epoch-milliseconds for gap arithmetic.
        ts_ms = df_5m.index.to_numpy().astype("datetime64[ms]").astype("int64")
        total_filled = 0

        for i in range(1, len(ts_ms)):
            gap_ms = ts_ms[i] - ts_ms[i - 1]
            if gap_ms <= FIVE_MIN_MS:
                continue
            gap_start_ms = ts_ms[i - 1] + FIVE_MIN_MS
            gap_end_ms = ts_ms[i] - 1
            gap_bars = (gap_end_ms - gap_start_ms + 1) // FIVE_MIN_MS
            gap_minutes = gap_bars * 5
            self._log.info(
                "Filling internal gap: %d bars (%d min) at %s.",
                gap_bars, gap_minutes,
                datetime.fromtimestamp(gap_start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            )
            self._backfill(gap_start_ms, gap_end_ms)
            total_filled += gap_bars

        return total_filled

    def fill_gap(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Backfill any gap between local data and now, then reload all data.

        Called after exchange connectivity resumes following an outage.
        Returns refreshed (df_5m, df_1h) with the gap filled and indicators
        ready for recalculation.

        NOTE: fetch_and_append_5m() appends the *latest* bar before this is
        called, so _get_last_timestamp() would return that bar and see no gap.
        Instead we scan the loaded data for internal gaps and backfill each one.
        """
        now_ms = int(self._now_fn().timestamp() * 1000)
        last_closed_ms = (now_ms // FIVE_MIN_MS) * FIVE_MIN_MS

        # Load current data to find internal gaps
        df_5m = self._load_recent("5m")

        if df_5m.empty:
            start_ms = now_ms - self.warm_up_hours * ONE_HOUR_MS
            self._log.info("fill_gap: no local data, backfilling %d hours.", self.warm_up_hours)
            self._backfill(start_ms, last_closed_ms - 1)
            df_5m = self._load_recent("5m")
            df_1h = self._resample_all(df_5m)
            self.has_gap = False
            return df_5m, df_1h

        total_filled = self._fill_internal_gaps(df_5m)

        # Also fill any trailing gap (between last bar and now)
        last_5m = int(df_5m.index[-1].to_numpy().astype("datetime64[ms]").astype("int64"))
        trailing_start = last_5m + FIVE_MIN_MS
        trailing_bars = (last_closed_ms - trailing_start) // FIVE_MIN_MS
        if trailing_bars > 0:
            self._log.info(
                "fill_gap: trailing gap of %d bars, backfilling.", trailing_bars,
            )
            self._backfill(trailing_start, last_closed_ms - 1)
            total_filled += trailing_bars

        if total_filled == 0:
            self._log.info("fill_gap: no gaps to fill.")
        else:
            self._log.info("fill_gap: backfilled %d total bars.", total_filled)

        df_5m = self._load_recent("5m")
        df_1h = self._resample_all(df_5m)

        self.has_gap = False
        self._log.info(
            "fill_gap complete: %d 5m bars, %d 1h bars loaded.",
            len(df_5m), len(df_1h),
        )
        return df_5m, df_1h
