"""Manages local data files: gap detection, backfill, live append, and 5m→1h resampling."""

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from exchange.base import ExchangeClient
from exchange.massive_rest import MassiveRestClient

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

# Wall-clock budgets so flaky exchanges can't stall startup / live loop.
STARTUP_BACKFILL_BUDGET_SEC = 60.0
LIVE_GAP_FILL_BUDGET_SEC = 20.0
# Skip exchange calls in fill_gap for this long after a call that didn't
# close the gap — prevents per-tick retry storms during outages.
GAP_FILL_COOLDOWN_SEC = 60.0


def feed_delay_minutes_from_config(config: dict) -> int:
    """Read ``feed_delay_minutes`` from ``exchange`` or ``bot`` / ``paper_trading``."""
    ex = config.get("exchange") or {}
    v = ex.get("feed_delay_minutes")
    if v is None:
        for key in ("bot", "paper_trading"):
            sec = config.get(key)
            if isinstance(sec, dict) and sec.get("feed_delay_minutes") is not None:
                v = sec.get("feed_delay_minutes")
                break
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


class DataManager:
    """Manages monthly CSV files for 5m and 1h OHLCV data.

    File naming: {symbol}-{interval}-{YYYY}-{MM}.csv

    ``feed_delay_minutes`` (see ``exchange.feed_delay_minutes`` in bot YAML)
    shifts only the **data horizon** used for bar selection, startup backfill,
    and ``fill_gap`` trailing end: effective time is wall clock minus that delay
    (for delayed SIP / aggregate plans). Month file paths and repair still use
    wall clock via ``now_fn``. If local CSVs already contain bars newer than
    that horizon, behavior is unchanged from the no-delay case (last_local vs
    last_closed_start).
    """

    def __init__(self, exchange: ExchangeClient, symbol: str, data_dir: str,
                 warm_up_hours: int = 250, now_fn=None, feed_delay_minutes: int = 0):
        self.exchange = exchange
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.warm_up_hours = warm_up_hours
        self.has_gap = False
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._feed_delay = timedelta(minutes=max(0, int(feed_delay_minutes)))
        self._log = logging.getLogger(f"{__name__}.{symbol}")
        # In-memory OHLCV cache. Kept in sync via _append_bar_to_*_cache on
        # live appends; invalidated by bulk backfills and fill_gap.
        self._df_5m_cache: pd.DataFrame | None = None
        self._df_1h_cache: pd.DataFrame | None = None
        # monotonic() timestamp of the last fill_gap exchange attempt (None = never).
        self._last_gap_fill_attempt: float | None = None
        if self._feed_delay.total_seconds() > 0:
            self._log.info(
                "feed_delay_minutes=%d: bar/backfill clock lags wall clock (delayed feed mode).",
                int(self._feed_delay.total_seconds() // 60),
            )

    def _feed_now(self) -> datetime:
        """Wall time minus feed delay — use for bar windows and backfill 'now' only."""
        return self._now_fn() - self._feed_delay

    # ── OHLCV cache accessors ────────────────────────────────────────

    def get_df_5m(self, *, refresh: bool = False) -> pd.DataFrame:
        """Return the cached 5m OHLCV frame, lazy-loading from disk once per cache miss."""
        if refresh or self._df_5m_cache is None:
            self._df_5m_cache = self._load_recent("5m")
        return self._df_5m_cache

    def get_df_1h(self, *, refresh: bool = False) -> pd.DataFrame:
        """Return the cached 1h OHLCV frame, lazy-loading from disk once per cache miss."""
        if refresh or self._df_1h_cache is None:
            self._df_1h_cache = self._load_recent("1h")
        return self._df_1h_cache

    def _invalidate_cache(self) -> None:
        self._df_5m_cache = None
        self._df_1h_cache = None

    def _append_bar_to_5m_cache(self, bar: pd.DataFrame) -> None:
        """Append one raw-schema 5m bar (as fetched) to the cached df_5m in place."""
        if self._df_5m_cache is None or bar is None or bar.empty:
            return
        row = bar.iloc[0]
        ts = pd.Timestamp(int(row["open_time"]), unit="ms")
        new_row = pd.DataFrame(
            {
                "open": [float(row["open"])],
                "high": [float(row["high"])],
                "low": [float(row["low"])],
                "close": [float(row["close"])],
                "volume": [float(row["volume"])],
            },
            index=pd.DatetimeIndex([ts], name="date"),
        )
        merged = pd.concat([self._df_5m_cache, new_row])
        if merged.index.duplicated().any():
            merged = merged[~merged.index.duplicated(keep="last")]
        self._df_5m_cache = merged.sort_index()

    def _append_bar_to_1h_cache(self, hourly_bar: pd.DataFrame) -> None:
        """Append a resampled 1h bar (DatetimeIndex, OHLCV columns) to the cached df_1h."""
        if self._df_1h_cache is None or hourly_bar is None or hourly_bar.empty:
            return
        new_row = hourly_bar[["open", "high", "low", "close", "volume"]].astype(float).copy()
        if new_row.index.tz is not None:
            new_row.index = new_row.index.tz_convert("UTC").tz_localize(None)
        merged = pd.concat([self._df_1h_cache, new_row])
        if merged.index.duplicated().any():
            merged = merged[~merged.index.duplicated(keep="last")]
        self._df_1h_cache = merged.sort_index()

    def _exchange_has_session_clock(self) -> bool:
        """US equities via Massive/Polygon: skip overnight/weekend in gap fill.

        Rely on concrete type — ``hasattr(..., "is_market_open")`` is not enough
        if the runtime client is wrapped or a different code path supplies the
        exchange instance.
        """
        return isinstance(self.exchange, MassiveRestClient)

    def _expects_5m_bar(self, bar_open_ms: int) -> bool:
        """True if a 5m bar at this open time is expected (crypto: always; US equity: extended hours)."""
        if not self._exchange_has_session_clock():
            return True
        return bool(self.exchange.is_market_open(bar_open_ms))

    def _utc_day_has_no_expected_equity_session(self, day_start_ms: int) -> bool:
        """True when no sampled instant in this UTC day falls in equity extended hours (skip API)."""
        if not self._exchange_has_session_clock():
            return False
        for m in range(0, 24 * 60, 30):
            t = day_start_ms + m * 60_000
            if t >= day_start_ms + ONE_DAY_MS:
                break
            if self.exchange.is_market_open(t):
                return False
        return True

    def _backfill_session_aware_gaps(
        self, gap_start_ms: int, next_bar_start_ms: int,
        *, deadline: float | None = None,
    ) -> int:
        """Backfill only 5m slots where the exchange expects bars. Returns nominal bar count filled."""
        total = 0
        run_start: int | None = None
        t = gap_start_ms
        while t < next_bar_start_ms:
            if deadline is not None and time.monotonic() >= deadline:
                break
            if self._expects_5m_bar(t):
                if run_start is None:
                    run_start = t
            else:
                if run_start is not None:
                    self._backfill(run_start, t - 1, deadline=deadline)
                    total += (t - run_start) // FIVE_MIN_MS
                    run_start = None
            t += FIVE_MIN_MS
        if run_start is not None:
            self._backfill(run_start, next_bar_start_ms - 1, deadline=deadline)
            total += (next_bar_start_ms - run_start) // FIVE_MIN_MS
        return total

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

    def _recover_ohlcv_frames(self, where: str) -> tuple[pd.DataFrame, pd.DataFrame]:
        """After an uncaught error in startup/fill_gap, return disk data or empty OHLCV."""
        try:
            df_5m = self.get_df_5m(refresh=True)
            df_1h = self._resample_all(df_5m)
            self._df_1h_cache = df_1h
            self._log.warning(
                "%s: using %d cached 5m bars for %s (exchange step aborted).",
                where,
                len(df_5m),
                self.symbol,
            )
            return df_5m, df_1h
        except Exception:
            self._log.exception(
                "%s: could not read local OHLCV for %s — empty warm-up.",
                where,
                self.symbol,
            )
            empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            return empty, empty

    def startup(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Run startup sequence: repair tail, fill the trailing gap, load data.

        Only the range **last CSV bar → feed_now** is fetched from the
        exchange — there is **no** mid-CSV hole scanning. If local data is
        older than ``warm_up_hours``, the fetch window is clamped to the
        warm-up horizon so startup can't run unbounded.

        The exchange backfill is additionally capped by a wall-clock budget
        (``STARTUP_BACKFILL_BUDGET_SEC``); if the budget is exhausted, the
        bot proceeds with whatever was fetched plus existing local data.

        Never raises: on unexpected failure logs full traceback and returns
        cached or empty OHLCV so the bot can finish startup.
        """
        self._repair_recent_files()
        self._invalidate_cache()

        try:
            now_ms = int(self._feed_now().timestamp() * 1000)
            last_closed_ms = (now_ms // FIVE_MIN_MS) * FIVE_MIN_MS
            warm_up_ms = self.warm_up_hours * ONE_HOUR_MS
            warm_up_floor = now_ms - warm_up_ms

            last_5m = self._get_last_timestamp("5m")
            if last_5m is None:
                start_ms = warm_up_floor
                self._log.info(
                    "No local data found. Warm-up backfill of %d hour(s).",
                    self.warm_up_hours,
                )
            else:
                # Only fill from last CSV row → now, clamped to the warm-up window
                # so an ancient CSV can't kick off a multi-week backfill.
                start_ms = max(last_5m + FIVE_MIN_MS, warm_up_floor)
                if last_5m + FIVE_MIN_MS < warm_up_floor:
                    self._log.info(
                        "Local data too old (last: %s). Clamping backfill to warm-up window.",
                        datetime.fromtimestamp(last_5m / 1000, tz=timezone.utc),
                    )
                elif start_ms < last_closed_ms - FIVE_MIN_MS:
                    gap_hours = (last_closed_ms - start_ms) / ONE_HOUR_MS
                    self._log.info("Trailing gap %.1fh from last CSV bar — backfilling.", gap_hours)

            if start_ms < last_closed_ms:
                deadline = time.monotonic() + STARTUP_BACKFILL_BUDGET_SEC
                self._backfill(start_ms, last_closed_ms - 1, deadline=deadline)

            df_5m = self.get_df_5m(refresh=True)
            df_1h = self._resample_all(df_5m)
            self._df_1h_cache = df_1h

            self._log.info(
                "Data ready: %d 5m bars, %d 1h bars, latest: %s",
                len(df_5m),
                len(df_1h),
                df_5m.index[-1] if len(df_5m) > 0 else "none",
            )
            return df_5m, df_1h
        except Exception:
            self._log.exception(
                "startup: uncaught error for %s — falling back to disk or empty OHLCV.",
                self.symbol,
            )
            return self._recover_ohlcv_frames("startup")

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

    def _backfill(self, start_ms: int, end_ms: int, *, deadline: float | None = None):
        """Fetch historical data one day at a time and store to monthly files.

        API errors are logged and skipped so that a maintenance window or
        transient outage does not prevent startup — the bot proceeds with
        whatever local data it already has.

        For US equity exchanges with ``is_market_open``, entire UTC days that
        never overlap extended hours are skipped (no futile weekend/overnight
        fetches).

        ``deadline`` is an absolute ``time.monotonic()`` timestamp. When set,
        the loop stops early once reached so flaky APIs can't stall the bot.
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
        cache_dirty = False

        while day_start < end_ms:
            if deadline is not None and time.monotonic() >= deadline:
                self._log.warning(
                    "_backfill: wall-clock budget exhausted after %d/%d days — stopping.",
                    fetched_days, total_days,
                )
                break

            day_end = day_start + ONE_DAY_MS - 1
            dt = datetime.fromtimestamp(day_start / 1000, tz=timezone.utc)

            utc_midnight = (day_start // ONE_DAY_MS) * ONE_DAY_MS
            if (
                day_start == utc_midnight
                and self._utc_day_has_no_expected_equity_session(day_start)
            ):
                day_start += ONE_DAY_MS
                continue

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
                    cache_dirty = True

            fetched_days += 1
            if total_days > 1 and (fetched_days % 5 == 0 or fetched_days == total_days):
                self._log.info("Backfill progress: %d/%d days", fetched_days, total_days)

            day_start += ONE_DAY_MS

        if failed_days:
            self._log.warning(
                "Backfill complete with %d day(s) skipped due to exchange errors. "
                "Bot will run on available cached data.",
                failed_days,
            )

        # Bulk writes bypass the incremental cache updater — force a reload.
        if cache_dirty:
            self._invalidate_cache()

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

        Uses :meth:`_feed_now` so delayed-feed plans request only bars inside the
        licensed window. Sleep/retry timing in the trader loop stays on wall clock.

        If multiple bars were missed (e.g. after a slow API retry during a
        network outage), sets has_gap so the caller triggers a full backfill
        and indicator recalculation via fill_gap().

        Returns the new bar as a single-row DataFrame, or None if no new bar.
        """
        now_ms = int(self._feed_now().timestamp() * 1000)
        last_closed_start = ((now_ms // FIVE_MIN_MS) - 1) * FIVE_MIN_MS

        last_local = self._get_last_timestamp("5m")
        if last_local is not None and last_local >= last_closed_start:
            return None

        # Skip fetch if the exchange has market hours and is currently closed
        if hasattr(self.exchange, 'is_market_open') and not self.exchange.is_market_open(last_closed_start):
            return None

        # Detect gap: if we're more than one **session** bar behind, immediately
        # backfill the missing range so the latest-bar append below does not
        # leave a permanent hole in the middle of the CSV/cache. ``has_gap`` is
        # left set so the caller still triggers indicator recalculation via
        # fill_gap (which will be a fast no-op for the trailing range).
        if last_local is not None:
            exp_missed = 0
            t = last_local + FIVE_MIN_MS
            while t < last_closed_start:
                if self._expects_5m_bar(t):
                    exp_missed += 1
                t += FIVE_MIN_MS
            if exp_missed > 0:
                self.has_gap = True
                if exp_missed >= 2:
                    self._log.warning("%d bars behind — backfilling gap.", exp_missed)
                deadline = time.monotonic() + LIVE_GAP_FILL_BUDGET_SEC
                if self._exchange_has_session_clock():
                    self._backfill_session_aware_gaps(
                        last_local + FIVE_MIN_MS, last_closed_start, deadline=deadline,
                    )
                else:
                    self._backfill(
                        last_local + FIVE_MIN_MS, last_closed_start - 1, deadline=deadline,
                    )

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
        self._append_bar_to_5m_cache(bar)
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

        Returns a single-row 1h DataFrame, or None if the hour lacks sufficient
        liquidity.

        Qualification rule:
          - >= 9 bars in the hour → always qualify
          - < 9 bars → qualify only if sum(close * volume) >= $1,000,000
          - 0 bars → always reject
        """
        if df_5m.empty:
            return None

        last_hour = df_5m.index[-1].floor("h")
        hour_bars = df_5m[(df_5m.index >= last_hour) & (df_5m.index < last_hour + pd.Timedelta(hours=1))]

        n = len(hour_bars)
        if n == 0:
            return None
        if n < 9:
            dollar_vol = (hour_bars["close"] * hour_bars["volume"]).sum()
            if dollar_vol < 1_000_000:
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
            self._append_bar_to_1h_cache(hourly_bar)
            self._log.debug(
                "Appended 1h bar: %s O=%.0f H=%.0f L=%.0f C=%.0f V=%.1f",
                dt.strftime("%Y-%m-%d %H:%M"),
                row["open"], row["high"], row["low"], row["close"], row["volume"],
            )

    def fill_gap(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Backfill the trailing gap (last CSV bar → feed_now) and return refreshed frames.

        Called after ``fetch_and_append_5m`` flags ``has_gap`` because one or
        more session slots were missed. Only the trailing range is fetched;
        there is no mid-CSV hole scanning.

        Cooldown: to avoid hammering a flaky exchange, successive calls within
        ``GAP_FILL_COOLDOWN_SEC`` of the last attempt return cached frames
        **without** touching the exchange and **leave ``has_gap`` set** so the
        caller can tell a real fill from a cooldown skip (e.g. to avoid
        unnecessary strategy re-warm-up).

        Wall-clock budget: the underlying ``_backfill`` is bounded by
        ``LIVE_GAP_FILL_BUDGET_SEC``.

        Never raises: on unexpected error returns cached or empty OHLCV so
        live loops do not stall.
        """
        now_mono = time.monotonic()
        if (
            self._last_gap_fill_attempt is not None
            and now_mono - self._last_gap_fill_attempt < GAP_FILL_COOLDOWN_SEC
        ):
            remaining = GAP_FILL_COOLDOWN_SEC - (now_mono - self._last_gap_fill_attempt)
            self._log.debug(
                "fill_gap: in cooldown (%.0fs left), skipping exchange call.", remaining,
            )
            return self.get_df_5m(), self.get_df_1h()

        self._last_gap_fill_attempt = now_mono

        try:
            now_ms = int(self._feed_now().timestamp() * 1000)
            last_closed_ms = (now_ms // FIVE_MIN_MS) * FIVE_MIN_MS
            deadline = time.monotonic() + LIVE_GAP_FILL_BUDGET_SEC

            df_5m = self.get_df_5m()

            if df_5m.empty:
                start_ms = now_ms - self.warm_up_hours * ONE_HOUR_MS
                self._log.info("fill_gap: no local data, backfilling %d hours.", self.warm_up_hours)
                self._backfill(start_ms, last_closed_ms - 1, deadline=deadline)
                df_5m = self.get_df_5m(refresh=True)
                df_1h = self._resample_all(df_5m)
                self._df_1h_cache = df_1h
                self.has_gap = False
                return df_5m, df_1h

            # Trailing gap only (between last bar in cached data and now)
            last_5m = int(df_5m.index[-1].to_numpy().astype("datetime64[ms]").astype("int64"))
            trailing_start = last_5m + FIVE_MIN_MS
            trailing_bars = (last_closed_ms - trailing_start) // FIVE_MIN_MS
            if trailing_bars > 0:
                if self._exchange_has_session_clock():
                    self._backfill_session_aware_gaps(
                        trailing_start, last_closed_ms, deadline=deadline,
                    )
                else:
                    self._backfill(trailing_start, last_closed_ms - 1, deadline=deadline)

            df_5m = self.get_df_5m(refresh=True)
            df_1h = self._resample_all(df_5m)
            self._df_1h_cache = df_1h

            self.has_gap = False
            return df_5m, df_1h
        except Exception:
            self._log.exception(
                "fill_gap: uncaught error for %s — clearing has_gap and using disk or empty OHLCV.",
                self.symbol,
            )
            self.has_gap = False
            return self._recover_ohlcv_frames("fill_gap")
