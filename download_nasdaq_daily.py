#!/usr/bin/env python3
"""Download daily OHLCV via Massive/Polygon (``MASSIVE_API_KEY``).

Universe (``--universe``):

- ``listed`` — all Nasdaq-listed symbols from Nasdaq Trader ``nasdaqlisted.txt``
  (thousands of tickers). Default output: ``data/backtests/nasdaq_listed``.
- ``nasdaq100`` — **Nasdaq-100 index** constituents (~100 names, same set QQQ tracks).
  Parsed from Wikipedia article ``Nasdaq-100`` (``==Current components==`` table).
  Default output: ``data/backtests/nasdaq100``.

Examples::

  python download_nasdaq_daily.py
  python download_nasdaq_daily.py --universe listed --max-symbols 50   # smoke test

Requires ``MASSIVE_API_KEY`` in repo ``.env`` (see ``src/exchange/massive_rest.py``).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

NASDAQ_LISTED_URLS = (
    "https://ftp.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
)

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_HEADERS = {
    "User-Agent": (
        "SwingerDownloadNasdaqDaily/1.0 "
        "(constituent list via MediaWiki API; respectful crawl)"
    ),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = _repo_root()
    for p in (root / ".env", root / "data" / "yasmas" / ".env"):
        if p.is_file():
            load_dotenv(p, override=False)


def fetch_nasdaq_symbols() -> list[str]:
    """Return active NASDAQ-listed tickers (no test issues)."""
    text = ""
    last_err: Exception | None = None
    headers = {"User-Agent": "swinger-download-nasdaq-daily/1.0"}
    for url in NASDAQ_LISTED_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=120)
            r.raise_for_status()
            text = r.text
            break
        except Exception as e:
            last_err = e
    if not text:
        raise RuntimeError(f"Could not download NASDAQ symbol list: {last_err}")
    lines = text.splitlines()
    symbols: list[str] = []
    for line in lines[1:]:
        if not line.strip() or line.startswith("File Creation Time"):
            break
        parts = line.rstrip().split("|")
        if len(parts) < 4:
            continue
        sym = parts[0].strip()
        test_issue = (parts[3] or "").strip().upper()
        if not sym or test_issue == "Y":
            continue
        if not re.fullmatch(r"[A-Z0-9.\-^]+", sym):
            continue
        symbols.append(sym)
    return sorted(set(symbols))


def _load_or_fetch_nasdaq100_cached(out_dir: Path) -> list[str]:
    """Nasdaq-100 constituents: use ``out_dir/.nasdaq100_symbols.txt`` if < 24h old, else Wikipedia."""
    cache = out_dir / ".nasdaq100_symbols.txt"
    if cache.is_file():
        age = time.time() - cache.stat().st_mtime
        if age < 24 * 3600:
            lines = [ln.strip() for ln in cache.read_text().splitlines() if ln.strip()]
            if len(lines) >= 99:
                print(
                    f"Using cached Nasdaq-100 list ({len(lines)} syms, age {age / 3600:.1f}h)",
                    flush=True,
                )
                return sorted(lines)
    syms = fetch_nasdaq100_symbols()
    try:
        cache.write_text("\n".join(syms) + "\n")
    except OSError:
        pass
    return syms


def fetch_nasdaq100_symbols() -> list[str]:
    """Return Nasdaq-100 index tickers (QQQ basket), newest ``==Current components==`` table.

    Source: English Wikipedia ``Nasdaq-100`` via the MediaWiki API (wikitext).
    Nasdaq occasionally rebalances; Wikipedia is updated by editors — verify for production.
    """
    params = {
        "action": "query",
        "format": "json",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "titles": "Nasdaq-100",
    }
    r = requests.get(
        WIKIPEDIA_API,
        params=params,
        headers=WIKIPEDIA_HEADERS,
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    pages = data.get("query", {}).get("pages", {})
    if not pages:
        raise RuntimeError("Wikipedia API: empty pages")
    page = next(iter(pages.values()))
    revs = page.get("revisions") or []
    if not revs:
        raise RuntimeError("Wikipedia API: no revisions")
    text = revs[0]["slots"]["main"]["*"]
    start = text.find("==Current components==")
    if start < 0:
        raise RuntimeError("Wikipedia layout changed: missing ==Current components==")
    end = text.find("==Component changes==", start)
    if end < 0:
        raise RuntimeError("Wikipedia layout changed: missing ==Component changes==")
    section = text[start:end]
    syms = re.findall(
        r"^\| ([A-Z][A-Z0-9.\-]*) \|\| \[\[",
        section,
        flags=re.MULTILINE,
    )
    if len(syms) < 99:
        raise RuntimeError(f"Expected ~100 Nasdaq-100 tickers, got {len(syms)}")
    if len(syms) != 100:
        print(
            f"WARNING: expected 100 Nasdaq-100 constituents, got {len(syms)}",
            flush=True,
        )
    return syms


def daily_df_to_csv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize Massive client output to date + OHLCV columns."""
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    out = df.copy()
    out["date"] = pd.to_datetime(out["open_time"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")
    return out[["date", "open", "high", "low", "close", "volume"]]


def last_date_in_daily_csv(path: Path) -> date | None:
    """Return the last ``date`` column value in a daily OHLCV CSV without reading the whole file."""
    try:
        sz = path.stat().st_size
    except OSError:
        return None
    if sz < 10:
        return None
    try:
        if sz <= 65536:
            with open(path, "rb") as f:
                raw = f.read()
        else:
            with open(path, "rb") as f:
                f.seek(-min(16384, sz), os.SEEK_END)
                raw = f.read()
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    for line in reversed(lines):
        low = line.lower()
        if low.startswith("date") and "open" in low:
            continue
        first = line.split(",")[0].strip().strip('"')
        try:
            return pd.to_datetime(first).date()
        except Exception:
            continue
    return None


def _compute_start_end_ms(months: float) -> tuple[int, int]:
    """UTC window: ``months`` of history ending now → (start_ms, end_ms)."""
    end_ms = int(time.time() * 1000)
    now = pd.Timestamp.now(tz="UTC")
    whole_m = int(months)
    frac_m = max(0.0, months - whole_m)
    start = now - pd.DateOffset(months=whole_m)
    if frac_m > 1e-6:
        start -= pd.Timedelta(days=round(frac_m * 30.437))
    start_ms = int(start.timestamp() * 1000)
    return start_ms, end_ms


def refill_nasdaq_daily(
    output_dir: Path,
    *,
    universe: str = "nasdaq100",
    months: float = 12.0,
    sleep_sec: float = 0.12,
    min_date: date | None = None,
    force_full: bool = False,
) -> None:
    """Download daily OHLCV for every symbol in ``universe`` into ``output_dir``.

    Overwrites existing CSVs. Raises ``RuntimeError`` if ``MASSIVE_API_KEY`` is missing,
    any symbol returns empty data, or any API/processing error (strict pipeline for rotation).

    When ``min_date`` is set and ``force_full`` is false, symbols whose CSV already has a last
    row on or after ``min_date`` are skipped (tail read only — no Massive call).

    Parameters
    ----------
    universe
        ``nasdaq100`` or ``listed`` (same semantics as CLI ``--universe``).
    output_dir
        Directory for ``{SYM}.csv`` files (created if needed).
    months
        Calendar months of history (default 12).
    sleep_sec
        Pause between API calls (rate limiting).
    min_date
        If set (and not ``force_full``), skip Massive download when existing CSV last date
        is on or after this day.
    force_full
        If true, always re-download every symbol (ignore ``min_date`` skips).
    """
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if universe == "nasdaq100":
        symbols = _load_or_fetch_nasdaq100_cached(out_dir)
    elif universe == "listed":
        symbols = fetch_nasdaq_symbols()
    else:
        raise ValueError(f"Unknown universe: {universe!r}")

    n_skip = 0
    n_dl = 0
    need_download: list[str] = []
    for sym in symbols:
        path = out_dir / f"{sym}.csv"
        if min_date is not None and not force_full:
            last_d = last_date_in_daily_csv(path)
            if last_d is not None and last_d >= min_date:
                n_skip += 1
                continue
        need_download.append(sym)

    if not need_download:
        print(
            f"Daily refill: 0 downloaded, {n_skip} skipped (already >= "
            f"{min_date.isoformat() if min_date else 'n/a'}) — no Massive calls",
            flush=True,
        )
        return

    _load_dotenv()
    if not os.getenv("MASSIVE_API_KEY", "").strip():
        raise RuntimeError("MASSIVE_API_KEY not set. Add it to .env at the repo root.")

    sys.path.insert(0, str(_repo_root() / "src"))
    from exchange.massive_rest import MassiveRestClient

    start_ms, end_ms = _compute_start_end_ms(months)
    client = MassiveRestClient({})

    for i, sym in enumerate(need_download):
        path = out_dir / f"{sym}.csv"
        try:
            df = client.fetch_ohlcv(
                sym,
                "1d",
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                limit=500,
            )
            daily = daily_df_to_csv(df)
            if daily.empty:
                raise RuntimeError(f"empty daily response for {sym}")
            daily.to_csv(path, index=False)
            n_dl += 1
        except Exception as e:
            raise RuntimeError(f"daily download failed for {sym}: {e}") from e

        if sleep_sec > 0:
            time.sleep(sleep_sec)

        step = 25 if len(need_download) <= 120 else 100
        if (i + 1) % step == 0 or (i + 1) == len(need_download):
            print(
                f"  … {i + 1}/{len(need_download)}  downloaded={n_dl}  skipped_fresh={n_skip}  → {out_dir}",
                flush=True,
            )

    print(
        f"Daily refill: {n_dl} downloaded, {n_skip} skipped (already >= "
        f"{min_date.isoformat() if min_date else 'n/a'})",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Download daily bars: full Nasdaq list or Nasdaq-100 (QQQ index)."
    )
    ap.add_argument(
        "--universe",
        choices=("listed", "nasdaq100"),
        default="nasdaq100",
        help=(
            "listed = all Nasdaq-listed symbols (nasdaqlisted.txt). "
            "nasdaq100 = Nasdaq-100 / QQQ constituents (~100 names, Wikipedia)."
        ),
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for CSVs (default: …/nasdaq_listed or …/nasdaq100 by universe)",
    )
    ap.add_argument(
        "--months",
        type=float,
        default=12.0,
        metavar="N",
        help="Calendar months of history ending now (default: 12)",
    )
    ap.add_argument(
        "--max-symbols",
        type=int,
        default=0,
        metavar="N",
        help="If > 0, only process the first N symbols (after sort)",
    )
    ap.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip symbols whose CSV already exists in output-dir",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.12,
        metavar="SEC",
        help="Pause between API calls to reduce 429s (default: 0.12)",
    )
    args = ap.parse_args()

    _load_dotenv()
    if not os.getenv("MASSIVE_API_KEY", "").strip():
        print(
            "ERROR: MASSIVE_API_KEY not set. Add it to .env at the repo root.",
            file=sys.stderr,
        )
        sys.exit(1)

    sys.path.insert(0, str(_repo_root() / "src"))
    from exchange.massive_rest import MassiveRestClient

    start_ms, end_ms = _compute_start_end_ms(float(args.months))

    out_dir = args.output_dir
    if out_dir is None:
        out_dir = (
            Path("data/backtests/nasdaq100")
            if args.universe == "nasdaq100"
            else Path("data/backtests/nasdaq_listed")
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.universe == "nasdaq100":
        print("Fetching Nasdaq-100 (QQQ index) constituents …", flush=True)
        symbols = fetch_nasdaq100_symbols()
    else:
        print("Fetching Nasdaq-listed symbol directory …", flush=True)
        symbols = fetch_nasdaq_symbols()
    if args.max_symbols > 0:
        symbols = symbols[: args.max_symbols]
    print(f"Universe={args.universe}  symbols={len(symbols)}  out_dir={out_dir}", flush=True)

    client = MassiveRestClient({})
    ok = skip = fail = 0
    t0 = time.perf_counter()

    for i, sym in enumerate(symbols):
        path = out_dir / f"{sym}.csv"
        if args.skip_existing and path.is_file():
            skip += 1
            continue
        try:
            df = client.fetch_ohlcv(
                sym,
                "1d",
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                limit=500,
            )
            daily = daily_df_to_csv(df)
            if daily.empty:
                fail += 1
            else:
                daily.to_csv(path, index=False)
                ok += 1
        except Exception as e:
            print(f"  FAIL {sym}: {e}", flush=True)
            fail += 1

        if args.sleep > 0:
            time.sleep(args.sleep)

        step = 25 if len(symbols) <= 120 else 100
        if (i + 1) % step == 0 or (i + 1) == len(symbols):
            elapsed = time.perf_counter() - t0
            print(
                f"  … {i + 1}/{len(symbols)}  ok={ok} skip={skip} fail={fail}  "
                f"{elapsed:.0f}s elapsed",
                flush=True,
            )

    elapsed = time.perf_counter() - t0
    print(
        f"\nDone in {elapsed:.1f}s: wrote {ok} CSVs, skipped {skip}, failed/empty {fail} "
        f"→ {out_dir.resolve()}"
    )


if __name__ == "__main__":
    main()
