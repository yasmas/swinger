#!/usr/bin/env python3
"""Sweep LazySwing ST-flip realised-vol filter variants on ETH 2024 H2.

Stage 1:
  - Baseline HOF config (no filter)
  - Reject mode "hold" at thresholds 40/80/120
  - Reject mode "flat" at thresholds 40/80/120
  - Reject mode "watch" at thresholds 40/80/120 and watch bars 2/4/8

Stage 2:
  - Pick the best non-baseline Stage-1 config by total return
  - Sweep thresholds around its winner
  - If the winner is watch-mode, also sweep nearby watch windows

Uses the ETH HOF 30m LazySwing baseline on 2024-07-01 -> 2024-12-31.
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

OUTPUT_ROOT = REPO / "reports" / "rvol-flip-filter-eth-h2-2024"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

START = "2024-07-01"
END = "2024-12-31"
DATA_FILE = "data/backtests/eth/coinbase/ETH-PERP-INTX-5m-2023-2024.csv"
SYMBOL = "ETH-PERP-INTX"
INITIAL_CASH = 100000.0

BASE_STRATEGY = {
    "resample_interval": "30min",
    "supertrend_atr_period": 25,
    "supertrend_multiplier": 1.75,
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
}

STAGE1_THRESHOLDS = [40.0, 80.0, 120.0]
WATCH_BARS_STAGE1 = [2, 4, 8]


def exit_win_rate(trade_log: pd.DataFrame) -> tuple[float, int, int, int]:
    wins = losses = 0
    for _, row in trade_log.iterrows():
        if row["action"] not in ("SELL", "COVER"):
            continue
        details = row.get("details")
        if not isinstance(details, dict):
            continue
        pnl = details.get("pnl_pct")
        if pnl is None:
            continue
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
    exited = wins + losses
    wr = (wins / exited * 100.0) if exited else float("nan")
    return wr, wins, losses, exited


def build_run_params(mode: str, threshold: float | None = None, watch_bars: int | None = None) -> dict:
    params = dict(BASE_STRATEGY)
    if mode == "baseline":
        params["flip_rvol_enabled"] = False
        return params

    params.update(
        {
            "flip_rvol_enabled": True,
            "flip_rvol_period": 20,
            "flip_rvol_threshold": float(threshold),
            "flip_rvol_annualize": True,
            "flip_rvol_reject_mode": mode,
        }
    )
    if mode == "watch":
        params["flip_rvol_watch_bars"] = int(watch_bars)
    return params


def _run_one(stage: str, tag: str, strat_params: dict) -> dict:
    from config import Config
    from controller import Controller
    from reporting.reporter import compute_stats
    from trade_log import TradeLogReader

    output_dir = OUTPUT_ROOT / stage / tag
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config(
        {
            "backtest": {
                "name": f"LazySwing_RVOL_{stage}_{tag}",
                "version": "rvol-flip",
                "initial_cash": INITIAL_CASH,
                "start_date": START,
                "end_date": END,
            },
            "data_source": {
                "type": "csv_file",
                "parser": "coinbase_intx_kline",
                "params": {
                    "file_path": DATA_FILE,
                    "symbol": SYMBOL,
                },
            },
            "strategies": [
                {
                    "type": "lazy_swing",
                    "params": strat_params,
                }
            ],
        }
    )

    t0 = time.time()
    controller = Controller(cfg, output_dir=str(output_dir))
    results = controller.run()
    elapsed = time.time() - t0

    result = results[0]
    trade_log = TradeLogReader().read(result.trade_log_path)
    stats = compute_stats(trade_log, cfg.initial_cash, 0.05)
    wr, wins, losses, exited = exit_win_rate(trade_log)
    flips = int(stats["num_sells"]) + int(stats["num_covers"])

    out = {
        "stage": stage,
        "tag": tag,
        "final_value": float(result.final_value),
        "total_return_pct": float(stats["total_return"]),
        "sharpe": float(stats["sharpe_ratio"]),
        "max_dd_pct": float(stats["max_drawdown"]),
        "wr_pct": wr,
        "wins": wins,
        "losses": losses,
        "exits_with_pnl": exited,
        "flips": flips,
        "elapsed_sec": round(elapsed, 1),
        "reject_mode": strat_params.get("flip_rvol_reject_mode", "baseline"),
        "rvol_threshold": strat_params.get("flip_rvol_threshold"),
        "watch_bars": strat_params.get("flip_rvol_watch_bars"),
    }
    return out


def run_stage(stage: str, runs: list[tuple[str, dict]]) -> list[dict]:
    print(f"\n=== {stage.upper()} ({len(runs)} runs) ===\n")
    results: list[dict] = []
    max_workers = min(8, len(runs))
    try:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_run_one, stage, tag, params): tag
                for tag, params in runs
            }
            for fut in as_completed(futures):
                tag = futures[fut]
                try:
                    row = fut.result()
                except Exception as exc:
                    print(f"[FAIL] {tag}: {type(exc).__name__}: {exc}")
                    continue
                results.append(row)
                print(fmt_row(row), flush=True)
    except PermissionError:
        print("Process pool unavailable in this environment; falling back to serial execution.\n")
        for tag, params in runs:
            try:
                row = _run_one(stage, tag, params)
            except Exception as exc:
                print(f"[FAIL] {tag}: {type(exc).__name__}: {exc}")
                continue
            results.append(row)
            print(fmt_row(row), flush=True)
    results.sort(key=lambda x: x["total_return_pct"], reverse=True)
    pd.DataFrame(results).to_csv(OUTPUT_ROOT / f"{stage}.csv", index=False)
    return results


def build_stage1_runs() -> list[tuple[str, dict]]:
    runs = [("baseline", build_run_params("baseline"))]
    for threshold in STAGE1_THRESHOLDS:
        runs.append((f"hold_t{int(threshold)}", build_run_params("hold", threshold=threshold)))
        runs.append((f"flat_t{int(threshold)}", build_run_params("flat", threshold=threshold)))
        for watch_bars in WATCH_BARS_STAGE1:
            tag = f"watch_t{int(threshold)}_n{watch_bars}"
            runs.append((tag, build_run_params("watch", threshold=threshold, watch_bars=watch_bars)))
    return runs


def _threshold_neighbors(winner_threshold: float) -> list[float]:
    vals = sorted(
        {
            max(10.0, winner_threshold - 20.0),
            max(10.0, winner_threshold - 10.0),
            winner_threshold,
            winner_threshold + 10.0,
            winner_threshold + 20.0,
        }
    )
    return vals


def _watch_neighbors(winner_watch_bars: int | None) -> list[int]:
    if winner_watch_bars is None:
        return []
    vals = sorted(
        {
            max(2, winner_watch_bars - 2),
            winner_watch_bars,
            winner_watch_bars + 2,
        }
    )
    return vals


def build_stage2_runs(stage1_results: list[dict]) -> tuple[list[tuple[str, dict]], dict]:
    non_baseline = [row for row in stage1_results if row["tag"] != "baseline"]
    if not non_baseline:
        raise RuntimeError("Stage 1 returned no non-baseline runs")
    winner = max(non_baseline, key=lambda x: x["total_return_pct"])

    thresholds = _threshold_neighbors(float(winner["rvol_threshold"]))
    runs = [("baseline", build_run_params("baseline"))]
    if winner["reject_mode"] == "watch":
        watch_bars_values = _watch_neighbors(int(winner["watch_bars"]))
        for threshold in thresholds:
            for watch_bars in watch_bars_values:
                tag = f"watch_t{int(threshold)}_n{watch_bars}"
                runs.append((tag, build_run_params("watch", threshold=threshold, watch_bars=watch_bars)))
    else:
        mode = str(winner["reject_mode"])
        for threshold in thresholds:
            tag = f"{mode}_t{int(threshold)}"
            runs.append((tag, build_run_params(mode, threshold=threshold)))
    return runs, winner


def fmt_row(row: dict) -> str:
    threshold = row["rvol_threshold"]
    threshold_str = "-" if threshold is None else f"{threshold:>5.1f}"
    watch_str = "-" if row["watch_bars"] is None else f"{int(row['watch_bars']):>2d}"
    return (
        f"{row['tag']:<18} "
        f"ret={row['total_return_pct']:>+8.2f}%  "
        f"WR={row['wr_pct']:>5.1f}% ({row['wins']}/{row['exits_with_pnl']})  "
        f"Sh={row['sharpe']:>+5.2f}  "
        f"DD={row['max_dd_pct']:>+6.1f}%  "
        f"thr={threshold_str}  "
        f"N={watch_str}  "
        f"flips={row['flips']:>3}  "
        f"{row['elapsed_sec']}s"
    )


def main() -> None:
    print("LazySwing ETH H2 2024 RVOL flip-filter sweep")
    print(f"Period: {START} -> {END}")
    print(f"Data:   {DATA_FILE}")
    print("Baseline: ETH 30m HOF (ST 25 / 1.75)\n")

    stage1 = run_stage("stage1", build_stage1_runs())
    print("\nStage 1 ranking:\n")
    for row in stage1:
        print(fmt_row(row))

    stage2_runs, winner = build_stage2_runs(stage1)
    print(
        "\nStage 2 seed:"
        f" {winner['tag']}  ret={winner['total_return_pct']:+.2f}%"
        f"  WR={winner['wr_pct']:.1f}%"
    )
    stage2 = run_stage("stage2", stage2_runs)
    print("\nStage 2 ranking:\n")
    for row in stage2:
        print(fmt_row(row))

    combined = pd.concat([pd.DataFrame(stage1), pd.DataFrame(stage2)], ignore_index=True)
    combined.to_csv(OUTPUT_ROOT / "summary.csv", index=False)
    print(f"\nSummary saved to {OUTPUT_ROOT / 'summary.csv'}")


if __name__ == "__main__":
    main()
