#!/usr/bin/env python3
"""Sanity check: reproduce grid baseline for one window standalone.

Builds the EXACT same Config as the grid would for (window, baseline_mg1.0)
and runs Controller, then prints the resulting return + key trade stats.

If grid reports +81% for 2025_Q3 baseline_mg1.0 and we see something wildly
different here, the grid has a bug.  If they match, the grid is internally
consistent (and the sub-agent's run was just a different config).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from controller import Controller  # noqa: E402
from reporting.reporter import compute_stats  # noqa: E402
from trade_log import TradeLogReader  # noqa: E402

# Import grid script bits to reuse exact configs
sys.path.insert(0, str(REPO / "scripts"))
from grid_search_lazyswing_profit_exit import (  # noqa: E402
    BASE_PARAMS,
    WINDOWS,
    _build_config,
    _prepare_slices,
    _trade_metrics,
)


def main(window_key: str = "2025_Q3", tag: str = "baseline_mg1.0") -> None:
    print(f"Reproducing grid run: window={window_key}, tag={tag}")
    slice_map = _prepare_slices()
    print()

    # Reconstruct the baseline_mg1.0 trail params directly
    baseline_params = {
        "regime_trail_mode": "strict_exhaustion",
        "regime_exhaustion_stretch_lookback": 3,
        "regime_exhaustion_kc_z_min": 1.75,
        "regime_exhaustion_bb_z_min": 2.75,
        "regime_exhaustion_adx_lookback": 2,
        "regime_exhaustion_prev_adx_min": 20.0,
        "regime_exhaustion_adx_drop_pct": 2.5,
        "trail_stop_min_gain_pct": 1.0,
    }

    cfg = _build_config(window_key, tag, baseline_params, slice_map[window_key])
    print(f"start_date={cfg.start_date}  end_date={cfg.end_date}")
    print(f"data_file={cfg.data_source['params']['file_path']}")
    print(f"strategy params (subset):")
    sp = cfg.strategies[0]["params"]
    for k in ("supertrend_atr_period", "supertrend_multiplier", "resample_interval",
              "fast_exit_enabled", "regime_trail_enabled", "regime_trail_mode",
              "trail_stop_pct", "trail_stop_min_gain_pct", "cost_per_trade_pct"):
        print(f"  {k}: {sp.get(k)}")
    print()

    tmp = tempfile.mkdtemp(prefix="grid_verify_")
    result = Controller(cfg, output_dir=tmp).run()[0]
    tl = TradeLogReader().read(result.trade_log_path)

    stats = compute_stats(tl, cfg.initial_cash, 0.05)
    tm = _trade_metrics(tl)

    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"  return_pct:         {stats['total_return']:+.2f}%")
    print(f"  final_value:        ${stats['final_value']:,.2f}")
    print(f"  num_buys:           {stats['num_buys']}")
    print(f"  num_shorts:         {stats['num_shorts']}")
    print(f"  wr_pct:             {tm['wr_pct']:.1f}%  ({tm['wins']}W / {tm['losses']}L)")
    print(f"  avg_pnl_pct:        {tm['avg_pnl_pct']:+.3f}%")
    print(f"  avg_trail_pnl_pct:  {tm['avg_trail_pnl_pct']:+.3f}%")
    print(f"  trail_exits:        {tm['trail_exits']}")
    print(f"  trade_log:          {result.trade_log_path}")
    print()
    print("Compare to grid log line for 2025_Q3 baseline_mg1.0 if completed.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--window", default="2025_Q3", choices=list(WINDOWS.keys()))
    p.add_argument("--tag", default="baseline_mg1.0")
    a = p.parse_args()
    main(a.window, a.tag)
