#!/usr/bin/env python3
"""Weekly Nasdaq-100 scoring simulation: decile ranks → SwingParty backtests → results.md.

Reads daily CSVs (e.g. ``data/backtests/nasdaq100``), picks **N** equity weeks (**W+1**) either
evenly spaced over history (default) or **exactly** the folder names under
``--fixed-w1-weeks-from-dir`` (reuse a prior run's week list). Scoring uses **W** = the calendar
week immediately before each W+1 Monday–Friday (missing W weekdays are filled **in memory** from
the prior session for scoring only), then top three decile bins, 5m download for W+1 plus warmup,
``MultiAssetController`` for max_positions 3/4/5, ``results.md``.

Run from repo root::

  PYTHONPATH=src python run_weekly_screener.py --n-weeks 8 --scoring momentum --provider massive

  # Override LazySwing entry persistence (else taken from ``--template-yaml``):
  PYTHONPATH=src python run_weekly_screener.py --n-weeks 11 --scoring momentum --provider massive \\
    --output-root data/backtests/nasdaq-sim-N11-massive-persist4-drift1pct \\
    --entry-persist-max-bars 4 --entry-persist-max-price-drift 0.01

Conventions: **W+1** is each simulation week (Mon–Fri). **W** is always the prior calendar week;
scores use daily data through ``end_W`` after optional OHLCV fill. Initial cash is always $1000
for generated configs.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

# Repo root = directory containing this script
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

from weekly_screener_core import (  # noqa: E402
    RANGE_EXPANSION_MIN_START_POS,
    SCORING_METHOD_CHOICES,
    SHOCK_PRIOR_WEEKS,
    WeekWindow,
    assign_deciles_and_top_groups,
    compound_returns,
    count_merged_sessions_before_w_monday,
    enumerate_simulation_week_windows,
    fill_calendar_week_ohlcv,
    list_w1_simulation_week_slugs,
    load_daily_frames,
    master_trading_days,
    mean_score_for_symbols,
    sample_evenly_spaced_indices,
    score_universe,
    symbols_in_bins,
    week_window_from_w1_simulation_slug,
)


def _min_merged_sessions_before_w_monday_for_method(method: str) -> int | None:
    """Minimum merged daily sessions strictly before W's Monday (same rule as rotation)."""
    if method == "range_expansion":
        return RANGE_EXPANSION_MIN_START_POS
    if method == "shock_vol_roc":
        return SHOCK_PRIOR_WEEKS * 5
    return None


def _load_template(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _deep_merge_backtest_config(
    template: dict,
    *,
    name: str,
    start_date: str,
    end_date: str,
    initial_cash: float,
) -> dict:
    cfg = copy.deepcopy(template)
    cfg["backtest"] = {
        **cfg.get("backtest", {}),
        "name": name,
        "version": cfg.get("backtest", {}).get("version", "v1"),
        "initial_cash": initial_cash,
        "start_date": start_date,
        "end_date": end_date,
    }
    return cfg


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _download_union(
    symbols: list[str],
    range_start: str,
    end_excl: str,
    ohlcv_dir: Path,
    file_tag: str,
    provider: str,
    *,
    alpaca_feed: str,
    databento_dataset: str,
    reuse_downloads: bool = False,
) -> set[str]:
    """Download one merged CSV per symbol; return set of symbols written successfully.

    When ``reuse_downloads`` is True, skip a symbol only if its CSV already exists and is
    non-empty; otherwise fetch (so missing files are still downloaded).
    """
    import download_swing_party_day as dsp

    ok: set[str] = set()
    ohlcv_dir.mkdir(parents=True, exist_ok=True)

    pending: list[str] = []
    for sym in symbols:
        fname = f"{sym}-5m-{file_tag}.csv"
        out = ohlcv_dir / fname
        if reuse_downloads and out.is_file() and out.stat().st_size > 0:
            ok.add(sym)
        else:
            pending.append(sym)

    if not pending:
        return ok

    db_client = None
    massive_key: str | None = None
    if provider == "databento":
        import databento as db

        key = os.environ.get("DATABENTO_API_KEY", "").strip()
        if not key:
            print("ERROR: DATABENTO_API_KEY not set.", file=sys.stderr)
            sys.exit(1)
        db_client = db.Historical(key)
    elif provider == "alpaca":
        dsp._load_alpaca_creds()
    elif provider == "massive":
        massive_key = dsp._load_massive_key()

    for sym in pending:
        fname = f"{sym}-5m-{file_tag}.csv"
        out = ohlcv_dir / fname
        if provider == "alpaca":
            if dsp.download_alpaca_5m_range(
                sym, range_start, end_excl, out, feed=alpaca_feed
            ):
                ok.add(sym)
        elif provider == "massive":
            if dsp.download_massive_5m_range(
                sym, range_start, end_excl, out, api_key=massive_key
            ):
                ok.add(sym)
        else:
            if dsp.download_databento_5m_range(
                db_client,
                sym,
                range_start,
                end_excl,
                out,
                dataset=databento_dataset,
            ):
                ok.add(sym)
    return ok


def _warmup_range_start(template_strategy: dict, sim_start: str) -> tuple[str, int]:
    """Return (range_start, n_warmup_trading_days) for 5m download."""
    from strategies.warmup_calendar import warmup_range_start_day, warmup_trading_days_from_strategy

    n = warmup_trading_days_from_strategy(template_strategy)
    range_start = warmup_range_start_day(sim_start, n)
    return range_start, n


def _build_strategy_yaml(
    template: dict,
    *,
    assets: list[str],
    max_positions: int,
    data_dir: Path,
    file_pattern: str,
) -> dict:
    cfg = copy.deepcopy(template)
    st = cfg["strategy"]
    st["max_positions"] = max_positions
    st["assets"] = list(assets)
    cfg["data_source"] = copy.deepcopy(template["data_source"])
    cfg["data_source"]["params"] = {
        **cfg["data_source"].get("params", {}),
        "data_dir": str(data_dir.resolve()),
        "file_pattern": file_pattern,
    }
    return cfg


def _run_one_backtest(config: dict, output_dir: Path):
    from multi_asset_controller import MultiAssetController

    output_dir.mkdir(parents=True, exist_ok=True)
    ctrl = MultiAssetController(config, output_dir=str(output_dir))
    return ctrl.run()


def _format_mean_score_cell(method: str, mean_score: float | None) -> str:
    if mean_score is None:
        return "—"
    if method == "momentum":
        return f"{mean_score * 100:.2f}%"
    if method == "relative_volume":
        return f"{mean_score:.3f}×"
    if method == "natr7":
        return f"{mean_score:.2f}%"
    if method == "atr_roc5":
        return f"{mean_score * 100:.2f}%"
    if method == "atr_vwap_dev":
        return f"{mean_score * 100:.2f}%"
    if method == "bb_pctb":
        return f"{mean_score:.3f}"
    if method == "shock_vol_roc":
        return f"{mean_score:.4f}"
    if method == "roc_acceleration":
        return f"{mean_score * 100:.2f}%"
    if method == "range_expansion":
        return f"{mean_score:.3f}"
    return f"{mean_score:.6g}"


def _write_results_md(
    path: Path,
    *,
    method: str,
    rows_by_group: list[list[dict]],
    group_labels: list[str],
    summary_lines: list[str],
) -> None:
    if method == "momentum":
        score_col = "Avg score (W) — mean \\|W ret\\|"
        score_expl = (
            "For momentum it is the mean of each stock’s **absolute** weekly return in W "
            "(fractional return shown as % in the table)."
        )
        method_def = (
            "**Momentum** — |close_last/close_first − 1| over W from daily data. "
            "Deciles: equal-count bins over the full universe."
        )
    elif method == "relative_volume":
        score_col = "Avg score (W) — mean rel. vol"
        score_expl = (
            "For relative volume it is the mean of (mean volume in W) / (mean volume in prior 21 sessions)."
        )
        method_def = (
            "**Relative volume** — mean(W volume) / mean(prior-21 volume). "
            "Deciles: equal-count bins over the full universe."
        )
    elif method == "natr7":
        score_col = "Avg score (W) — mean NATR %"
        score_expl = (
            "For **natr7** it is the mean **(ATR(7)/close)×100** at Friday close of W (Wilder ATR, "
            "full universe)."
        )
        method_def = (
            "**natr7** — Normalized ATR: Wilder **ATR(7)** at end of W divided by **close** on that "
            "day, ×100 (percent). Same decile bucketing as momentum (full universe, ~equal count per bin)."
        )
    elif method == "atr_roc5":
        score_col = "Avg score (W) — mean \\|W ROC\\|"
        score_expl = (
            "For **atr_roc5** it is the mean **absolute** 5-day return over W among symbols that "
            "passed the ATR filter (second-stage score only)."
        )
        method_def = (
            "**atr_roc5** — Cross-section: keep the top `--atr-keep-top` fraction by "
            "normalized ATR(14) at end of W, then rank survivors by **absolute** weekly ROC. "
            "Deciles apply only to survivors (~equal count per bin among them)."
        )
    elif method == "atr_vwap_dev":
        score_col = "Avg score (W) — mean VWAP dev"
        score_expl = (
            "For **atr_vwap_dev** it is the mean **close vs weekly VWAP** deviation (fraction) "
            "among survivors after the ATR filter."
        )
        method_def = (
            "**atr_vwap_dev** — Same ATR filter as atr_roc5; second stage ranks by "
            "(Friday close / weekly VWAP) − 1 with weekly VWAP from daily typical "
            "(H+L+C)/3 weighted by volume over W. Deciles among survivors only."
        )
    elif method == "bb_pctb":
        score_col = "Avg score (W) — mean %B"
        score_expl = (
            "For **bb_pctb** it is the mean Bollinger **%B** at Friday close: position of close "
            "within the 20-day, 2σ bands (0 = lower band, 1 = upper band; can exceed [0,1] if "
            "price is outside the bands)."
        )
        method_def = (
            "**bb_pctb** — Bollinger Band %B at end of W: BB(20, 2σ) on **daily** closes using "
            "the 20 trading days ending at ``end_W``. Deciles: equal-count bins over the full universe."
        )
    elif method == "shock_vol_roc":
        score_col = "Avg score (W) — mean shock"
        score_expl = (
            "For **shock_vol_roc** it is the mean of (5-day total volume / mean of 10 prior weekly "
            "5-day volume totals) × **|** 5-day ROC **|** over W."
        )
        method_def = (
            "**shock_vol_roc** — (vol_W_sum / mean(vol_5d summed over each of prior 10 weeks)) × "
            "|close_end/close_start − 1|. Rewards large **up or down** moves with abnormal volume. "
            "Full universe deciles."
        )
    elif method == "roc_acceleration":
        score_col = "Avg score (W) — mean \\|ΔROC\\|"
        score_expl = (
            "For **roc_acceleration** it is the mean **|** current 5-day ROC − prior 5-day ROC **|** "
            "(fractional; table shows mean as %)."
        )
        method_def = (
            "**roc_acceleration** — |ROC(W) − ROC(previous week)|. Large positive or negative "
            "acceleration both rank high. Full universe deciles."
        )
    elif method == "range_expansion":
        score_col = "Avg score (W) — mean extremity"
        score_expl = (
            "For **range_expansion** it is the mean **close extremity** |2×stoch−1| among symbols "
            "that passed the range-ratio filter (second stage only)."
        )
        method_def = (
            "**range_expansion** — Stage 1: rank by (week high − week low) / mean daily ATR(14) over "
            "the 70 sessions before W. Keep top `--range-expansion-keep-top`. Stage 2: rank survivors "
            "by |2×stoch−1| with stoch = (Friday close − week low)/(week high − week low), so closes "
            "near the week **high or low** score highest. Deciles among survivors only."
        )
    else:
        score_col = "Avg score (W)"
        score_expl = "Mean of the scoring metric over stocks in the row."
        method_def = "Deciles are rank-based (~equal count per bin)."

    lines = [
        f"# Nasdaq scoring simulation ({method})",
        "",
        "Generated by `run_weekly_screener.py`.",
        "",
        "Tickers are the basenames of `*.csv` files in `--daily-dir` (e.g. real Nasdaq-100 "
        "symbols under `data/backtests/nasdaq100`). Names like `S00`… are only from ad-hoc test fixtures.",
        "",
        "**How to read the table (two different weeks):**",
        "",
        "- **Avg score (W)** — Mean of the **scoring metric** over **week W only**, using **daily** "
        "OHLC (same inputs used to rank into deciles). " + score_expl + " It is **not** the SwingParty result.",
        "",
        "- **PF ret 3 / 4 / 5** — **Strategy** total return % over **simulation week W+1** "
        "(5m backtest, `max_positions` = 3/4/5). Same ticker list as the row, **next** calendar week. "
        "These can be **higher or lower** than the score column: different week, trading, costs, "
        "and slot logic—not a bug.",
        "",
        method_def,
        "",
    ]
    for label, rows in zip(group_labels, rows_by_group):
        lines.append(f"## {label}")
        lines.append("")
        lines.append(
            f"| Simulation week (W+1) | {score_col} | Stocks | "
            "PF ret 3 (W+1) | PF ret 4 (W+1) | PF ret 5 (W+1) | Acc 3 | Acc 4 | Acc 5 |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in rows:
            ms = _format_mean_score_cell(method, r.get("mean_score"))
            lines.append(
                f"| {r['w1_range']} | {ms} | {r['stocks']} | "
                f"{r['ret3']:.4f} | {r['ret4']:.4f} | {r['ret5']:.4f} | "
                f"{r['acc3']:.4f} | {r['acc4']:.4f} | {r['acc5']:.4f} |"
            )
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    for s in summary_lines:
        lines.append(s)
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Weekly Nasdaq scoring → deciles → SwingParty backtests.")
    ap.add_argument("--n-weeks", type=int, required=True, metavar="N", help="Sample weeks (evenly spaced).")
    ap.add_argument(
        "--scoring",
        choices=SCORING_METHOD_CHOICES,
        required=True,
        help="Scoring method.",
    )

    def _atr_keep_top_type(s: str) -> float:
        v = float(s)
        if not (0.0 < v <= 1.0):
            raise argparse.ArgumentTypeError("must be in (0, 1]")
        return v

    ap.add_argument(
        "--atr-keep-top",
        type=_atr_keep_top_type,
        default=0.35,
        metavar="F",
        help="For atr_roc5 / atr_vwap_dev: fraction of universe to keep after ranking by "
        "normalized ATR(14) (default 0.35 = top 35%%).",
    )
    ap.add_argument(
        "--range-expansion-keep-top",
        type=_atr_keep_top_type,
        default=0.2,
        metavar="F",
        help="For range_expansion: fraction of universe to keep after ranking by range-expansion "
        "ratio (default 0.2 = top 20%%).",
    )
    ap.add_argument(
        "--daily-dir",
        type=Path,
        default=REPO_ROOT / "data" / "backtests" / "nasdaq100",
        help="Directory of daily {SYM}.csv files",
    )
    ap.add_argument(
        "--template-yaml",
        type=Path,
        default=REPO_ROOT / "config" / "strategies" / "swing_party" / "apr9-movers.yaml",
        help="SwingParty template (merged YAML).",
    )
    ap.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "data" / "backtests",
        help="Parent for nasdaq-scoring-simulation-{method}/…",
    )
    ap.add_argument(
        "--fixed-w1-weeks-from-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Use exact W+1 simulation weeks from child folder names YYYY-MM-DD_YYYY-MM-DD "
            "(e.g. data/.../nasdaq-scoring-simulation-momentum). Order is lexicographic sort. "
            "Number of folders must equal --n-weeks. Scoring W is always the calendar week before "
            "each W+1 (same as rotation); OHLC fill applies for missing W days."
        ),
    )
    ap.add_argument(
        "--provider",
        choices=("massive", "alpaca", "databento"),
        default="massive",
        help="5m data provider (real market data only; no synthetic bars).",
    )
    ap.add_argument("--alpaca-feed", choices=("iex", "sip"), default="iex")
    ap.add_argument("--databento-dataset", default="XNAS.ITCH")
    ap.add_argument(
        "--reuse-downloads",
        action="store_true",
        help="Per symbol: skip download only when that symbol's CSV already exists and is non-empty; "
        "still fetch missing files (no synthetic data).",
    )
    ap.add_argument(
        "--skip-backtests",
        action="store_true",
        help="Only write YAML configs and optionally download; no MultiAssetController.",
    )
    ap.add_argument(
        "--entry-persist-max-bars",
        type=int,
        default=None,
        metavar="N",
        help="Set strategy.entry_persist_max_bars on the SwingParty template (omit to use YAML only).",
    )
    ap.add_argument(
        "--entry-persist-max-price-drift",
        type=float,
        default=None,
        metavar="FRACTION",
        help="Set strategy.entry_persist_max_price_drift (e.g. 0.01 for 1%%); omit to use YAML only.",
    )
    args = ap.parse_args()

    template = _load_template(args.template_yaml)
    template_strategy = template["strategy"]
    if args.entry_persist_max_bars is not None:
        template_strategy["entry_persist_max_bars"] = args.entry_persist_max_bars
    if args.entry_persist_max_price_drift is not None:
        template_strategy["entry_persist_max_price_drift"] = args.entry_persist_max_price_drift

    frames = load_daily_frames(args.daily_dir)
    if not frames:
        print(f"No daily CSVs in {args.daily_dir}", file=sys.stderr)
        sys.exit(1)

    _min_merged = _min_merged_sessions_before_w_monday_for_method(args.scoring)
    need_merged = max(21, _min_merged if _min_merged is not None else 21)

    if args.fixed_w1_weeks_from_dir is not None:
        parent = args.fixed_w1_weeks_from_dir.expanduser().resolve()
        try:
            slugs = list_w1_simulation_week_slugs(parent)
        except ValueError as e:
            raise SystemExit(str(e)) from e
        if len(slugs) != args.n_weeks:
            print(
                f"--fixed-w1-weeks-from-dir: found {len(slugs)} week folders in {parent}, "
                f"expected --n-weeks {args.n_weeks}.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            picked = [week_window_from_w1_simulation_slug(s, frames) for s in slugs]
        except ValueError as e:
            raise SystemExit(str(e)) from e
        for ww in picked:
            n = count_merged_sessions_before_w_monday(frames, ww.start_w)
            if n < need_merged:
                raise SystemExit(
                    f"Insufficient merged history before W {ww.start_w} "
                    f"(sim week {ww.w1_dates[0]}): {n} sessions < {need_merged} for "
                    f"scoring={args.scoring!r}."
                )
    else:
        windows = enumerate_simulation_week_windows(
            frames,
            min_prior_trading_days=21,
            min_merged_sessions_before_w_monday=_min_merged,
        )
        if not windows:
            print(
                "No simulation weeks: need enough merged daily history before each W Monday "
                "(prior-21 + scorer lookback).",
                file=sys.stderr,
            )
            sys.exit(1)

        idxs = sample_evenly_spaced_indices(len(windows), args.n_weeks)
        picked = [windows[i] for i in idxs]

    method = args.scoring
    out_base = args.output_root / f"nasdaq-scoring-simulation-{method}"
    out_base.mkdir(parents=True, exist_ok=True)
    if args.fixed_w1_weeks_from_dir is not None:
        print(
            f"Using {len(picked)} fixed W+1 weeks from {args.fixed_w1_weeks_from_dir.resolve()}",
            flush=True,
        )

    # Accumulators for results.md: 3 groups × rows with returns
    # We'll store per (method run) one results.md at out_base
    all_rows: list[list[dict]] = [[], [], []]
    group_labels = [
        "Group 1 (highest decile bin)",
        "Group 2 (2nd-highest decile bin)",
        "Group 3 (3rd-highest decile bin)",
    ]
    skipped_weeks: list[str] = []
    gaps: list[str] = []
    completed_scoring_weeks: list[str] = []

    for ww in picked:
        w1_start, w1_end = ww.w1_start, ww.w1_end
        file_tag = f"{w1_start}_{w1_end}"
        # One folder per simulation week W+1 (Mon–Fri), same slug as 5m file_tag
        week_dir = out_base / file_tag
        ohlcv_dir = week_dir / "ohlcv"

        frames_scoring = fill_calendar_week_ohlcv(frames, ww.w_dates)
        scores = score_universe(
            frames_scoring,
            ww,
            method,
            atr_keep_top=args.atr_keep_top,
            range_expansion_keep_top=args.range_expansion_keep_top,
        )
        if len(scores) < 10:
            skipped_weeks.append(f"{file_tag}: fewer than 10 scored symbols ({len(scores)})")
            continue

        dec_series, top_bins = assign_deciles_and_top_groups(scores, top_k_groups=3)
        if len(top_bins) < 1:
            skipped_weeks.append(f"{file_tag}: decile assignment failed")
            continue

        completed_scoring_weeks.append(f"{ww.start_w} → {ww.end_w}")

        bins_high_to_low = sorted(top_bins, reverse=True)
        sym_by_bin = symbols_in_bins(dec_series, top_bins)

        # Ordered list of symbol lists for the three report tables
        group_syms: list[list[str]] = []
        for b in bins_high_to_low:
            group_syms.append(sym_by_bin.get(b, []))
        while len(group_syms) < 3:
            group_syms.append([])

        union_syms = sorted(set().union(*(set(x) for x in group_syms)))

        range_start, _n = _warmup_range_start(template_strategy, w1_start)
        end_excl = (pd.Timestamp(w1_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        _download_union(
            union_syms,
            range_start,
            end_excl,
            ohlcv_dir,
            file_tag,
            args.provider,
            alpaca_feed=args.alpaca_feed,
            databento_dataset=args.databento_dataset,
            reuse_downloads=args.reuse_downloads,
        )

        file_pattern = f"{{symbol}}-5m-{file_tag}.csv"

        # Filter symbols missing OHLCV
        def filt(symbols: list[str]) -> list[str]:
            out = []
            for sym in symbols:
                p = ohlcv_dir / f"{sym}-5m-{file_tag}.csv"
                if p.is_file() and p.stat().st_size > 0:
                    out.append(sym)
                else:
                    gaps.append(f"{file_tag} missing 5m: {sym}")
            return out

        group_syms = [filt(g) for g in group_syms[:3]]
        union_syms = sorted(set().union(*(set(x) for x in group_syms)))

        rets: dict[tuple[int, int], float] = {}  # (group_idx 0..2, max_pos) -> return %

        if not args.skip_backtests:
            for gi, syms in enumerate(group_syms[:3]):
                bin_label = bins_high_to_low[gi] if gi < len(bins_high_to_low) else "na"
                if not syms:
                    for mp in (3, 4, 5):
                        rets[(gi, mp)] = 0.0
                    continue
                dec_dir = week_dir / f"decile_bin_{bin_label}"
                for mp in (3, 4, 5):
                    cfg = _build_strategy_yaml(
                        template,
                        assets=syms,
                        max_positions=mp,
                        data_dir=ohlcv_dir,
                        file_pattern=file_pattern,
                    )
                    cfg = _deep_merge_backtest_config(
                        cfg,
                        name=f"WScrn_{file_tag}_g{gi}_m{mp}",
                        start_date=w1_start,
                        end_date=w1_end,
                        initial_cash=1000.0,
                    )
                    ypath = dec_dir / f"backtest_max{mp}.yaml"
                    _write_yaml(ypath, cfg)
                    try:
                        res = _run_one_backtest(cfg, dec_dir / f"reports_max{mp}")
                        rets[(gi, mp)] = res.total_return_pct
                    except Exception as e:
                        print(f"WARN backtest {file_tag} g{gi} max{mp}: {e}", file=sys.stderr)
                        rets[(gi, mp)] = 0.0
        else:
            for gi, syms in enumerate(group_syms[:3]):
                bin_label = bins_high_to_low[gi] if gi < len(bins_high_to_low) else "na"
                dec_dir = week_dir / f"decile_bin_{bin_label}"
                for mp in (3, 4, 5):
                    cfg = _build_strategy_yaml(
                        template,
                        assets=syms,
                        max_positions=mp,
                        data_dir=ohlcv_dir,
                        file_pattern=file_pattern,
                    )
                    cfg = _deep_merge_backtest_config(
                        cfg,
                        name=f"WScrn_{file_tag}_g{gi}_m{mp}",
                        start_date=w1_start,
                        end_date=w1_end,
                        initial_cash=1000.0,
                    )
                    _write_yaml(dec_dir / f"backtest_max{mp}.yaml", cfg)

        w_range = f"{ww.start_w} → {ww.end_w}"
        w1_range = f"{w1_start} → {w1_end}"

        for gi in range(3):
            syms = group_syms[gi] if gi < len(group_syms) else []
            ms = mean_score_for_symbols(scores, syms)
            stock_str = ", ".join(syms) if syms else "(none)"
            row = {
                "w_range": w_range,
                "w1_range": w1_range,
                "mean_score": ms,
                "stocks": stock_str,
                "ret3": rets.get((gi, 3), 0.0),
                "ret4": rets.get((gi, 4), 0.0),
                "ret5": rets.get((gi, 5), 0.0),
                "acc3": 0.0,
                "acc4": 0.0,
                "acc5": 0.0,
            }
            all_rows[gi].append(row)

    # Compound accumulated returns per group × position count (through each row)
    for gi in range(3):
        r3 = [r["ret3"] for r in all_rows[gi]]
        r4 = [r["ret4"] for r in all_rows[gi]]
        r5 = [r["ret5"] for r in all_rows[gi]]
        for j, r in enumerate(all_rows[gi]):
            r["acc3"] = compound_returns(r3[: j + 1])
            r["acc4"] = compound_returns(r4[: j + 1])
            r["acc5"] = compound_returns(r5[: j + 1])

    # Summary
    summary = [
        f"- Scoring method: `{method}`",
        f"- Sample weeks requested: {args.n_weeks}; windows with rows: {len(all_rows[0])}.",
    ]
    ep = template_strategy.get("entry_persist_max_bars")
    ed = template_strategy.get("entry_persist_max_price_drift")
    if ep is not None or ed is not None:
        summary.append(
            f"- Entry persistence: `entry_persist_max_bars={ep!r}`, "
            f"`entry_persist_max_price_drift={ed!r}` (SwingParty / LazySwing)."
        )
    if method in ("atr_roc5", "atr_vwap_dev"):
        summary.append(
            f"- ATR filter: keep top **{args.atr_keep_top:g}** of universe by normalized ATR(14) "
            "at end of W; deciles on survivors only."
        )
    if method == "range_expansion":
        summary.append(
            f"- Range expansion: keep top **{args.range_expansion_keep_top:g}** by "
            "(week range / mean daily ATR); deciles on survivors by close extremity only."
        )
    if skipped_weeks:
        summary.append("- Skipped: " + "; ".join(skipped_weeks))
    if gaps:
        summary.append("- Data gaps: " + str(len(gaps)) + " notices (see stderr log).")

    # Best / worst accumulated (last row of each group)
    best = None
    worst = None
    for gi in range(3):
        for mp in (3, 4, 5):
            rows = all_rows[gi]
            if not rows:
                continue
            key = {3: "acc3", 4: "acc4", 5: "acc5"}[mp]
            lv = rows[-1][key]
            if best is None or lv > best[0]:
                best = (lv, f"group{gi+1}_max{mp}")
            if worst is None or lv < worst[0]:
                worst = (lv, f"group{gi+1}_max{mp}")

    if best:
        summary.append(f"- Best terminal accumulated return: **{best[1]}** ≈ {best[0]:.4f}%")
    if worst:
        summary.append(f"- Worst terminal accumulated return: **{worst[1]}** ≈ {worst[0]:.4f}%")

    _write_results_md(
        out_base / "results.md",
        method=method,
        rows_by_group=all_rows,
        group_labels=group_labels,
        summary_lines=summary,
    )

    print(f"Wrote {out_base / 'results.md'}")


if __name__ == "__main__":
    main()
