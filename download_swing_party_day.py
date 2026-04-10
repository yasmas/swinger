#!/usr/bin/env python3
"""Download one calendar day of 5m OHLCV and write Binance-style CSVs for SwingParty backtests.

Providers:
  alpaca     — Alpaca Data API v2 (5m bars directly). Uses ALPACA_API_KEY + ALPACA_API_SECRET.
  databento  — XNAS.ITCH 1m via Databento, resampled to 5m. Uses DATABENTO_API_KEY.

Used for configs with file_pattern like "{symbol}-5m-YYYY-MM-DD.csv".

Usage:
    PYTHONPATH=src python download_swing_party_day.py config/strategies/swing_party/apr9-movers.yaml
    PYTHONPATH=src python download_swing_party_day.py config/.../apr9-movers.yaml --provider databento

Reads credentials from the repo-root `.env` and `data/yasmas/.env` (etc.), not only the shell.
Run from any cwd; paths are resolved from this script's location.

If both APIs fail, use --synthetic for deterministic placeholder bars (offline).

Warmup (default when a strategy config is passed): extra trading days of 5m bars are downloaded
before the backtest `start_date` so LazySwing can finish ST/HMACD warmup and volume_breakout can
use `long_window` on 1h-resampled volume. Formula: max(supertrend_atr_period * 15, long_window,
HMACD slow+signal+5) hourly bars, converted with ~6.5 RTH hours/day + 3 day buffer. Override with
`--warmup-trading-days N` or `--no-warmup`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from strategies.warmup_calendar import warmup_range_start_day, warmup_trading_days_from_strategy

DEFAULT_DATASET = "XNAS.ITCH"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _load_dotenv_files() -> None:
    """Load .env from repo root and common locations (cwd-independent)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = _repo_root()
    for path in (
        root / ".env",
        root / "data" / "yasmas" / ".env",
        root / "data" / "testuser" / ".env",
    ):
        if path.is_file():
            load_dotenv(path, override=False)


def _load_databento_key() -> str:
    _load_dotenv_files()
    key = os.environ.get("DATABENTO_API_KEY", "").strip()
    if not key:
        root = _repo_root()
        print(
            "ERROR: DATABENTO_API_KEY not set. Add it to .env at the repo root, e.g.\n"
            f"  {root / '.env'}\n"
            "or use --provider alpaca, or export the key in your shell.",
            file=sys.stderr,
        )
        sys.exit(1)
    return key


def _load_alpaca_creds() -> tuple[str, str]:
    _load_dotenv_files()
    key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    if not key or not secret:
        root = _repo_root()
        print(
            "ERROR: ALPACA_API_KEY and ALPACA_API_SECRET required for --provider alpaca.\n"
            f"  Add them to {root / '.env'} (or data/yasmas/.env).",
            file=sys.stderr,
        )
        sys.exit(1)
    return key, secret


def _ensure_src_on_path() -> None:
    src = _repo_root() / "src"
    s = str(src.resolve())
    if s not in sys.path:
        sys.path.insert(0, s)


def _read_yaml(path: Path) -> dict:
    import yaml

    with open(path) as f:
        return yaml.safe_load(f)


def _day_from_config(cfg: dict) -> str:
    return str(cfg["backtest"]["start_date"])[:10]


def _pattern_and_dir(cfg: dict) -> tuple[str, Path]:
    params = cfg["data_source"]["params"]
    data_dir = Path(params.get("data_dir", "data/backtests"))
    pattern = params.get("file_pattern", "{symbol}-5m-{start_year}-{end_year}-combined.csv")
    return pattern, data_dir


def _output_path(symbol: str, day: str, pattern: str, data_dir: Path) -> Path:
    import string

    y = day[:4]
    fmt_keys = {fn for _, fn, _, _ in string.Formatter().parse(pattern) if fn}
    ctx = {"symbol": symbol, "start_year": y, "end_year": y, "day": day}
    fname = pattern.format(**{k: ctx[k] for k in fmt_keys})
    return data_dir / fname


def write_synthetic_range(symbol: str, start_day: str, end_day_exclusive: str, out_path: Path) -> None:
    """Synthetic 5m bars from start_day 00:00 UTC through end_day_exclusive (exclusive)."""
    start = pd.Timestamp(f"{start_day}T00:00:00Z")
    end = pd.Timestamp(f"{end_day_exclusive}T00:00:00Z")
    periods = max(1, int((end - start) / pd.Timedelta(minutes=5)))
    rng = np.random.default_rng(abs(hash(symbol + start_day + end_day_exclusive)) % (2**32))
    idx = pd.date_range(start, periods=periods, freq="5min", tz="UTC")
    price = 100.0 + np.cumsum(rng.normal(0, 0.15, periods))
    noise = rng.uniform(0.02, 0.4, periods)
    high = price + noise
    low = price - noise
    open_ = np.r_[price[0], price[:-1]]
    vol = rng.integers(500, 50000, periods).astype(float)
    out = pd.DataFrame(
        {
            "open_time": idx.tz_localize(None).astype("datetime64[ms]").astype("int64"),
            "open": open_,
            "high": np.maximum.reduce([open_, high, low, price]),
            "low": np.minimum.reduce([open_, high, low, price]),
            "close": price,
            "volume": vol,
        }
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f" {len(out)} synthetic bars → {out_path.name}")


def write_synthetic_day(symbol: str, day: str, out_path: Path, bars: int = 288) -> None:
    """Write Binance-style 5m CSV with deterministic synthetic OHLCV (offline / license fallback)."""
    rng = np.random.default_rng(abs(hash(symbol + day)) % (2**32))
    start = pd.Timestamp(f"{day}T00:00:00Z")
    idx = pd.date_range(start, periods=bars, freq="5min", tz="UTC")
    price = 100.0 + np.cumsum(rng.normal(0, 0.15, bars))
    noise = rng.uniform(0.02, 0.4, bars)
    high = price + noise
    low = price - noise
    open_ = np.r_[price[0], price[:-1]]
    vol = rng.integers(500, 50000, bars).astype(float)

    out = pd.DataFrame(
        {
            "open_time": idx.tz_localize(None).astype("datetime64[ms]").astype("int64"),
            "open": open_,
            "high": np.maximum.reduce([open_, high, low, price]),
            "low": np.minimum.reduce([open_, high, low, price]),
            "close": price,
            "volume": vol,
        }
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f" {len(out)} synthetic bars → {out_path.name}")


def download_alpaca_5m_range(
    symbol: str,
    start_day: str,
    end_day_exclusive: str,
    out_path: Path,
    feed: str = "iex",
) -> bool:
    """5m bars from Alpaca from start_day 00:00 UTC through end_day_exclusive (exclusive)."""
    print(
        f"  {symbol}: {start_day} .. {end_day_exclusive} (excl) → {out_path.name} (Alpaca {feed}) ...",
        end="",
        flush=True,
    )
    try:
        _ensure_src_on_path()
        from exchange.alpaca_rest import AlpacaRestClient

        client = AlpacaRestClient({"feed": feed})
        start = pd.Timestamp(f"{start_day}T00:00:00Z")
        end = pd.Timestamp(f"{end_day_exclusive}T00:00:00Z")
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        df = client.fetch_ohlcv(
            symbol, "5m", start_time_ms=start_ms, end_time_ms=end_ms, limit=10_000
        )
    except Exception as e:
        print(f" FAILED ({e})")
        return False

    if df.empty:
        print(" no rows")
        return False

    out = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    out = out.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="first")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f" {len(out)} bars")
    return True


def download_one_alpaca(
    symbol: str,
    day: str,
    out_path: Path,
    feed: str = "iex",
    warmup_trading_days: int = 0,
) -> bool:
    end_excl = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    start_day = warmup_range_start_day(day, warmup_trading_days)
    return download_alpaca_5m_range(symbol, start_day, end_excl, out_path, feed=feed)


def download_one_databento(
    client,
    symbol: str,
    day: str,
    out_path: Path,
    dataset: str = DEFAULT_DATASET,
    warmup_trading_days: int = 0,
) -> bool:
    end_excl = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    start_day = warmup_range_start_day(day, warmup_trading_days)
    print(f"  {symbol}: {start_day} .. {end_excl} (excl) → {out_path.name} ...", end="", flush=True)
    try:
        data = client.timeseries.get_range(
            dataset=dataset,
            symbols=[symbol],
            stype_in="raw_symbol",
            schema="ohlcv-1m",
            start=f"{start_day}T00:00:00",
            end=f"{end_excl}T00:00:00",
        )
        df = data.to_df()
    except Exception as e:
        print(f" FAILED ({e})")
        return False

    if df.empty:
        print(" no rows")
        return False

    ohlcv = df[["open", "high", "low", "close", "volume"]].copy()
    df_5m = ohlcv.resample("5min").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna(subset=["open"])

    out = pd.DataFrame()
    out["open_time"] = df_5m.index.tz_localize(None).astype("datetime64[ms]").astype("int64")
    out["open"] = df_5m["open"].values
    out["high"] = df_5m["high"].values
    out["low"] = df_5m["low"].values
    out["close"] = df_5m["close"].values
    out["volume"] = df_5m["volume"].values

    out = out.sort_values("open_time").drop_duplicates(subset=["open_time"], keep="first")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    print(f" {len(out)} bars")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="Download one day of 5m OHLCV for SwingParty backtest CSVs.")
    ap.add_argument(
        "config",
        nargs="?",
        help="Strategy YAML (uses backtest.start_date, data_source, strategy.assets)",
    )
    ap.add_argument("--day", help="Override calendar day YYYY-MM-DD")
    ap.add_argument("--symbols", help="Comma-separated symbols (override config assets)")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument(
        "--provider",
        choices=("alpaca", "databento"),
        default="alpaca",
        help="alpaca: Data API v2 (default). databento: XNAS.ITCH 1m → 5m.",
    )
    ap.add_argument(
        "--alpaca-feed",
        choices=("iex", "sip"),
        default="iex",
        help="Alpaca bars feed: iex (default, free) or sip (paid consolidated).",
    )
    ap.add_argument(
        "--synthetic",
        action="store_true",
        help="Skip APIs; write deterministic synthetic 5m bars for the day",
    )
    ap.add_argument(
        "--warmup-trading-days",
        type=int,
        default=None,
        metavar="N",
        help="Trading days of 5m history before --day (default: auto from strategy YAML when config is passed)",
    )
    ap.add_argument(
        "--no-warmup",
        action="store_true",
        help="Only load the target calendar day (no extra history for indicators)",
    )
    args = ap.parse_args()

    if not args.config and not (args.day and args.symbols):
        ap.print_help()
        sys.exit(1)

    cfg = None
    if args.config:
        cfg = _read_yaml(Path(args.config))
        day = args.day or _day_from_config(cfg)
        symbols = (
            [s.strip() for s in args.symbols.split(",")]
            if args.symbols
            else list(cfg["strategy"]["assets"])
        )
        pattern, data_dir = _pattern_and_dir(cfg)
    else:
        day = args.day
        if not day or not args.symbols:
            print("With no config file, pass both --day and --symbols", file=sys.stderr)
            sys.exit(1)
        symbols = [s.strip() for s in args.symbols.split(",")]
        pattern = f"{{symbol}}-5m-{day}.csv"
        data_dir = Path("data/backtests")

    if args.no_warmup:
        warmup_td = 0
    elif args.warmup_trading_days is not None:
        warmup_td = max(0, args.warmup_trading_days)
    elif cfg is not None:
        warmup_td = warmup_trading_days_from_strategy(cfg.get("strategy", {}))
    else:
        warmup_td = 0

    end_excl = (pd.Timestamp(day) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    range_start = warmup_range_start_day(day, warmup_td)

    print(
        f"Day={day}  symbols={symbols}  out_dir={data_dir}  "
        f"provider={args.provider}  synthetic={args.synthetic}  "
        f"warmup_trading_days={warmup_td}  fetch_range={range_start}..{end_excl}(excl)"
    )

    if args.synthetic:
        for sym in symbols:
            out = _output_path(sym, day, pattern, data_dir) if args.config else data_dir / f"{sym}-5m-{day}.csv"
            print(f"  {sym}:", end="", flush=True)
            if warmup_td > 0:
                write_synthetic_range(sym, range_start, end_excl, out)
            else:
                write_synthetic_day(sym, day, out)
        print("\nDone (synthetic).")
        return

    ok = 0
    db_client = None
    if args.provider == "databento":
        import databento as db

        db_client = db.Historical(_load_databento_key())
    elif args.provider == "alpaca":
        _load_alpaca_creds()

    for sym in symbols:
        out = (
            _output_path(sym, day, pattern, data_dir)
            if args.config
            else data_dir / f"{sym}-5m-{day}.csv"
        )
        if args.provider == "alpaca":
            if download_one_alpaca(
                sym, day, out, feed=args.alpaca_feed, warmup_trading_days=warmup_td
            ):
                ok += 1
        else:
            if download_one_databento(
                db_client, sym, day, out, dataset=args.dataset, warmup_trading_days=warmup_td
            ):
                ok += 1

    print(f"\nDone: {ok}/{len(symbols)} symbols written.")
    if ok < len(symbols):
        sys.exit(1)


if __name__ == "__main__":
    main()
