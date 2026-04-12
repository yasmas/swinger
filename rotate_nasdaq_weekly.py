#!/usr/bin/env python3
"""Weekly Nasdaq rotation: refill daily bars → validate last Friday → score → 5m warmup → bot YAML.

By default downloads fresh Nasdaq daily CSVs (Massive) into ``--daily-dir``, then requires every
symbol to have a row for the last Friday before the upcoming equity week. Use
``--bypass-daily-refill`` to skip the daily download and all of those checks (best effort on
whatever CSVs are on disk). ``--dry-run`` skips daily refill and side effects after scoring; with
only ``--dry-run``, last-Friday validation still runs. With ``--dry-run`` and ``--bypass-daily-refill``,
nothing is validated.

Run manually on weekends. See docs/plan-nasdaq-weekly-rotation.md.

  PYTHONPATH=src python rotate_nasdaq_weekly.py --user yasmas --dry-run
  PYTHONPATH=src python rotate_nasdaq_weekly.py --user yasmas --bypass-daily-refill
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import shutil
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent


def _ensure_paths() -> None:
    src = REPO_ROOT / "src"
    s = str(src.resolve())
    if s not in sys.path:
        sys.path.insert(0, s)
    r = str(REPO_ROOT.resolve())
    if r not in sys.path:
        sys.path.insert(0, r)


_ensure_paths()

from download_nasdaq_daily import refill_nasdaq_daily  # noqa: E402
from strategies.warmup_calendar import (  # noqa: E402
    warmup_range_start_day,
    warmup_trading_days_from_strategy,
)
from weekly_screener_core import (  # noqa: E402
    WeekWindow,
    assign_deciles_and_top_groups,
    enumerate_week_windows,
    load_daily_frames,
    master_trading_days,
    score_universe,
    symbols_in_bins,
)
from reporting.reporter import compute_stats  # noqa: E402
from reporting.swing_party_reporter import SwingPartyReporter  # noqa: E402

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")

LEDGER_HEADER = """# Nasdaq Weekly Rotation — Cumulative Performance

| Week | Stocks | Return % | Win Rate | Trades | Long/Short | Capital Start | Capital End | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
"""


def monday_date_tag(d: date) -> str:
    """Short tag e.g. ``apr14-26`` (lowercase month + day + 2-digit year)."""
    m = _MONTHS[d.month - 1]
    y = d.year % 100
    return f"{m}{d.day}-{y:02d}"


def next_monday(today: date) -> date:
    """Next **equity-week** Monday to prepare for: Sat/Sun → immediate Monday; Mon → today; else next Monday."""
    wd = today.weekday()
    if wd == 5:  # Sat
        return today + timedelta(days=2)
    if wd == 6:  # Sun
        return today + timedelta(days=1)
    if wd == 0:
        return today
    return today + timedelta(days=7 - wd)


def last_friday_before_equity_week(next_monday: date) -> date:
    """Friday of the week immediately before the equity week that starts on ``next_monday``."""
    return next_monday - timedelta(days=3)


def _finite_close(x) -> bool:
    try:
        v = float(x)
        return math.isfinite(v)
    except (TypeError, ValueError):
        return False


def verify_last_friday_daily_closes(daily_dir: Path, last_friday: date) -> None:
    """Require every ``*.csv`` under ``daily_dir`` to have a row for ``last_friday`` with a finite close."""
    root = Path(daily_dir)
    files = sorted(root.glob("*.csv"))
    if not files:
        raise SystemExit(f"No daily CSVs in {daily_dir}")
    need = pd.Timestamp(last_friday.isoformat()).normalize()
    missing: list[str] = []
    bad_close: list[str] = []
    for path in files:
        sym = path.stem.upper()
        df = pd.read_csv(path)
        if df.empty or "date" not in df.columns:
            bad_close.append(sym)
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        row = df.loc[df["date"] == need]
        if row.empty:
            missing.append(sym)
            continue
        close = row.iloc[0].get("close")
        if not _finite_close(close):
            bad_close.append(sym)
    parts: list[str] = []
    if missing:
        parts.append(f"missing date {last_friday.isoformat()}: {', '.join(missing[:20])}")
        if len(missing) > 20:
            parts[-1] += f" … (+{len(missing) - 20} more)"
    if bad_close:
        parts.append(f"missing/invalid close on {last_friday.isoformat()}: {', '.join(bad_close[:20])}")
        if len(bad_close) > 20:
            parts[-1] += f" … (+{len(bad_close) - 20} more)"
    if parts:
        raise SystemExit(
            "Cannot find last Friday daily closes in --daily-dir:\n  "
            + "\n  ".join(parts)
        )


def _min_leading_for_scoring(method: str) -> int | None:
    from weekly_screener_core import RANGE_EXPANSION_MIN_START_POS, SHOCK_PRIOR_WEEKS

    if method == "range_expansion":
        return RANGE_EXPANSION_MIN_START_POS
    if method == "shock_vol_roc":
        return SHOCK_PRIOR_WEEKS * 5
    return None


def latest_scoring_week(
    daily_dir: Path,
    *,
    scoring: str,
) -> WeekWindow:
    """Most recent valid Mon–Fri scoring window W with full prior history."""
    frames = load_daily_frames(daily_dir)
    if not frames:
        raise SystemExit(f"No daily CSVs in {daily_dir}")
    dates = master_trading_days(frames)
    min_lead = _min_leading_for_scoring(scoring)
    windows = enumerate_week_windows(
        dates,
        min_leading_index=min_lead,
    )
    if not windows:
        raise SystemExit("No valid scoring week (need merged calendar + prior sessions).")
    return windows[-1]


def pick_group1_symbols(
    frames: dict,
    ww: WeekWindow,
    scoring: str,
) -> list[str]:
    scores = score_universe(frames, ww, scoring)
    if not scores:
        raise SystemExit("No scores produced (check daily data and scoring method).")
    decile_series, top_bins = assign_deciles_and_top_groups(scores, top_k_groups=1)
    if not top_bins:
        return []
    by_bin = symbols_in_bins(decile_series, top_bins)
    top = top_bins[-1]
    return sorted(by_bin.get(top, []))


def _strategy_stub_from_template(template: dict) -> dict:
    """Strategy block from a swing_party backtest YAML (e.g. apr9-movers)."""
    return copy.deepcopy(template.get("strategy", {}))


def _warmup_trading_days(template_strategy: dict) -> int:
    cfg = {"strategy": template_strategy}
    return warmup_trading_days_from_strategy(cfg.get("strategy", {}))


def _exit_win_rate(trades: pd.DataFrame) -> float | None:
    wins = 0
    total = 0
    for _, r in trades.iterrows():
        if str(r["action"]).upper() not in ("SELL", "COVER"):
            continue
        d = r.get("details")
        if not isinstance(d, dict):
            continue
        pnl = d.get("pnl_pct")
        if pnl is None:
            continue
        try:
            v = float(pnl)
        except (TypeError, ValueError):
            continue
        total += 1
        if v > 0:
            wins += 1
    if total == 0:
        return None
    return 100.0 * wins / total


def _long_short_opens(trades: pd.DataFrame) -> tuple[int, int]:
    lo = int((trades["action"] == "BUY").sum())
    so = int((trades["action"] == "SHORT").sum())
    return lo, so


def _filter_trades_week(
    trades: pd.DataFrame,
    week_start: date,
    week_end: date,
) -> pd.DataFrame:
    t0 = pd.Timestamp(week_start.isoformat())
    t1 = pd.Timestamp(week_end.isoformat()) + pd.Timedelta(days=1)
    d = trades["date"]
    return trades[(d >= t0) & (d < t1)].copy()


def _append_ledger_row(
    ledger_path: Path,
    *,
    week_tag: str,
    stocks: str,
    ret_pct: float,
    win_rate: float | None,
    n_trades: int,
    long_short: str,
    cap_start: float,
    cap_end: float,
    sharpe: float,
) -> None:
    wr = f"{win_rate:.1f}%" if win_rate is not None else "—"
    line = (
        f"| {week_tag} | {stocks} | {ret_pct:.2f} | {wr} | {n_trades} | {long_short} | "
        f"${cap_start:,.0f} | ${cap_end:,.0f} | {sharpe:.2f} |\n"
    )
    if not ledger_path.is_file():
        ledger_path.write_text(LEDGER_HEADER + line)
    else:
        with open(ledger_path, "a") as f:
            f.write(line)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _build_backtest_strategy_config(
    *,
    template: dict,
    name: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
    assets: list[str],
    live_data_dir: str,
    file_pattern_literal: str,
    max_positions: int,
) -> dict:
    """Full multi-asset backtest config for SwingPartyReporter / MultiAssetController."""
    out = copy.deepcopy(template)
    out["backtest"] = {
        "name": name,
        "version": str(out.get("backtest", {}).get("version", "v1")),
        "initial_cash": initial_cash,
        "start_date": start_date,
        "end_date": end_date,
    }
    out["data_source"] = {
        "type": "csv_file",
        "parser": "binance_kline",
        "params": {
            "data_dir": live_data_dir,
            "file_pattern": file_pattern_literal,
        },
    }
    strat = copy.deepcopy(out.get("strategy", {}))
    strat["type"] = "swing_party"
    strat["max_positions"] = max_positions
    strat["assets"] = sorted(assets)
    out["strategy"] = strat
    return out


def archive_previous_week(
    *,
    repo: Path,
    user: str,
    prev_tag: str,
    last_week: tuple[date, date],
    strategy_path: Path,
    dry_run: bool,
) -> None:
    """Archive bot yaml, HTML report, trades, zip 5m CSVs, append ledger."""
    user_root = repo / "data" / user
    live = user_root / "nasdaq-live"
    archive = user_root / "nasdaq-archive"
    bot_src = user_root / f"nasdaq-{prev_tag}.yaml"
    if not bot_src.is_file():
        print(f"No prior bot config at {bot_src} — skip archive.", flush=True)
        return

    trades_path = live / "trades.csv"
    if not trades_path.is_file():
        print(f"Warning: no {trades_path} — skip report and ledger.", flush=True)
        if not dry_run:
            archive.mkdir(parents=True, exist_ok=True)
            dest_bot = archive / f"nasdaq-{prev_tag}.yaml"
            shutil.move(str(bot_src), dest_bot)
            print(f"Archived bot yaml → {dest_bot}", flush=True)
        return

    trades_all = pd.read_csv(trades_path)
    trades_all["date"] = pd.to_datetime(trades_all["date"], format="mixed", utc=True).dt.tz_convert(
        None
    )
    trades_all["details"] = trades_all["details"].apply(
        lambda x: json.loads(x) if isinstance(x, str) else x
    )

    week_start, week_end = last_week
    tw = _filter_trades_week(trades_all, week_start, week_end)

    initial_cash = 100000.0
    try:
        bot_cfg = _load_yaml(bot_src)
        initial_cash = float(bot_cfg.get("broker", {}).get("initial_cash", initial_cash))
    except Exception:
        pass

    stats = compute_stats(tw, initial_cash, cost_per_trade_pct=0.05)

    if not strategy_path.is_file():
        print(f"Warning: no strategy file {strategy_path} — skip HTML report.", flush=True)

    backtest_cfg = _load_yaml(strategy_path) if strategy_path.is_file() else None

    if dry_run:
        print(f"[dry-run] Would archive week {prev_tag}, report, zip, ledger.", flush=True)
        return

    archive.mkdir(parents=True, exist_ok=True)
    dest_bot = archive / f"nasdaq-{prev_tag}.yaml"
    shutil.move(str(bot_src), dest_bot)
    print(f"Archived bot yaml → {dest_bot}", flush=True)

    if backtest_cfg is not None:
        rep = SwingPartyReporter(output_dir=str(archive))
        html_name = f"nasdaq-{prev_tag}-report.html"
        rep.generate(
            str(trades_path),
            backtest_cfg,
            strategy_name="swing_party",
            version=prev_tag,
            output_filename=html_name,
        )
        print(f"Wrote HTML report → {archive / html_name}", flush=True)

    assets_list = sorted((backtest_cfg or {}).get("strategy", {}).get("assets", []))
    if not assets_list and not tw.empty:
        assets_list = sorted(tw["symbol"].astype(str).unique().tolist())
    stocks_cell = ",".join(assets_list[:12]) + ("..." if len(assets_list) > 12 else "")
    win_r = _exit_win_rate(tw)
    lo, so = _long_short_opens(tw)
    n_tr = int(stats["num_trades"])
    pv = tw["portfolio_value"].astype(float) if not tw.empty else pd.Series(dtype=float)
    cap_s = float(pv.iloc[0]) if len(pv) else initial_cash
    cap_e = float(pv.iloc[-1]) if len(pv) else initial_cash

    _append_ledger_row(
        user_root / "nasdaq-weekly-summary.md",
        week_tag=prev_tag,
        stocks=stocks_cell,
        ret_pct=float(stats["total_return"]),
        win_rate=win_r,
        n_trades=n_tr,
        long_short=f"{lo}/{so}",
        cap_start=cap_s,
        cap_end=cap_e,
        sharpe=float(stats["sharpe_ratio"]),
    )
    print(f"Appended row → {user_root / 'nasdaq-weekly-summary.md'}", flush=True)

    zip_path = archive / f"nasdaq-{prev_tag}-5m.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if live.is_dir():
            for p in live.rglob("*.csv"):
                if p.name == "trades.csv":
                    continue
                arc = p.relative_to(live)
                zf.write(p, arcname=str(arc))
    print(f"Zipped 5m CSVs → {zip_path}", flush=True)

    for p in live.rglob("*.csv"):
        if p.name == "trades.csv":
            continue
        try:
            p.unlink()
        except OSError:
            pass

    state_p = live / "state.yaml"
    if state_p.is_file():
        state_p.write_text("{}\n")

    dest_trades = archive / f"nasdaq-{prev_tag}-trades.csv"
    shutil.move(str(trades_path), dest_trades)
    print(f"Moved trades → {dest_trades}", flush=True)


def download_massive_batch(
    symbols: list[str],
    range_start: str,
    end_excl: str,
    live_dir: Path,
    file_suffix: str,
) -> int:
    import download_swing_party_day as dsp

    dsp._load_massive_key()
    ok = 0
    live_dir.mkdir(parents=True, exist_ok=True)
    for sym in symbols:
        out = live_dir / f"{sym}-5m-{file_suffix}.csv"
        if dsp.download_massive_5m_range(sym, range_start, end_excl, out):
            ok += 1
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly Nasdaq rotation (scoring → YAML → warmup download).")
    ap.add_argument("--user", required=True, help='Dashboard user folder under data/, e.g. "yasmas"')
    ap.add_argument("--scoring", default="momentum", help="Scoring method (default momentum)")
    ap.add_argument("--max-positions", type=int, default=3, help="SwingParty max_positions (default 3)")
    ap.add_argument("--provider", default="massive", choices=("massive",), help="5m download provider")
    ap.add_argument(
        "--daily-dir",
        type=Path,
        default=REPO_ROOT / "data" / "backtests" / "nasdaq100",
        help="Daily Nasdaq CSVs for scoring",
    )
    ap.add_argument(
        "--daily-universe",
        choices=("nasdaq100", "listed"),
        default="nasdaq100",
        help="Universe for daily refill (must match --daily-dir layout; default nasdaq100)",
    )
    ap.add_argument(
        "--daily-months",
        type=float,
        default=12.0,
        metavar="N",
        help="Months of daily history when refilling (default 12)",
    )
    ap.add_argument(
        "--bypass-daily-refill",
        action="store_true",
        help="Skip Massive daily download, last-Friday validation, and stale-week warning (best effort)",
    )
    ap.add_argument(
        "--force-daily-refill",
        action="store_true",
        help="Re-download every daily CSV from Massive (ignore skip-if-fresh by last date)",
    )
    ap.add_argument(
        "--template-yaml",
        type=Path,
        default=REPO_ROOT / "config" / "strategies" / "swing_party" / "apr9-movers.yaml",
        help="Strategy template (swing_party backtest YAML)",
    )
    ap.add_argument(
        "--bot-template",
        type=Path,
        default=REPO_ROOT / "config" / "bot" / "nasdaq_weekly_bot_template.yaml",
        help="Bot YAML template with __USER__ and __DATE_TAG__ placeholders",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print picks only; no writes or downloads")
    args = ap.parse_args()

    today = date.today()
    nm = next_monday(today)
    last_m = nm - timedelta(days=7)
    date_tag = monday_date_tag(nm)
    prev_tag = monday_date_tag(last_m)

    print(f"next_monday={nm}  date_tag={date_tag}", flush=True)
    print(f"archive week last_monday={last_m}  prev_tag={prev_tag}", flush=True)

    daily_dir = args.daily_dir.resolve()
    last_friday = last_friday_before_equity_week(nm)

    if args.bypass_daily_refill:
        print(
            "--bypass-daily-refill: skipping daily Massive refill and last-Friday / stale-week checks.",
            flush=True,
        )
    elif args.dry_run:
        print("Dry run — skipping daily Massive refill.", flush=True)
    else:
        print(
            f"Refilling daily data: universe={args.daily_universe} → {daily_dir} "
            f"({args.daily_months} months) …",
            flush=True,
        )
        try:
            refill_nasdaq_daily(
                daily_dir,
                universe=args.daily_universe,
                months=args.daily_months,
                min_date=None if args.force_daily_refill else last_friday,
                force_full=args.force_daily_refill,
            )
        except RuntimeError as e:
            raise SystemExit(str(e)) from e

    if not args.bypass_daily_refill:
        print(f"required_last_friday={last_friday.isoformat()} (closes must exist for rotation)", flush=True)
        verify_last_friday_daily_closes(daily_dir, last_friday)

    ww = latest_scoring_week(daily_dir, scoring=args.scoring)
    print(f"Scoring week W: {ww.start_w} .. {ww.end_w}", flush=True)
    if not args.bypass_daily_refill and ww.end_w != last_friday.isoformat():
        print(
            f"WARNING: scoring week ends {ww.end_w}, not the Friday before next equity week "
            f"({last_friday.isoformat()}). enumerate_week_windows needs W and W+1 in the merged "
            "calendar — data may be stale for the week you intend.",
            flush=True,
        )

    frames = load_daily_frames(daily_dir)
    picks = pick_group1_symbols(frames, ww, args.scoring)
    print(f"Group 1 ({len(picks)}): {', '.join(picks)}", flush=True)

    if args.dry_run:
        print("Dry run — exiting before archive / writes.", flush=True)
        return

    template = _load_yaml(args.template_yaml.resolve())
    strategy_stub = _strategy_stub_from_template(template)
    strategy_stub["max_positions"] = args.max_positions
    strategy_stub["assets"] = picks

    user_root = REPO_ROOT / "data" / args.user
    live = user_root / "nasdaq-live"
    archive = user_root / "nasdaq-archive"
    live.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)

    week_start_s = nm.isoformat()
    week_end_s = (nm + timedelta(days=4)).isoformat()
    warmup_td = _warmup_trading_days(strategy_stub)
    range_start = warmup_range_start_day(week_start_s, warmup_td)
    end_excl = (nm + timedelta(days=5)).strftime("%Y-%m-%d")
    file_suffix = f"{range_start}_{week_end_s}"

    strat_name = f"Nasdaq {date_tag}"
    initial_cash = float(template.get("backtest", {}).get("initial_cash", 100000))

    live_rel = f"data/{args.user}/nasdaq-live"
    file_pattern_literal = f"{{symbol}}-5m-{file_suffix}.csv"

    strat_cfg = _build_backtest_strategy_config(
        template=template,
        name=strat_name,
        start_date=week_start_s,
        end_date=week_end_s,
        initial_cash=initial_cash,
        assets=picks,
        live_data_dir=live_rel,
        file_pattern_literal=file_pattern_literal,
        max_positions=args.max_positions,
    )

    prev_strategy = user_root / f"nasdaq-{prev_tag}-strategy.yaml"
    archive_previous_week(
        repo=REPO_ROOT,
        user=args.user,
        prev_tag=prev_tag,
        last_week=(last_m, last_m + timedelta(days=4)),
        strategy_path=prev_strategy,
        dry_run=False,
    )

    strategy_out = user_root / f"nasdaq-{date_tag}-strategy.yaml"
    _write_yaml(strategy_out, strat_cfg)
    print(f"Wrote strategy → {strategy_out}", flush=True)

    if args.provider == "massive":
        n_ok = download_massive_batch(
            picks,
            range_start,
            end_excl,
            live,
            file_suffix,
        )
        print(f"Downloaded {n_ok}/{len(picks)} symbols (Massive).", flush=True)

    bot_raw = args.bot_template.read_text()
    bot_raw = bot_raw.replace("__USER__", args.user).replace("__DATE_TAG__", date_tag)
    bot_out = user_root / f"nasdaq-{date_tag}.yaml"
    bot_out.write_text(bot_raw)
    print(f"Wrote bot config → {bot_out}", flush=True)
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
