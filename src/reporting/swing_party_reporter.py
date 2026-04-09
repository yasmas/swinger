"""SwingParty multi-asset HTML report: normalized % lines (solid=held, dotted=not) + portfolio."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader

from multi_asset_controller import load_multi_asset_datasets
from trade_log import TradeLogReader
from reporting.reporter import compute_stats, TEMPLATES_DIR
from reporting.lazy_swing_reporter import _build_portfolio, _resample_ohlcv

# Distinct colors for asset lines (solid + dotted share the same color per symbol)
ASSET_LINE_COLORS = [
    "#38bdf8",
    "#f472b6",
    "#a3e635",
    "#fbbf24",
    "#c084fc",
    "#2dd4bf",
    "#fb923c",
    "#94a3b8",
]


def _held_state_after_trade(trade_log: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Per-row held flag for rows affecting `symbol` (position after that row)."""
    sl = trade_log[trade_log["symbol"] == symbol].sort_values("date").copy()
    if sl.empty:
        return sl
    pos = sl["position_qty"].astype(float)
    sh = sl["short_qty"].astype(float)
    sl["_held"] = (pos > 0) | (sh > 0)
    return sl[["date", "_held"]]


def _to_merge_asof_dates(s: pd.Series | pd.DatetimeIndex) -> pd.Series:
    """Normalize to tz-naive datetime64[ns] for merge_asof (avoids ms vs us mismatch)."""
    raw = pd.to_datetime(s, utc=False)
    arr = np.asarray(getattr(raw, "values", raw), dtype="datetime64[ns]")
    return pd.Series(arr)


def _evaluation_times(bar_times: pd.DatetimeIndex, freq: str | None) -> pd.DatetimeIndex:
    """End-of-bar instant for held state (align with bar close, not left edge).

    Resampled bars use left labels; trades during the bar must be included.
    Uses Timedelta arithmetic so datetime64[us]/[ns] indices stay consistent.
    """
    if len(bar_times) == 0:
        return bar_times
    s = pd.Series(bar_times)
    if freq == "1h":
        delta = pd.Timedelta(hours=1)
    elif freq == "4h":
        delta = pd.Timedelta(hours=4)
    else:
        if len(bar_times) >= 2:
            delta = s.diff().median()
            if pd.isna(delta) or delta <= pd.Timedelta(0):
                delta = pd.Timedelta(minutes=5)
        else:
            delta = pd.Timedelta(minutes=5)
    ends = s + delta - pd.Timedelta(nanoseconds=1)
    return pd.DatetimeIndex(ends)


def held_flags_at_bar_times(
    trade_log: pd.DataFrame,
    symbol: str,
    bar_times: pd.DatetimeIndex,
    freq: str | None = None,
) -> pd.Series:
    """Whether `symbol` is in the book at each bar (state after all trades through bar close)."""
    sl = _held_state_after_trade(trade_log, symbol)
    if sl.empty or len(bar_times) == 0:
        return pd.Series(False, index=bar_times)

    eval_times = _evaluation_times(bar_times, freq)
    bars = pd.DataFrame(
        {
            "date": _to_merge_asof_dates(eval_times),
            "_ord": np.arange(len(bar_times), dtype=np.int64),
        }
    )
    sl = sl.copy()
    sl["date"] = _to_merge_asof_dates(sl["date"])
    sl = sl.dropna(subset=["date"])
    bars = bars.sort_values("date")
    if sl.empty:
        return pd.Series(False, index=bar_times)
    merged = pd.merge_asof(
        bars,
        sl.sort_values("date"),
        on="date",
        direction="backward",
    )
    merged["_held"] = merged["_held"].fillna(False).astype(bool)
    merged = merged.sort_values("_ord")
    return pd.Series(merged["_held"].values, index=bar_times)


def _union_bar_index(datasets: dict[str, pd.DataFrame], freq: str | None) -> pd.DatetimeIndex:
    """Sorted union of bar timestamps for all assets at this timeframe."""
    if freq is None:
        idx_sets = [df.index for df in datasets.values()]
    else:
        idx_sets = [_resample_ohlcv(df, freq).index for df in datasets.values()]
    if not idx_sets:
        return pd.DatetimeIndex([])
    u = idx_sets[0]
    for ix in idx_sets[1:]:
        u = u.union(ix)
    return u.sort_values()


def _aligned_close_series(
    df: pd.DataFrame, bar_times: pd.DatetimeIndex, freq: str | None
) -> pd.Series:
    if freq is None:
        s = df["close"].reindex(bar_times, method="ffill")
    else:
        rs = _resample_ohlcv(df, freq)["close"]
        s = rs.reindex(bar_times, method="ffill")
    return s


def _pct_from_first(close: pd.Series) -> pd.Series:
    first = close.dropna()
    if first.empty:
        return pd.Series(np.nan, index=close.index)
    base = float(first.iloc[0])
    if base == 0:
        return pd.Series(np.nan, index=close.index)
    return (close.astype(float) / base - 1.0) * 100.0


def _segment_points(
    bar_times: pd.DatetimeIndex, pct: pd.Series, held: pd.Series, want_held: bool
) -> list[list[dict]]:
    """Contiguous runs as separate point lists so the chart does not draw across gaps."""
    segs: list[list[dict]] = []
    cur: list[dict] = []
    for i, t in enumerate(bar_times):
        v = pct.iloc[i] if i < len(pct) else np.nan
        if pd.isna(v):
            continue
        h = bool(held.iloc[i]) if i < len(held) else False
        if h != want_held:
            if cur:
                segs.append(cur)
                cur = []
            continue
        pt = {"time": int(pd.Timestamp(t).timestamp()), "value": round(float(v), 4)}
        cur.append(pt)
    if cur:
        segs.append(cur)
    return segs


def _tf_chart_payload(
    datasets: dict[str, pd.DataFrame],
    trade_log: pd.DataFrame,
    freq: str | None,
) -> dict[str, dict]:
    """Per-symbol solid/dotted line data for one timeframe."""
    symbols = sorted(datasets.keys())
    bar_times = _union_bar_index(datasets, freq)
    if len(bar_times) == 0:
        return {}

    out: dict[str, dict] = {}
    for sym in symbols:
        close = _aligned_close_series(datasets[sym], bar_times, freq)
        pct = _pct_from_first(close)
        held = held_flags_at_bar_times(trade_log, sym, bar_times, freq=freq)
        out[sym] = {
            "solid": _segment_points(bar_times, pct, held, True),
            "dotted": _segment_points(bar_times, pct, held, False),
        }
    return out


def _equal_weight_bnh(datasets: dict[str, pd.DataFrame]) -> tuple[float, float]:
    """Average buy-and-hold return % and CAGR % across assets."""
    rets = []
    for df in datasets.values():
        if df.empty or "close" not in df.columns:
            continue
        c = df["close"].dropna()
        if len(c) < 2:
            continue
        first = float(c.iloc[0])
        last = float(c.iloc[-1])
        if first <= 0:
            continue
        rets.append((last / first - 1) * 100)
    if not rets:
        return 0.0, 0.0
    avg_ret = float(np.mean(rets))
    # Use span of first non-empty dataset for time
    any_df = next(iter(datasets.values()))
    start = any_df.index[0]
    end = any_df.index[-1]
    days = (end - start).days
    years = days / 365.25 if days > 0 else 1.0
    # CAGR of equal-weight terminal wealth: approximate as mean of CAGRs
    cagrs = []
    for df in datasets.values():
        c = df["close"].dropna()
        if len(c) < 2:
            continue
        f_, l_ = float(c.iloc[0]), float(c.iloc[-1])
        if f_ <= 0 or years <= 0:
            continue
        cagrs.append(((l_ / f_) ** (1 / years) - 1) * 100)
    avg_cagr = float(np.mean(cagrs)) if cagrs else 0.0
    return avg_ret, avg_cagr


def build_swing_party_chart_data(
    datasets: dict[str, pd.DataFrame], trade_log: pd.DataFrame
) -> dict:
    """JSON-serializable chart bundle for swing_party_report.html."""
    return {
        "5m": _tf_chart_payload(datasets, trade_log, None),
        "1h": _tf_chart_payload(datasets, trade_log, "1h"),
        "4h": _tf_chart_payload(datasets, trade_log, "4h"),
        "portfolio": _build_portfolio(trade_log),
    }


def symbol_color_map(symbols: list[str]) -> list[dict]:
    return [
        {"symbol": sym, "color": ASSET_LINE_COLORS[i % len(ASSET_LINE_COLORS)]}
        for i, sym in enumerate(symbols)
    ]


class SwingPartyReporter:
    """HTML report: multi-asset normalized performance + portfolio value."""

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        trade_log_path: str,
        config: dict,
        strategy_name: str = "swing_party",
        version: str = "",
        output_filename: str | None = None,
        auto_refresh_seconds: int | None = None,
    ) -> str:
        trade_log = TradeLogReader.read(trade_log_path)
        if not trade_log.empty:
            trade_log = trade_log.sort_values("date").reset_index(drop=True)
        datasets = load_multi_asset_datasets(config)
        if not datasets:
            raise ValueError("No price data loaded for SwingParty report")

        backtest = config["backtest"]
        strategy_cfg = config["strategy"]
        initial_cash = float(backtest["initial_cash"])
        cost_pct = float(strategy_cfg.get("cost_per_trade_pct", 0.05))

        stats = compute_stats(trade_log, initial_cash, cost_per_trade_pct=cost_pct)
        bnh_ret, bnh_cagr = _equal_weight_bnh(datasets)
        stats["bnh_return"] = bnh_ret
        stats["bnh_cagr"] = bnh_cagr

        chart_data = build_swing_party_chart_data(datasets, trade_log)
        symbols = sorted(datasets.keys())
        meta = symbol_color_map(symbols)

        if not trade_log.empty:
            start_date = str(trade_log.iloc[0]["date"].date())
            end_date = str(trade_log.iloc[-1]["date"].date())
        else:
            start_date = str(min(df.index[0] for df in datasets.values()).date())
            end_date = str(max(df.index[-1] for df in datasets.values()).date())

        st_atr = int(strategy_cfg.get("supertrend_atr_period", 10))
        st_mult = float(strategy_cfg.get("supertrend_multiplier", 2.0))
        max_pos = int(strategy_cfg.get("max_positions", 3))

        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        template = env.get_template("swing_party_report.html")

        html = template.render(
            strategy_name=strategy_name,
            assets_label=", ".join(symbols),
            start_date=start_date,
            end_date=end_date,
            stats=stats,
            version=version,
            auto_refresh_seconds=auto_refresh_seconds,
            chart_data_json=json.dumps(chart_data),
            symbols_meta=meta,
            symbols_json=json.dumps(meta),
            st_atr_period=st_atr,
            st_multiplier=st_mult,
            max_positions=max_pos,
        )

        if output_filename is None:
            ver = f"_{version}" if version else ""
            safe_name = str(backtest["name"]).replace(" ", "_")
            output_filename = f"{strategy_name}_{safe_name}_{start_date}_{end_date}{ver}.html"
        output_path = self.output_dir / output_filename

        with open(output_path, "w") as f:
            f.write(html)

        return str(output_path)
