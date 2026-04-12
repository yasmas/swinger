#!/usr/bin/env python3
"""Generate SwingParty HTML reports from weekly screener outputs (Group 1, max_positions=3).

For each week folder under a scoring run directory, picks ``decile_bin_<max>`` (highest bin =
Group 1 in results.md), uses ``reports_max3/swing_party/*_g0_m3_*.csv`` and ``backtest_max3.yaml``,
and writes one HTML next to ``results.md`` (not inside week subfolders).

Usage (repo root)::

  PYTHONPATH=src .venv/bin/python scripts/gen_weekly_screener_swing_party_html.py \\
    --scoring-dir data/backtests/nasdaq-sim-N9-massive/nasdaq-scoring-simulation-momentum
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def _batch_and_method(scoring_dir: Path) -> tuple[str, str]:
    """From e.g. .../nasdaq-sim-N11-massive/nasdaq-scoring-simulation-momentum → (N11, momentum)."""
    d = scoring_dir.resolve()
    m_method = re.match(r"nasdaq-scoring-simulation-(.+)", d.name)
    method = m_method.group(1) if m_method else "unknown"
    m_n = re.match(r"nasdaq-sim-N(\d+)-massive", d.parent.name)
    n_tag = f"N{m_n.group(1)}" if m_n else "N?"
    return n_tag, method


def main() -> None:
    ap = argparse.ArgumentParser(description="SwingParty HTML from weekly screener week folders.")
    ap.add_argument(
        "--scoring-dir",
        type=Path,
        required=True,
        help="e.g. data/backtests/nasdaq-sim-N9-massive/nasdaq-scoring-simulation-momentum",
    )
    ap.add_argument(
        "--date-suffix",
        default=None,
        help="YYYYMMDD for filenames (default: today UTC date in local timezone).",
    )
    args = ap.parse_args()

    base = args.scoring_dir
    if not base.is_dir():
        print(f"Not a directory: {base}", file=sys.stderr)
        sys.exit(1)

    short = args.date_suffix or date.today().strftime("%Y%m%d")

    src = str(REPO / "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    from reporting.swing_party_reporter import SwingPartyReporter

    n_batch, method_slug = _batch_and_method(base)
    reporter = SwingPartyReporter(output_dir=str(base.resolve()))
    n_ok = 0
    for week_dir in sorted(base.iterdir()):
        if not week_dir.is_dir():
            continue
        bins: list[tuple[int, Path]] = []
        for p in week_dir.glob("decile_bin_*"):
            try:
                bins.append((int(p.name.rsplit("_", 1)[-1]), p))
            except ValueError:
                continue
        if not bins:
            continue
        _max_id, dec_dir = max(bins, key=lambda x: x[0])
        sp = dec_dir / "reports_max3" / "swing_party"
        if not sp.is_dir():
            print(f"skip {week_dir.name}: no {sp}", file=sys.stderr)
            continue
        logs = sorted(sp.glob("*_g0_m3_*.csv"))
        if not logs:
            logs = sorted(sp.glob("*.csv"))
        if not logs:
            print(f"skip {week_dir.name}: no trade log in {sp}", file=sys.stderr)
            continue
        yml = dec_dir / "backtest_max3.yaml"
        if not yml.is_file():
            print(f"skip {week_dir.name}: missing {yml}", file=sys.stderr)
            continue
        cfg = yaml.safe_load(yml.read_text())
        fn = f"swing-party-{method_slug}-{n_batch}-g1-max3-w{week_dir.name}_{short}.html"
        path = reporter.generate(
            trade_log_path=str(logs[0]),
            config=cfg,
            strategy_name="swing_party",
            version=str(cfg.get("backtest", {}).get("version", "")),
            output_filename=fn,
        )
        print(path)
        n_ok += 1

    print(f"Wrote {n_ok} report(s) under {base}", file=sys.stderr)


if __name__ == "__main__":
    main()
