#!/usr/bin/env python3
"""
Grid search: kc_midline_hold_bars ∈ {0,1,2,4} × adx_rising ∈ {False,True}
Uses the real run_backtest.py engine so results are directly comparable to v1.
"""

import sys, os, subprocess, json, tempfile, shutil
import yaml
import pandas as pd
import numpy as np

PYTHON = sys.executable
SCRIPT = "run_backtest.py"

# ── Load base configs ─────────────────────────────────────────────────────────
with open('config/swing_trend_dev_v1.yaml')  as f: dev_base  = yaml.safe_load(f)
with open('config/swing_trend_test_v1.yaml') as f: test_base = yaml.safe_load(f)

# ── Metrics from trade log CSV ─────────────────────────────────────────────────
def metrics_from_log(log_path: str, initial_cash: float) -> dict:
    df = pd.read_csv(log_path)
    pv = df['portfolio_value']
    ret_pct = (pv.iloc[-1] / initial_cash - 1) * 100

    # Equity curve returns (5m bars)
    dret = pv.pct_change().dropna()
    bars_per_year = 105120  # 5m bars
    sharpe = (dret.mean() / dret.std() * np.sqrt(bars_per_year)) if dret.std() > 0 else 0.0

    roll_max = pv.cummax()
    max_dd   = ((pv - roll_max) / roll_max).min() * 100

    # Parse trade rows (BUY/SHORT entries, SELL/COVER exits)
    trade_rows = df[df['action'].isin(['SELL', 'COVER'])].copy()
    n_trades   = len(trade_rows)
    win_rate   = 0.0
    pf         = 0.0
    midline_tr = 0
    midline_pnl_sum = 0.0

    if n_trades > 0:
        wins   = []
        losses = []
        for _, row in trade_rows.iterrows():
            try:
                d = json.loads(row['details'])
            except Exception:
                d = {}
            pnl = d.get('pnl_pct', 0.0)
            if pnl > 0:
                wins.append(pnl)
            else:
                losses.append(pnl)
            if d.get('trigger') == 'kc_midline_hold':
                midline_tr  += 1
                midline_pnl_sum += pnl

        win_rate = len(wins) / n_trades * 100
        pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else float('inf')

    # Also count entries with kc_midline_hold trigger
    entry_rows = df[df['action'].isin(['BUY', 'SHORT'])].copy()
    midline_entries = 0
    for _, row in entry_rows.iterrows():
        try:
            d = json.loads(row['details'])
        except Exception:
            d = {}
        if d.get('trigger') == 'kc_midline_hold':
            midline_entries += 1

    return {
        'return_pct'    : round(ret_pct,  2),
        'sharpe'        : round(sharpe,   3),
        'max_dd_pct'    : round(max_dd,   2),
        'n_trades'      : n_trades,
        'win_rate'      : round(win_rate, 1),
        'pf'            : round(pf,       3),
        'midline_entries'   : midline_entries,
        'midline_pnl_sum'   : round(midline_pnl_sum, 2),
    }


def run_one(base_cfg: dict, N: int, adx_rising: bool, label: str) -> dict:
    """Write a temp config, run backtest, parse results."""
    cfg = yaml.safe_load(yaml.dump(base_cfg))  # deep copy
    cfg['strategies'][0]['params']['kc_midline_hold_bars']       = N
    cfg['strategies'][0]['params']['kc_midline_hold_adx_rising'] = adx_rising
    # Give it a unique name so log file name is predictable
    safe = label.replace(' ', '_').replace('=', '').replace(',', '')
    cfg['backtest']['name'] = f"grid_{safe}"

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(cfg, f)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [PYTHON, SCRIPT, tmp_path],
            capture_output=True, text=True,
            env={**os.environ, 'PYTHONPATH': 'src'}
        )
        if result.returncode != 0:
            print(f"  ERROR:\n{result.stderr[-800:]}")
            return {}

        # Find trade log path from stdout
        log_path = None
        for line in result.stdout.splitlines():
            if 'Trade log:' in line:
                log_path = line.split('Trade log:')[-1].strip()
                break
        if log_path is None or not os.path.exists(log_path):
            print(f"  Could not find trade log. stdout:\n{result.stdout[-400:]}")
            return {}

        initial_cash = cfg['backtest']['initial_cash']
        m = metrics_from_log(log_path, initial_cash)
        return m

    finally:
        os.unlink(tmp_path)


# ── Grid ──────────────────────────────────────────────────────────────────────
grid = [
    (0, False, 'v1 baseline'),
    (1, False, 'N=1 simple'),
    (1, True,  'N=1 adx_rising'),
    (2, False, 'N=2 simple'),
    (2, True,  'N=2 adx_rising'),
    (4, False, 'N=4 simple'),
    (4, True,  'N=4 adx_rising'),
]

results = []
for N, adx_rising, label in grid:
    print(f"Running {label:<22} ...", end=' ', flush=True)
    dev_r  = run_one(dev_base,  N, adx_rising, label + '_dev')
    print("dev ✓", end='  ', flush=True)
    test_r = run_one(test_base, N, adx_rising, label + '_test')
    print("test ✓")
    results.append({'label': label, 'N': N, 'adx_rising': adx_rising,
                    'dev': dev_r, 'test': test_r})

# ── Report ────────────────────────────────────────────────────────────────────
SEP = "─" * 104

print(f"\n{'═'*104}")
print(f"{'KC MIDLINE HOLD — GRID SEARCH RESULTS':^104}")
print(f"{'═'*104}")
print(f"{'Config':<22}  │  {'──── DEV (2022-2024) ────':^40}  │  {'──── TEST ────':^36}")
print(f"{'':22}  │  {'Ret%':>7}  Sharpe  MaxDD%  Trades  WR%  PF  │  {'Ret%':>7}  Sharpe  MaxDD%  Trades  WR%  PF")
print(SEP)

for r in results:
    d, t = r['dev'], r['test']
    marker = " ◄" if r['N'] == 0 else "  "
    def fmt(m):
        if not m:
            return f"{'ERROR':>7}  {'—':>6}  {'—':>6}  {'—':>6}  {'—':>4}  {'—':>5}"
        return (f"{m['return_pct']:>+7.1f}  {m['sharpe']:>6.3f}  "
                f"{m['max_dd_pct']:>6.2f}  {m['n_trades']:>6}  "
                f"{m['win_rate']:>4.1f}  {m['pf']:>5.3f}")
    print(f"{r['label']+marker:<22}  │  {fmt(d)}  │  {fmt(t)}")

print(SEP)

# ── Midline hold contribution ─────────────────────────────────────────────────
print(f"\n{'MIDLINE-HOLD TRIGGER CONTRIBUTION (extra trades fired by new trigger only)':}")
print(f"{'─'*72}")
print(f"{'Config':<22}  {'Dev: entries / pnl-sum':>25}  {'Test: entries / pnl-sum':>25}")
print(f"{'─'*72}")
for r in results[1:]:
    d, t = r['dev'], r['test']
    def mstr(m):
        if not m: return "ERROR"
        return f"{m['midline_entries']:>3} entries / {m['midline_pnl_sum']:>+7.1f}% pnl-sum"
    print(f"  {r['label']:<20}  {mstr(d):>25}  {mstr(t):>25}")

print(f"{'═'*104}")
