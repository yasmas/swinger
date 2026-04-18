#!/usr/bin/env python3
"""Grid search SwingParty on ETF mix (see COMBOS). Backtest 2025 only.

Loads data from existing 2024–2025 combined CSVs; uses literal file_pattern so filenames
match. Writes per-run logs under reports/grid-etf-2025/ and prints a sorted summary.

Usage (repo root)::

  PYTHONPATH=src python scripts/grid_search_swingparty_etf_2025.py
"""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]


def _ensure_src() -> None:
    s = str(REPO / "src")
    if s not in sys.path:
        sys.path.insert(0, s)


def exit_win_rate(trade_log: pd.DataFrame) -> tuple[float, int, int, int]:
    """Win rate on SELL/COVER rows that include pnl_pct in details."""
    wins = losses = 0
    for _, r in trade_log.iterrows():
        if r["action"] not in ("SELL", "COVER"):
            continue
        d = r.get("details")
        if not isinstance(d, dict):
            continue
        pnl = d.get("pnl_pct")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    exited = wins + losses
    wr = (wins / exited * 100.0) if exited else float("nan")
    return wr, wins, losses, exited


def main() -> None:
    _ensure_src()
    from multi_asset_controller import MultiAssetController
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    # (Atr, M) — includes ATR15/20 grid, ATR20 M1.0, ATR25 M1.25/M1.0
    combos: list[tuple[int, float]] = [
        (15, 2.0),
        (15, 1.5),
        (15, 1.25),
        (20, 2.0),
        (20, 1.5),
        (20, 1.25),
        (20, 1.0),
        (25, 1.25),
        (25, 1.0),
    ]

    base = {
        "backtest": {
            "name": "SwingParty ETF grid 2025",
            "version": "grid",
            "initial_cash": 10000.0,
            "start_date": "2025-01-02",
            "end_date": "2025-12-31",
            "output_dir": "reports/grid-etf-2025",
        },
        "data_source": {
            "type": "csv_file",
            "parser": "binance_kline",
            "params": {
                "data_dir": "data/backtests/etf-mix-2024-2025",
                "file_pattern": "{symbol}-5m-2024-2025-combined.csv",
            },
        },
        "strategy": {
            "type": "swing_party",
            "max_positions": 3,
            "resample_interval": "1h",
            "supertrend_atr_period": 10,
            "supertrend_multiplier": 2.0,
            "catchup_enabled": True,
            "entry_persist_max_bars": 4,
            "entry_persist_max_price_drift": 0.01,
            "scorer": {
                "type": "volume_breakout",
                "params": {"short_window": 8, "long_window": 100},
            },
            "assets": ["QQQ", "IWM", "EEM", "BNO", "CPER", "SLV"],
        },
    }

    rows: list[dict] = []
    out_root = REPO / "reports" / "grid-etf-2025"
    out_root.mkdir(parents=True, exist_ok=True)

    for atr, m in combos:
        cfg = deepcopy(base)
        tag = f"atr{atr}_m{m:g}".replace(".", "p")
        cfg["strategy"]["supertrend_atr_period"] = atr
        cfg["strategy"]["supertrend_multiplier"] = m
        cfg["backtest"]["version"] = tag
        cfg["backtest"]["output_dir"] = str(out_root / tag)

        print(f"\n=== Running {tag} ===", flush=True)
        ctrl = MultiAssetController(cfg, output_dir=cfg["backtest"]["output_dir"])
        result = ctrl.run()

        log = TradeLogReader.read(result.trade_log_path)
        cost_pct = float(cfg["strategy"].get("cost_per_trade_pct", 0.05))
        stats = compute_stats(log, float(cfg["backtest"]["initial_cash"]), cost_per_trade_pct=cost_pct)
        wr, nw, nl, nexit = exit_win_rate(log)

        rows.append(
            {
                "atr": atr,
                "M": m,
                "tag": tag,
                "return_pct": stats["total_return"],
                "max_dd_pct": stats["max_drawdown"],
                "n_trades": stats["num_trades"],
                "win_rate_pct": wr,
                "n_exits_scored": nexit,
                "wins": nw,
                "losses": nl,
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values("return_pct", ascending=False).reset_index(drop=True)

    lines = [
        "SwingParty ETF grid — 2025-01-02 .. 2025-12-31 (QQQ, IWM, EEM, BNO, CPER, SLV)",
        "Sorted by total return %. Win rate = SELL/COVER rows with pnl_pct in details.",
        "",
        f"{'ATR':>4} {'M':>5}  {'Return%':>10}  {'MaxDD%':>9}  {'Trades':>7}  {'WinRate%':>9}  (scored exits: wins/losses)",
        "-" * 88,
    ]
    for _, r in df.iterrows():
        lines.append(
            f"{int(r['atr']):4d} {r['M']:5.2f}  {r['return_pct']:10.2f}  {r['max_dd_pct']:9.2f}  "
            f"{int(r['n_trades']):7d}  {r['win_rate_pct']:9.1f}  ({int(r['wins'])}/{int(r['losses'])}, n={int(r['n_exits_scored'])})"
        )

    text = "\n".join(lines) + "\n"
    print("\n" + text)
    summary_path = out_root / "grid_summary.txt"
    summary_path.write_text(text, encoding="utf-8")
    print(f"Wrote {summary_path}")


if __name__ == "__main__":
    main()
