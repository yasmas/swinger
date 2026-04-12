"""Pure helpers for weekly Nasdaq scoring simulation (no I/O, no network)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

import numpy as np
import pandas as pd

ATR_PERIOD = 14
BB_PERIOD = 20
BB_STD_MULT = 2.0

# Shock: 10 disjoint 5-day volume blocks before W; range_expansion: mean daily ATR over ~14 weeks.
SHOCK_PRIOR_WEEKS = 10
RANGE_EXPANSION_ATR_LOOKBACK_DAYS = 14 * 5  # 70 trading days before W
# First index where Wilder ATR(14) is finite is 13; need ATR valid through the lookback window.
RANGE_EXPANSION_MIN_START_POS = RANGE_EXPANSION_ATR_LOOKBACK_DAYS + (ATR_PERIOD - 1)

# All valid `--scoring` values for `run_weekly_screener.py` (includes two-phase ATR methods).
SCORING_METHOD_CHOICES: tuple[str, ...] = tuple(
    sorted(
        list(
            {
                "bb_pctb",
                "momentum",
                "relative_volume",
                "atr_roc5",
                "atr_vwap_dev",
                "roc_acceleration",
                "range_expansion",
                "shock_vol_roc",
            }
        )
    )
)


@dataclass(frozen=True)
class WeekWindow:
    """One scoring week W and the following simulation week W+1.

    Both are **US Mon–Fri** calendar weeks (five session dates each). ``start_w`` is Monday of W,
    ``end_w`` is Friday of W; ``w1_dates`` is Mon–Fri of the **next** calendar week.
    """

    start_w: str
    end_w: str
    w_dates: tuple[str, ...]
    prior_21_dates: tuple[str, ...]
    w1_dates: tuple[str, ...]

    @property
    def w1_start(self) -> str:
        return self.w1_dates[0]

    @property
    def w1_end(self) -> str:
        return self.w1_dates[-1]


def load_daily_frames(daily_dir) -> dict[str, pd.DataFrame]:
    """Load ``{SYM}.csv`` with columns date, OHLCV; index by normalized date."""
    from pathlib import Path

    out: dict[str, pd.DataFrame] = {}
    root = Path(daily_dir)
    for path in sorted(root.glob("*.csv")):
        sym = path.stem.upper()
        df = pd.read_csv(path)
        if df.empty or "date" not in df.columns:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date").sort_index()
        for c in ("open", "high", "low", "close", "volume"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        out[sym] = df
    return out


def master_trading_days(symbol_frames: dict[str, pd.DataFrame]) -> list[pd.Timestamp]:
    """Sorted union of all dates present in any symbol file."""
    s: set[pd.Timestamp] = set()
    for df in symbol_frames.values():
        s.update(df.index)
    return sorted(s)


def _calendar_week_mon_fri_sessions(
    date_index: dict[pd.Timestamp, int],
    monday: pd.Timestamp,
) -> list[pd.Timestamp] | None:
    """Return Mon–Fri of the ISO week starting ``monday``, or None if any day is missing from data.

    Skips US-market **holiday** weeks where one of Mon–Fri is not in the master calendar (no
    synthetic fill).
    """
    m = pd.Timestamp(monday).normalize()
    if int(m.weekday()) != 0:
        return None
    week: list[pd.Timestamp] = []
    for k in range(5):
        d = m + pd.Timedelta(days=k)
        d = d.normalize()
        if d not in date_index:
            return None
        if int(d.weekday()) >= 5:
            return None
        week.append(d)
    return week


def enumerate_week_windows(
    dates: list[pd.Timestamp],
    *,
    min_prior_trading_days: int = 21,
    min_leading_index: int | None = None,
) -> list[WeekWindow]:
    """Valid (W, W+1) pairs on **calendar US-equity weeks** (Mon–Fri).

    Each of W and W+1 is exactly five **calendar** weekdays Mon–Fri that all appear in the merged
    master calendar (holidays omitted → that week is skipped). W+1 is the **following** calendar
    week (next Monday + 4 days).

    ``prior_21_dates`` are the ``min_prior_trading_days`` sessions strictly before W's Monday.

    ``min_leading_index`` is the minimum **master index** of W's **Monday** (same meaning as
    before: extra history for shock / range_expansion, etc.). Must be ``>= min_prior_trading_days``.
    """
    prior_days = min_prior_trading_days
    start_i = max(prior_days, min_leading_index if min_leading_index is not None else prior_days)
    dnorm = sorted({pd.Timestamp(x).normalize() for x in dates})
    if len(dnorm) < prior_days + 10:
        return []
    date_index = {d: i for i, d in enumerate(dnorm)}
    out: list[WeekWindow] = []
    for d in dnorm:
        if int(d.weekday()) != 0:
            continue
        mon_idx = date_index[d]
        if mon_idx < start_i:
            continue
        w_sess = _calendar_week_mon_fri_sessions(date_index, d)
        if w_sess is None:
            continue
        mon_next = d + pd.Timedelta(days=7)
        w1_sess = _calendar_week_mon_fri_sessions(date_index, mon_next)
        if w1_sess is None:
            continue
        prior = tuple(str(x.date()) for x in dnorm[mon_idx - prior_days : mon_idx])
        if len(prior) != prior_days:
            continue
        w = tuple(str(x.date()) for x in w_sess)
        w1 = tuple(str(x.date()) for x in w1_sess)
        out.append(
            WeekWindow(
                start_w=w[0],
                end_w=w[-1],
                w_dates=w,
                prior_21_dates=prior,
                w1_dates=w1,
            )
        )
    return out


def sample_evenly_spaced_indices(n_valid: int, n_pick: int) -> list[int]:
    """Evenly spaced indices in [0, n_valid-1], deduped (may be fewer than n_pick)."""
    if n_valid <= 0 or n_pick <= 0:
        return []
    if n_pick >= n_valid:
        return list(range(n_valid))
    idx = np.linspace(0, n_valid - 1, n_pick)
    return sorted(set(int(round(x)) for x in idx))


def score_momentum(
    df: pd.DataFrame,
    w_dates: tuple[str, ...],
) -> float | None:
    """Absolute weekly return over W: ``|close(end_W)/close(start_W) - 1|`` (fraction, not %)."""
    idx = pd.to_datetime(list(w_dates)).normalize()
    try:
        lo = df.index.get_indexer([idx[0]], method=None)[0]
        hi = df.index.get_indexer([idx[-1]], method=None)[0]
        if lo < 0 or hi < 0:
            return None
        c0 = float(df.iloc[lo]["close"])
        c1 = float(df.iloc[hi]["close"])
        if c0 <= 0 or np.isnan(c0) or np.isnan(c1):
            return None
        return abs(c1 / c0 - 1.0)
    except Exception:
        return None


def score_relative_volume(
    df: pd.DataFrame,
    w_dates: tuple[str, ...],
    prior_21_dates: tuple[str, ...],
) -> float | None:
    """mean(vol W) / mean(vol prior 21 sessions)."""
    idx_w = pd.to_datetime(list(w_dates)).normalize()
    idx_p = pd.to_datetime(list(prior_21_dates)).normalize()
    try:
        vol_w = []
        for d in idx_w:
            j = df.index.get_indexer([d], method=None)[0]
            if j < 0:
                return None
            v = float(df.iloc[j]["volume"])
            if np.isnan(v) or v < 0:
                return None
            vol_w.append(v)
        vol_p = []
        for d in idx_p:
            j = df.index.get_indexer([d], method=None)[0]
            if j < 0:
                return None
            v = float(df.iloc[j]["volume"])
            if np.isnan(v) or v < 0:
                return None
            vol_p.append(v)
        m_w = float(np.mean(vol_w))
        m_p = float(np.mean(vol_p))
        if m_p <= 0 or m_w <= 0:
            return None
        return m_w / m_p
    except Exception:
        return None


def score_bb_pctb(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """Bollinger %B at ``end_w``: (close − lower) / (upper − lower) from BB(20, 2σ) on daily closes.

    Uses the last ``BB_PERIOD`` trading days ending at ``end_w`` (inclusive). Returns ``None`` if
    bands collapse (upper ≤ lower) or data are insufficient.
    """
    end_ts = pd.Timestamp(ww.end_w).normalize()
    if end_ts not in df.index:
        return None
    try:
        pos = df.index.get_loc(end_ts)
        if isinstance(pos, slice):
            return None
        pi = int(pos)
        i0 = pi - (BB_PERIOD - 1)
        if i0 < 0:
            return None
        win = df.iloc[i0 : pi + 1]
        if len(win) != BB_PERIOD:
            return None
        closes = win["close"].astype(float).values
    except Exception:
        return None
    mid = float(np.mean(closes))
    std = float(np.std(closes, ddof=0))
    if not np.isfinite(std) or std <= 0:
        return None
    upper = mid + BB_STD_MULT * std
    lower = mid - BB_STD_MULT * std
    if upper <= lower:
        return None
    c_last = float(closes[-1])
    if not np.isfinite(c_last):
        return None
    pctb = (c_last - lower) / (upper - lower)
    if not np.isfinite(pctb):
        return None
    return float(pctb)


def score_shock_vol_roc(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """Relative 5-day volume vs average of prior 10 weekly 5-day totals × |5-day ROC|.

    Large magnitude in either direction (rally or selloff) scores high when volume is elevated.
    """
    start_ts = pd.Timestamp(ww.w_dates[0]).normalize()
    if start_ts not in df.index:
        return None
    try:
        pos = df.index.get_loc(start_ts)
        if isinstance(pos, slice):
            return None
        pos = int(pos)
    except Exception:
        return None
    if pos < SHOCK_PRIOR_WEEKS * 5:
        return None
    try:
        vol_w = float(df.iloc[pos : pos + 5]["volume"].astype(float).sum())
        block_sums: list[float] = []
        for k in range(1, SHOCK_PRIOR_WEEKS + 1):
            s = pos - 5 * k
            e = pos - 5 * (k - 1)
            block = df.iloc[s:e]
            if len(block) != 5:
                return None
            block_sums.append(float(block["volume"].astype(float).sum()))
        avg_vol = float(np.mean(block_sums))
        if avg_vol <= 0 or not np.isfinite(avg_vol) or not np.isfinite(vol_w) or vol_w < 0:
            return None
        rel = vol_w / avg_vol
        roc = roc5_week(df, ww)
        if roc is None or not np.isfinite(roc):
            return None
        return float(rel * abs(roc))
    except Exception:
        return None


def score_roc_acceleration(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """|current 5-day ROC − prior 5-day ROC| (both logics over consecutive calendar weeks in data)."""
    start_ts = pd.Timestamp(ww.w_dates[0]).normalize()
    if start_ts not in df.index:
        return None
    try:
        pos = df.index.get_loc(start_ts)
        if isinstance(pos, slice):
            return None
        pos = int(pos)
    except Exception:
        return None
    if pos < 5:
        return None
    try:
        c0 = float(df.iloc[pos]["close"])
        c1 = float(df.iloc[pos + 4]["close"])
        c_lo = float(df.iloc[pos - 5]["close"])
        c_hi = float(df.iloc[pos - 1]["close"])
        if min(c0, c1, c_lo, c_hi) <= 0:
            return None
        roc_curr = c1 / c0 - 1.0
        roc_prev = c_hi / c_lo - 1.0
        return float(abs(roc_curr - roc_prev))
    except Exception:
        return None


SCORERS: dict[str, Callable[..., float | None]] = {
    "momentum": lambda df, ww: score_momentum(df, ww.w_dates),
    "relative_volume": lambda df, ww: score_relative_volume(
        df, ww.w_dates, ww.prior_21_dates
    ),
    "bb_pctb": lambda df, ww: score_bb_pctb(df, ww),
    "shock_vol_roc": lambda df, ww: score_shock_vol_roc(df, ww),
    "roc_acceleration": lambda df, ww: score_roc_acceleration(df, ww),
}


def _ohlc_slice_for_atr(df: pd.DataFrame, ww: WeekWindow) -> pd.DataFrame | None:
    """Rows from one day before ``prior_21`` start through ``end_w`` (for TR + Wilder ATR)."""
    start_w = pd.Timestamp(ww.prior_21_dates[0]).normalize()
    end_w = pd.Timestamp(ww.end_w).normalize()
    idx = df.index
    if end_w not in idx or start_w not in idx:
        return None
    pos0 = idx.get_indexer([start_w], method=None)[0]
    if pos0 < 0:
        return None
    lo = pos0 - 1 if pos0 > 0 else pos0
    end_pos = idx.get_indexer([end_w], method=None)[0]
    if end_pos < 0:
        return None
    sub = df.iloc[lo : end_pos + 1]
    if len(sub) < ATR_PERIOD + 1:
        return None
    return sub


def _true_range_and_wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Wilder ATR(``ATR_PERIOD``); NaN until first fully seeded value at index ``ATR_PERIOD - 1``."""
    n = len(close)
    tr = np.zeros(n)
    tr[0] = float(high[0] - low[0])
    for i in range(1, n):
        pc = float(close[i - 1])
        tr[i] = max(
            float(high[i] - low[i]),
            abs(float(high[i]) - pc),
            abs(float(low[i]) - pc),
        )
    atr = np.full(n, np.nan)
    if n < ATR_PERIOD:
        return atr
    atr[ATR_PERIOD - 1] = float(np.mean(tr[:ATR_PERIOD]))
    for i in range(ATR_PERIOD, n):
        atr[i] = (atr[i - 1] * (ATR_PERIOD - 1) + tr[i]) / float(ATR_PERIOD)
    return atr


def _wilder_atr_series(df: pd.DataFrame) -> np.ndarray | None:
    """Wilder ATR(14) for each row of ``df`` (NaN until seeded)."""
    try:
        h = df["high"].astype(float).values
        lo = df["low"].astype(float).values
        c = df["close"].astype(float).values
    except Exception:
        return None
    if len(h) < ATR_PERIOD + 1:
        return None
    return _true_range_and_wilder_atr(h, lo, c)


def normalized_atr(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """ATR(14) at ``end_w`` divided by close at ``end_w``."""
    sub = _ohlc_slice_for_atr(df, ww)
    if sub is None:
        return None
    try:
        h = sub["high"].astype(float).values
        lo = sub["low"].astype(float).values
        c = sub["close"].astype(float).values
    except Exception:
        return None
    if len(h) != len(lo) or len(h) != len(c):
        return None
    atr = _true_range_and_wilder_atr(h, lo, c)
    end_ts = pd.Timestamp(ww.end_w).normalize()
    try:
        pos = sub.index.get_loc(end_ts)
        if isinstance(pos, slice):
            return None
        a = float(atr[int(pos)])
        cl = float(c[int(pos)])
    except Exception:
        return None
    if not np.isfinite(a) or not np.isfinite(cl) or cl <= 0:
        return None
    return a / cl


def roc5_week(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """Signed return over week W: close(end_W) / close(start_W) - 1."""
    idx = pd.to_datetime(list(ww.w_dates)).normalize()
    try:
        lo = df.index.get_indexer([idx[0]], method=None)[0]
        hi = df.index.get_indexer([idx[-1]], method=None)[0]
        if lo < 0 or hi < 0:
            return None
        c0 = float(df.iloc[lo]["close"])
        c1 = float(df.iloc[hi]["close"])
        if c0 <= 0 or np.isnan(c0) or np.isnan(c1):
            return None
        return c1 / c0 - 1.0
    except Exception:
        return None


def roc5_week_abs(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """Absolute weekly return over W; used for ``atr_roc5`` decile ranking."""
    r = roc5_week(df, ww)
    if r is None or not np.isfinite(r):
        return None
    return float(abs(r))


def vwap_week_deviation(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """(close_Friday / weekly_VWAP) - 1 with daily typical = (H+L+C)/3 over W."""
    idx_w = pd.to_datetime(list(ww.w_dates)).normalize()
    num = 0.0
    den = 0.0
    last_close: float | None = None
    try:
        for d in idx_w:
            j = df.index.get_indexer([d], method=None)[0]
            if j < 0:
                return None
            row = df.iloc[j]
            h = float(row["high"])
            l = float(row["low"])
            cl = float(row["close"])
            v = float(row["volume"])
            if np.isnan(h) or np.isnan(l) or np.isnan(cl) or np.isnan(v) or v < 0:
                return None
            typ = (h + l + cl) / 3.0
            num += typ * v
            den += v
            last_close = cl
        if den <= 0 or last_close is None:
            return None
        vwap = num / den
        if vwap <= 0:
            return None
        return abs(last_close / vwap - 1.0)
    except Exception:
        return None


def _range_expansion_ratio(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """(week high − week low) / mean daily ATR(14) over the 70 sessions before ``start_w``."""
    start_ts = pd.Timestamp(ww.w_dates[0]).normalize()
    if start_ts not in df.index:
        return None
    try:
        pos = df.index.get_loc(start_ts)
        if isinstance(pos, slice):
            return None
        pos = int(pos)
    except Exception:
        return None
    if pos < RANGE_EXPANSION_MIN_START_POS:
        return None
    w = df.iloc[pos : pos + 5]
    if len(w) != 5:
        return None
    try:
        wh = float(w["high"].max())
        wl = float(w["low"].min())
    except Exception:
        return None
    if not np.isfinite(wh) or not np.isfinite(wl) or wh <= wl:
        return None
    week_range = wh - wl
    atr = _wilder_atr_series(df)
    if atr is None:
        return None
    ms = pos - RANGE_EXPANSION_ATR_LOOKBACK_DAYS
    me = pos - 1
    if ms < 0:
        return None
    seg = atr[ms : me + 1]
    valid = seg[np.isfinite(seg)]
    if len(valid) < 1:
        return None
    mean_atr = float(np.mean(valid))
    if mean_atr <= 0 or not np.isfinite(mean_atr):
        return None
    r = week_range / mean_atr
    if not np.isfinite(r):
        return None
    return float(r)


def _week_close_range_extremity(df: pd.DataFrame, ww: WeekWindow) -> float | None:
    """|2×stoch − 1| with stoch = (Friday close − week low) / (week high − week low).

    1.0 when close is at the week high or low (strong up or down week); 0 at the midpoint.
    """
    start_ts = pd.Timestamp(ww.w_dates[0]).normalize()
    if start_ts not in df.index:
        return None
    try:
        pos = df.index.get_loc(start_ts)
        if isinstance(pos, slice):
            return None
        pos = int(pos)
    except Exception:
        return None
    w = df.iloc[pos : pos + 5]
    if len(w) != 5:
        return None
    try:
        hi = float(w["high"].max())
        lo = float(w["low"].min())
        fc = float(df.iloc[pos + 4]["close"])
    except Exception:
        return None
    if not np.isfinite(hi) or not np.isfinite(lo) or not np.isfinite(fc):
        return None
    if hi <= lo:
        return None
    stoch = (fc - lo) / (hi - lo)
    if not np.isfinite(stoch):
        return None
    return float(abs(2.0 * stoch - 1.0))


def score_universe_range_expansion_filtered(
    symbol_frames: dict[str, pd.DataFrame],
    ww: WeekWindow,
    *,
    keep_top: float,
) -> dict[str, float]:
    """Keep top ``keep_top`` fraction by range-expansion ratio; rank survivors by close extremity."""
    ranked: list[tuple[str, float]] = []
    for sym, df in symbol_frames.items():
        r = _range_expansion_ratio(df, ww)
        if r is not None and np.isfinite(r) and r > 0:
            ranked.append((sym, float(r)))
    n = len(ranked)
    if n == 0:
        return {}
    ranked.sort(key=lambda x: (-x[1], x[0]))
    keep_n = max(1, int(np.ceil(keep_top * n)))
    survivors = [s for s, _ in ranked[:keep_n]]
    if len(survivors) < 10:
        return {}
    out: dict[str, float] = {}
    for sym in survivors:
        e = _week_close_range_extremity(symbol_frames[sym], ww)
        if e is not None and np.isfinite(e):
            out[sym] = float(e)
    if len(out) < 10:
        return {}
    return out


def score_universe_atr_filtered(
    symbol_frames: dict[str, pd.DataFrame],
    ww: WeekWindow,
    second: Literal["roc5", "vwap"],
    *,
    keep_top: float,
) -> dict[str, float]:
    """Cross-sectional: keep top ``keep_top`` fraction by normalized ATR, then rank survivors only."""
    atr_ranked: list[tuple[str, float]] = []
    for sym, df in symbol_frames.items():
        a = normalized_atr(df, ww)
        if a is not None and np.isfinite(a) and a > 0:
            atr_ranked.append((sym, float(a)))
    n = len(atr_ranked)
    if n == 0:
        return {}
    atr_ranked.sort(key=lambda x: (-x[1], x[0]))
    keep_n = max(1, int(np.ceil(keep_top * n)))
    survivors = [s for s, _ in atr_ranked[:keep_n]]
    if len(survivors) < 10:
        return {}
    fn = roc5_week_abs if second == "roc5" else vwap_week_deviation
    out: dict[str, float] = {}
    for sym in survivors:
        v = fn(symbol_frames[sym], ww)
        if v is not None and np.isfinite(v):
            out[sym] = float(v)
    if len(out) < 10:
        return {}
    return out


def score_universe(
    symbol_frames: dict[str, pd.DataFrame],
    ww: WeekWindow,
    method: str,
    *,
    atr_keep_top: float = 0.35,
    range_expansion_keep_top: float = 0.2,
) -> dict[str, float]:
    if method == "atr_roc5":
        return score_universe_atr_filtered(
            symbol_frames, ww, "roc5", keep_top=atr_keep_top
        )
    if method == "atr_vwap_dev":
        return score_universe_atr_filtered(
            symbol_frames, ww, "vwap", keep_top=atr_keep_top
        )
    if method == "range_expansion":
        return score_universe_range_expansion_filtered(
            symbol_frames, ww, keep_top=range_expansion_keep_top
        )
    if method not in SCORERS:
        raise ValueError(
            f"Unknown scoring method {method!r}. Implemented: {list(SCORING_METHOD_CHOICES)}"
        )
    fn = SCORERS[method]
    out: dict[str, float] = {}
    for sym, df in symbol_frames.items():
        v = fn(df, ww)
        if v is not None and np.isfinite(v):
            out[sym] = float(v)
    return out


def assign_deciles_and_top_groups(
    scores: dict[str, float],
    *,
    top_k_groups: int = 3,
) -> tuple[pd.Series, list[int]]:
    """Return (symbol -> decile_bin 0..), and list of **top** ``top_k_groups`` bin ids (high scores).

    Deciles are **equal-count by rank**: symbols sorted by (score descending, ticker ascending)
    for a stable tie-break, then split into up to 10 bins. Bin 0 = worst-ranked names; highest
    bin id = top decile. This avoids collapsing to a single bin when many tickers share the same
    raw score (e.g. identical test data).
    """
    if not scores:
        return pd.Series(dtype="Int64"), []
    symbols_sorted = sorted(scores.keys(), key=lambda sym: (-scores[sym], sym))
    n = len(symbols_sorted)
    if n == 1:
        decile_series = pd.Series(0, index=symbols_sorted, dtype="Int64")
        return decile_series, [0]
    n_bins = min(10, n)
    # Pseudo scores n..1 so the best-ranked symbol (first in list) gets the largest value and
    # lands in the highest qcut bin.
    pseudo = np.arange(n, 0, -1, dtype=float)
    try:
        cats = pd.qcut(
            pseudo,
            q=n_bins,
            labels=False,
            duplicates="drop",
        )
    except ValueError:
        cats = np.zeros(n, dtype=int)
    cats = np.asarray(cats, dtype=int)
    if cats.shape != (n,):
        cats = np.zeros(n, dtype=int)
    decile_series = pd.Series(cats, index=symbols_sorted, dtype="Int64")
    unique_bins = sorted(int(x) for x in decile_series.dropna().unique())
    if not unique_bins:
        return decile_series, []
    top_bins = unique_bins[-top_k_groups:]
    return decile_series, top_bins


def symbols_in_bins(
    decile_series: pd.Series,
    target_bins: list[int],
) -> dict[int, list[str]]:
    """Symbols per bin id (unordered within bin)."""
    out: dict[int, list[str]] = {b: [] for b in target_bins}
    for sym, b in decile_series.items():
        if pd.isna(b):
            continue
        bi = int(b)
        if bi in out:
            out[bi].append(sym)
    for b in target_bins:
        out[b].sort()
    return out


def mean_score_for_symbols(scores: dict[str, float], symbols: list[str]) -> float | None:
    vals = [scores[s] for s in symbols if s in scores]
    if not vals:
        return None
    return float(np.mean(vals))


def compound_returns(returns_pct: list[float]) -> float:
    """Geometric compound: prod(1 + r/100) - 1, as percent."""
    p = 1.0
    for r in returns_pct:
        p *= 1.0 + float(r) / 100.0
    return (p - 1.0) * 100.0
