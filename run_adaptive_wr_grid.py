"""Grid search: Adaptive WR filter for LazySwing.

Tracks trade outcomes (win/loss) in a rolling history. Computes HMA(wr_hma_period)
as the fast win-rate signal, compared to a long-term baseline. When the ratio drops
below wr_activate_pct, activates an ATR vol filter (S=3, L=40, T=1.30). Deactivates
when ratio recovers to wr_deactivate_pct.

Grid (dev set only):
  wr_hma_period:   [10, 20, 30]
  wr_lt_window:    [50, 100]
  wr_activate_pct: [0.4, 0.5, 0.6]
  wr_deactivate:   activate + 0.20 (fixed hysteresis)

ATR filter params fixed at: short=3, long=40, threshold=1.30
"""

import sys
import json
import itertools
import pandas as pd

sys.path.insert(0, "src")

from config import Config
from controller import Controller

# ── Config ────────────────────────────────────────────────────────────

BASE_PARAMS = {
    "supertrend_atr_period": 10,
    "supertrend_multiplier": 1.5,
    "hmacd_fast": 24,
    "hmacd_slow": 51,
    "hmacd_signal": 12,
    "cost_per_trade_pct": 0.05,
    # adaptive ATR filter when active
    "adaptive_vol_short": 3,
    "adaptive_vol_long": 40,
    "adaptive_vol_threshold": 1.30,
}

DEV = {
    "file": "data/BTCUSDT-5m-2022-2024-combined.csv",
    "start": "2022-01-01",
    "end": "2024-12-31",
    "symbol": "BTCUSDT",
}

TEST = {
    "file": "data/BTCUSDT-5m-test-combined.csv",
    "start": "2020-01-01",
    "end": "2026-01-31",
    "symbol": "BTCUSDT",
}

LIVE = {
    "file": "tmp/lazyswing_live_5m.csv",
    "start": "2026-02-01",
    "end": "2026-03-27",
    "symbol": "BTCUSDT",
}

HMA_PERIODS  = [10, 20, 30]
LT_WINDOWS   = [50, 100]
ACTIVATE_PCTS = [0.4, 0.5, 0.6]
HYSTERESIS   = 0.20          # deactivate = activate + hysteresis

OUTPUT_CSV = "tmp/adaptive_wr_grid_results.csv"


# ── Helpers ───────────────────────────────────────────────────────────

def make_config(name, ds, extra_params):
    return Config({
        "backtest": {
            "name": name,
            "version": "grid",
            "initial_cash": 100000,
            "start_date": ds["start"],
            "end_date": ds["end"],
        },
        "data_source": {
            "type": "csv_file",
            "parser": "binance_kline",
            "params": {
                "file_path": ds["file"],
                "symbol": ds["symbol"],
            },
        },
        "strategies": [{
            "type": "lazy_swing",
            "params": {**BASE_PARAMS, **extra_params},
        }],
    })


def run_one(config):
    ctrl = Controller(config, output_dir="tmp/adaptive_wr_grid")
    return ctrl.run()[0]


def compute_stats(result):
    df = pd.read_csv(result.trade_log_path)
    closes = df[df["action"].isin(["SELL", "COVER"])]
    total = len(closes)
    if total == 0:
        return 0, 0.0, 0.0, 0
    pnls = closes["details"].apply(lambda x: json.loads(x).get("pnl_pct", 0))
    win_rate = (pnls > 0).sum() / total * 100
    avg_pnl = pnls.mean()
    # Count bars when adaptive filter was active
    active_bars = df["details"].apply(
        lambda x: json.loads(x).get("adaptive_filter_active", False)
    ).sum()
    return total, round(win_rate, 1), round(avg_pnl, 3), int(active_bars)


def run_dataset(ds, label, extra_params, row_base):
    cfg = make_config(f"{label}_{ds['start'][:4]}", ds, extra_params)
    r = run_one(cfg)
    trades, wr, avg_pnl, active_bars = compute_stats(r)
    return {
        **row_base,
        "dataset": label,
        "return_pct": round(r.total_return_pct, 2),
        "trades": trades,
        "win_rate": wr,
        "avg_pnl": avg_pnl,
        "active_bars": active_bars,
    }


# ── Main ──────────────────────────────────────────────────────────────

def main():
    import os
    os.makedirs("tmp/adaptive_wr_grid", exist_ok=True)

    rows = []

    # Baseline: no adaptive filter
    print("[baseline] dev ...", flush=True)
    cfg = make_config("baseline_dev", DEV, {})
    r = run_one(cfg)
    trades, wr, avg_pnl, _ = compute_stats(r)
    baseline_row = {"hma": 0, "lt_window": 0, "activate": 0.0, "deactivate": 0.0}
    rows.append({**baseline_row, "dataset": "dev",
                 "return_pct": round(r.total_return_pct, 2),
                 "trades": trades, "win_rate": wr, "avg_pnl": avg_pnl, "active_bars": 0})
    print(f"  → return={r.total_return_pct:+.0f}%  trades={trades}  WR={wr:.1f}%")

    # Grid on dev
    combos = list(itertools.product(HMA_PERIODS, LT_WINDOWS, ACTIVATE_PCTS))
    for i, (hma, lt_w, act) in enumerate(combos, 1):
        deact = round(act + HYSTERESIS, 2)
        label = f"H{hma}_W{lt_w}_A{act}"
        print(f"[{i}/{len(combos)}] {label} dev ...", flush=True)
        extra = {
            "wr_hma_period": hma,
            "wr_lt_window": lt_w,
            "wr_activate_pct": act,
            "wr_deactivate_pct": deact,
        }
        row_base = {"hma": hma, "lt_window": lt_w, "activate": act, "deactivate": deact}
        row = run_dataset(DEV, "dev", extra, row_base)
        rows.append(row)
        print(f"  → return={row['return_pct']:+.0f}%  trades={row['trades']}  "
              f"WR={row['win_rate']:.1f}%  filter_active_bars={row['active_bars']}")

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)

    dev = df[df["dataset"] == "dev"].sort_values("return_pct", ascending=False)
    baseline = dev[dev["hma"] == 0].iloc[0]

    print(f"\n=== BASELINE: return={baseline.return_pct:+.0f}%  "
          f"trades={baseline.trades}  WR={baseline.win_rate:.1f}%")

    print("\n=== DEV TOP 10 BY RETURN ===")
    for _, r in dev[dev["hma"] > 0].head(10).iterrows():
        print(f"H={r.hma} W={r.lt_window} A={r.activate:.2f}: "
              f"return={r.return_pct:+.0f}%  trades={r.trades}  "
              f"WR={r.win_rate:.1f}%  avgPnL={r.avg_pnl:.3f}%  "
              f"filter_bars={r.active_bars}")

    print("\n=== DEV TOP 5 BY WIN RATE ===")
    for _, r in dev[dev["hma"] > 0].sort_values("win_rate", ascending=False).head(5).iterrows():
        print(f"H={r.hma} W={r.lt_window} A={r.activate:.2f}: "
              f"return={r.return_pct:+.0f}%  trades={r.trades}  "
              f"WR={r.win_rate:.1f}%  avgPnL={r.avg_pnl:.3f}%  "
              f"filter_bars={r.active_bars}")

    # Best 3 by return (excluding baseline)
    best3 = dev[dev["hma"] > 0].head(3)
    print("\n=== RUNNING TEST + LIVE ON BEST 3 ===")

    test_live_rows = []
    for _, brow in best3.iterrows():
        extra = {
            "wr_hma_period": int(brow.hma),
            "wr_lt_window": int(brow.lt_window),
            "wr_activate_pct": float(brow.activate),
            "wr_deactivate_pct": float(brow.deactivate),
        }
        row_base = {"hma": int(brow.hma), "lt_window": int(brow.lt_window),
                    "activate": float(brow.activate), "deactivate": float(brow.deactivate)}
        label = f"H{int(brow.hma)}_W{int(brow.lt_window)}_A{brow.activate}"

        print(f"\n  {label} — test ...", flush=True)
        row_test = run_dataset(TEST, "test", extra, row_base)
        test_live_rows.append(row_test)
        print(f"    test: return={row_test['return_pct']:+.0f}%  trades={row_test['trades']}  "
              f"WR={row_test['win_rate']:.1f}%")

        print(f"  {label} — live ...", flush=True)
        row_live = run_dataset(LIVE, "live", extra, row_base)
        test_live_rows.append(row_live)
        print(f"    live: return={row_live['return_pct']:+.0f}%  trades={row_live['trades']}  "
              f"WR={row_live['win_rate']:.1f}%")

    # Run baseline on test/live too
    for ds, ds_label in [(TEST, "test"), (LIVE, "live")]:
        cfg = make_config(f"baseline_{ds_label}", ds, {})
        r = run_one(cfg)
        trades, wr, avg_pnl, _ = compute_stats(r)
        test_live_rows.append({**baseline_row, "dataset": ds_label,
                                "return_pct": round(r.total_return_pct, 2),
                                "trades": trades, "win_rate": wr,
                                "avg_pnl": avg_pnl, "active_bars": 0})
        print(f"\n  baseline {ds_label}: return={r.total_return_pct:+.0f}%  "
              f"trades={trades}  WR={wr:.1f}%")

    all_rows = pd.DataFrame(list(rows) + test_live_rows)
    all_rows.to_csv(OUTPUT_CSV, index=False)
    print(f"\nAll results saved to {OUTPUT_CSV}")

    print("\n=== FINAL COMPARISON TABLE ===")
    summary = all_rows[all_rows["dataset"].isin(["test", "live"])]
    for _, r in summary.sort_values(["dataset", "return_pct"], ascending=[True, False]).iterrows():
        tag = "BASELINE" if r.hma == 0 else f"H={int(r.hma)} W={int(r.lt_window)} A={r.activate:.2f}"
        print(f"[{r.dataset}] {tag}: return={r.return_pct:+.0f}%  "
              f"trades={r.trades}  WR={r.win_rate:.1f}%  avgPnL={r.avg_pnl:.3f}%")


if __name__ == "__main__":
    main()
