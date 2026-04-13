"""Grid search over LazySwing entry persistence (SwingParty).

Uses the scorer and strategy from the given YAML unchanged; only sweeps:
  - entry_persist_max_bars: 0 (off), 4, 8, 16, 24
  - entry_persist_max_price_drift: 1% (0.01) or 0.5% (0.005)

5 x 2 = 10 backtests per config file.

Usage:
    source .venv/bin/activate
    PYTHONPATH=src python3 run_grid_search.py config/strategies/swing_party/dev.yaml
"""

import copy
import html
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from multi_asset_controller import MultiAssetController

# Resampled-bar patience after ST flip (0 = off, immediate on flip).
ENTRY_PERSIST_BARS_GRID = (0, 4, 8, 16, 24)

# Max |price - flip_ref| / flip_ref on resampled closes while waiting to enter.
ENTRY_PERSIST_DRIFT_GRID = (
    0.01,   # 1%
    0.005,  # 0.5%
)


def run_one(
    base_config: dict,
    entry_persist_max_bars: int,
    entry_persist_max_price_drift: float,
) -> dict:
    """Run a single backtest; scorer and other strategy keys come from base_config."""
    config = copy.deepcopy(base_config)
    strategy = config.setdefault("strategy", {})
    strategy["entry_persist_max_bars"] = int(entry_persist_max_bars)
    strategy["entry_persist_max_price_drift"] = float(entry_persist_max_price_drift)

    controller = MultiAssetController(config, output_dir="reports/grid_search")
    result = controller.run()

    ev = result.eviction_stats
    scorer = strategy.get("scorer", {})
    return {
        "scorer_type": scorer.get("type", ""),
        "scorer_params": scorer.get("params", {}),
        "entry_persist_max_bars": int(entry_persist_max_bars),
        "entry_persist_max_price_drift": float(entry_persist_max_price_drift),
        "total_return_pct": round(result.total_return_pct, 2),
        "final_value": round(result.final_value, 2),
        "n_evictions": ev.get("n_events", 0),
        "n_resolved": ev.get("n_resolved", 0),
        "n_correct": ev.get("n_correct", 0),
        "accuracy": ev.get("accuracy", 0),
        "entered_compound_pnl": ev.get("entered_compound_pnl", 0),
        "evicted_compound_pnl": ev.get("evicted_compound_pnl", 0),
        "net_compound_pnl": ev.get("net_compound_pnl", 0),
        "events": ev.get("events", []),
    }


def _safe_filename_fragment(name: str, max_len: int = 80) -> str:
    out = "".join(c if c.isalnum() or c in "._-" else "_" for c in name.strip())
    return out[:max_len] or "grid"


def write_grid_search_html_report(
    results: list[dict],
    out_path: Path,
    *,
    config_path: str,
    backtest: dict,
    strategy: dict,
    scorer_label: str,
) -> None:
    """Write an HTML file with one row per persist/drift combo, sorted by total return (desc)."""
    rows = sorted(results, key=lambda r: r["total_return_pct"], reverse=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gen_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    bt_name = html.escape(str(backtest.get("name", "")))
    period = html.escape(
        f"{backtest.get('start_date', '')} → {backtest.get('end_date', '')}"
    )
    assets = html.escape(", ".join(strategy.get("assets", [])))
    cfg_esc = html.escape(config_path)

    body_rows = []
    for i, r in enumerate(rows, start=1):
        drift_pct = r["entry_persist_max_price_drift"] * 100
        body_rows.append(
            "<tr>"
            f"<td>{i}</td>"
            f"<td>{r['entry_persist_max_bars']}</td>"
            f"<td>{drift_pct:.2f}%</td>"
            f"<td class='num'>{r['total_return_pct']:+.2f}</td>"
            f"<td class='num'>{r['final_value']:,.2f}</td>"
            f"<td class='num'>{r['n_evictions']}</td>"
            f"<td class='num'>{r['n_correct']}</td>"
            f"<td class='num'>{r['accuracy']:.1f}</td>"
            f"<td class='num'>{r['entered_compound_pnl']:+.2f}</td>"
            f"<td class='num'>{r['evicted_compound_pnl']:+.2f}</td>"
            f"<td class='num'>{r['net_compound_pnl']:+.2f}</td>"
            "</tr>"
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>SwingParty persist/drift grid — {bt_name}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #1a1a1a; }}
    h1 {{ font-size: 1.25rem; }}
    .meta {{ color: #444; font-size: 0.9rem; margin-bottom: 1rem; }}
    table {{ border-collapse: collapse; width: 100%; max-width: 1100px; }}
    th, td {{ border: 1px solid #ccc; padding: 0.45rem 0.6rem; text-align: left; }}
    th {{ background: #f4f4f4; font-weight: 600; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    caption {{ text-align: left; font-weight: 600; margin-bottom: 0.5rem; }}
  </style>
</head>
<body>
  <h1>SwingParty grid: entry persistence</h1>
  <div class="meta">
    <div><strong>Backtest:</strong> {bt_name}</div>
    <div><strong>Period:</strong> {period}</div>
    <div><strong>Config:</strong> {cfg_esc}</div>
    <div><strong>Scorer:</strong> {html.escape(scorer_label)}</div>
    <div><strong>Assets:</strong> {assets}</div>
    <div><strong>Generated:</strong> {html.escape(gen_at)}</div>
  </div>
  <p>Rows are sorted by <strong>total return %</strong> (highest first). Each row is one
  <code>entry_persist_max_bars</code> × <code>entry_persist_max_price_drift</code> combination.</p>
  <table>
    <caption>Grid results (by total return)</caption>
    <thead>
      <tr>
        <th>#</th>
        <th>persist bars</th>
        <th>price drift</th>
        <th>Total return %</th>
        <th>Final value</th>
        <th>Evictions</th>
        <th>Correct</th>
        <th>Accuracy %</th>
        <th>Entered PnL %</th>
        <th>Evicted PnL %</th>
        <th>Net eviction PnL %</th>
      </tr>
    </thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table>
</body>
</html>
"""
    out_path.write_text(html_doc, encoding="utf-8")


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_grid_search.py <config.yaml>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        base_config = yaml.safe_load(f)

    scorer = base_config.get("strategy", {}).get("scorer", {})
    scorer_label = f"{scorer.get('type', '?')}({scorer.get('params', {})})"

    total = len(ENTRY_PERSIST_BARS_GRID) * len(ENTRY_PERSIST_DRIFT_GRID)

    print("SwingParty grid (entry persistence only)")
    print(f"  Config: {sys.argv[1]}")
    print(f"  Scorer (from YAML): {scorer_label}")
    print(f"  Assets: {', '.join(base_config['strategy']['assets'])}")
    print(f"  Max positions: {base_config['strategy']['max_positions']}")
    print(f"  Period: {base_config['backtest']['start_date']} to {base_config['backtest']['end_date']}")
    print(f"  Grid: entry_persist_max_bars {list(ENTRY_PERSIST_BARS_GRID)}")
    print(f"        x entry_persist_max_price_drift {[f'{d*100:g}%' for d in ENTRY_PERSIST_DRIFT_GRID]}")
    print(f"  Total runs: {total}")
    print()

    results = []
    idx = 0

    for drift in ENTRY_PERSIST_DRIFT_GRID:
        for ep in ENTRY_PERSIST_BARS_GRID:
            idx += 1
            label = f"persist_bars={ep} drift={drift*100:g}%"
            print(f"[{idx}/{total}] {label} ...", end=" ", flush=True)

            t0 = time.time()
            try:
                result = run_one(base_config, ep, drift)
                elapsed = time.time() - t0
                print(f"done ({elapsed:.1f}s) | "
                      f"Return: {result['total_return_pct']:+.2f}% | "
                      f"Evictions: {result['n_evictions']} | "
                      f"Net eviction PnL: {result['net_compound_pnl']:+.2f}%")
                results.append(result)
            except Exception as e:
                elapsed = time.time() - t0
                print(f"FAILED ({elapsed:.1f}s): {e}")

    print("\n" + "=" * 100)
    print(
        f"{'persist':>7} {'drift':>8} {'Return%':>10} {'Evict':>7} "
        f"{'OK':>5} {'Acc%':>7} {'EntPnL%':>9} {'EvPnL%':>9} {'NetPnL%':>9}"
    )
    print("-" * 100)

    results.sort(key=lambda r: r["total_return_pct"], reverse=True)

    for r in results:
        drift_pct = r["entry_persist_max_price_drift"] * 100
        print(
            f"{r['entry_persist_max_bars']:>7} {drift_pct:>7.2f}% "
            f"{r['total_return_pct']:>+10.2f} {r['n_evictions']:>7} "
            f"{r['n_correct']:>5} {r['accuracy']:>6.1f}% "
            f"{r['entered_compound_pnl']:>+9.2f} {r['evicted_compound_pnl']:>+9.2f} "
            f"{r['net_compound_pnl']:>+9.2f}"
        )

    print("=" * 100)

    if results:
        best = results[0]
        print(
            f"\nBest by total return: persist={best['entry_persist_max_bars']} "
            f"drift={best['entry_persist_max_price_drift']*100:.2f}% "
            f"-> {best['total_return_pct']:+.2f}% return, "
            f"net eviction PnL {best['net_compound_pnl']:+.2f}%"
        )
        if best["events"]:
            print("\n  Eviction details (best run):")
            for ev in best["events"]:
                marker = "+" if ev["diff_pct"] > 0 else "-"
                print(
                    f"    {ev['date']}: evicted {ev['evicted']} ({ev['evicted_ret_pct']:+.2f}%) "
                    f"-> entered {ev['entered']} ({ev['entered_ret_pct']:+.2f}%) "
                    f"[{marker}{abs(ev['diff_pct']):.2f}%]"
                )

        stem = _safe_filename_fragment(str(base_config.get("backtest", {}).get("name", "grid")))
        report_path = Path("reports/grid_search") / f"{stem}_persist_drift_grid.html"
        write_grid_search_html_report(
            results,
            report_path,
            config_path=sys.argv[1],
            backtest=base_config.get("backtest", {}),
            strategy=base_config.get("strategy", {}),
            scorer_label=scorer_label,
        )
        print(f"\nHTML report: {report_path}")


if __name__ == "__main__":
    main()
