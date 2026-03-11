"""Grid search for intraday_trend v6 parameters.

Axes searched:
  supertrend_atr_period : [14, 21]
  supertrend_multiplier : [3.5, 4.0, 4.5]
  min_hold_bars         : [6, 12, 24]
  hma_period            : [34, 55]

All other params held at v5 values.
Data: 2022-01-01 to 2024-12-31 (dev set).
"""

import sys
import json
import math
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

# ── path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "src"))

from data_sources.parsers.binance import BinanceKlineParser
from data_sources.csv_file import CsvFileDataSource as CSVFileDataSource
from strategies.intraday_trend import IntradayTrendStrategy
from strategies.base import ActionType, portfolio_view_from
from portfolio import Portfolio
from execution.backtest_executor import BacktestExecutor
from trade_log import TradeLogger

# ── Grid axes ────────────────────────────────────────────────────────────────
GRID = {
    "supertrend_atr_period": [14, 21],
    "supertrend_multiplier": [3.5, 4.0, 4.5],
    "min_hold_bars":         [6, 12, 24],
    "hma_period":            [34, 55],
}

# ── Fixed v5 params (everything not being searched) ─────────────────────────
BASE_PARAMS = dict(
    symbol                   = "BTCUSDT",
    keltner_ema_period       = 15,
    keltner_atr_period       = 10,
    keltner_atr_multiplier   = 2.5,
    bb_period                = 20,
    bb_stddev                = 2.0,
    adx_period               = 14,
    adx_threshold            = 30,
    volume_avg_period        = 20,
    volume_confirm_multiplier= 2.0,
    stop_loss_pct            = 2.0,
    daily_max_drawdown_pct   = 6.0,
    max_supertrend_stop_pct  = 2.0,
    breakeven_trigger_pct    = 1.2,
    trailing_supertrend_multiplier = 0,
    enable_keltner_bounce    = False,
    breakout_confirm_bars    = 2,
    cooldown_bars            = 12,
    min_hma_slope_bps        = 2.0,
    enable_short             = True,
    short_adx_threshold      = 35,
    min_atr_pct              = 0.18,
    cost_per_trade_pct       = 0.05,
)

INITIAL_CASH  = 100_000.0
DATA_FILE     = "data/BTCUSDT-5m-2022-2024-combined.csv"
START_DATE    = "2022-01-01"
END_DATE      = "2024-12-31"
SYMBOL        = "BTCUSDT"
OUTPUT_CSV    = "reports/grid_search_intraday_v6.csv"


# ── helpers ──────────────────────────────────────────────────────────────────

def _portfolio_view(portfolio: Portfolio, symbol: str, price: float):
    return portfolio_view_from(portfolio, symbol)


def run_backtest(data: pd.DataFrame, params: dict) -> dict:
    """Run a single backtest; return metrics dict."""
    portfolio = Portfolio(INITIAL_CASH)
    strategy  = IntradayTrendStrategy(params)
    executor  = BacktestExecutor()
    strategy.prepare(data)

    num_bars   = len(data)
    daily_vals = {}
    all_trades = []  # (pnl_pct, bars_held)

    _pending_entry = {}  # temp storage to pair entry→exit

    for i, (date, row) in enumerate(data.iterrows()):
        is_last = i == num_bars - 1
        price   = row["close"]
        pv      = _portfolio_view(portfolio, SYMBOL, price)

        action = strategy.on_bar(date, row, data.iloc[: i + 1], is_last, pv)

        if action.action != ActionType.HOLD:
            executor.execute(action, SYMBOL, price, portfolio)

            # Capture trades for metrics
            act = action.action
            det = action.details or {}
            if act in (ActionType.BUY, ActionType.SHORT):
                _pending_entry = {"action": act, "price": price, "time": date}
            elif act in (ActionType.SELL, ActionType.COVER):
                pnl   = det.get("pnl_pct", 0.0)
                bars  = det.get("bars_held", 0)
                all_trades.append((pnl, bars))

        pv_value = portfolio.total_value({SYMBOL: price})
        day_str  = str(date.date()) if hasattr(date, "date") else str(date)[:10]
        daily_vals[day_str] = pv_value

    final_price = data.iloc[-1]["close"]
    final_value = portfolio.total_value({SYMBOL: final_price})

    total_return = (final_value / INITIAL_CASH - 1) * 100

    # ── cost estimation ──────────────────────────────────────────────────────
    n_trades = len(all_trades)
    cost_pct_per_side = params.get("cost_per_trade_pct", 0.05) / 100.0
    total_cost_pct    = n_trades * 2 * cost_pct_per_side * 100   # both legs, back to %
    after_cost_return = total_return - total_cost_pct

    # ── Sharpe (daily returns) ───────────────────────────────────────────────
    daily_series = pd.Series(daily_vals)
    daily_rets   = daily_series.pct_change().dropna()
    sharpe = (daily_rets.mean() / daily_rets.std() * math.sqrt(252)) if daily_rets.std() > 0 else 0.0

    # ── Max drawdown ─────────────────────────────────────────────────────────
    rolling_max = daily_series.cummax()
    drawdowns   = (daily_series - rolling_max) / rolling_max * 100
    max_dd      = drawdowns.min()

    # ── Win rate / profit factor ─────────────────────────────────────────────
    if all_trades:
        pnls    = [t[0] for t in all_trades]
        wins    = [p for p in pnls if p > 0]
        losses  = [p for p in pnls if p <= 0]
        win_rate   = 100 * len(wins) / len(pnls)
        gross_profit = sum(wins)
        gross_loss   = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        avg_bars  = sum(t[1] for t in all_trades) / len(all_trades)
        short_hold_count = sum(1 for t in all_trades if t[1] < 12)
        short_hold_pct   = 100 * short_hold_count / len(all_trades)
    else:
        win_rate = profit_factor = avg_bars = short_hold_pct = 0.0

    return dict(
        total_return     = round(total_return, 2),
        after_cost_return= round(after_cost_return, 2),
        sharpe           = round(sharpe, 3),
        max_drawdown     = round(max_dd, 2),
        total_trades     = n_trades,
        win_rate         = round(win_rate, 1),
        profit_factor    = round(profit_factor, 3),
        avg_bars_held    = round(avg_bars, 1),
        short_hold_pct   = round(short_hold_pct, 1),
    )


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"Loading data: {DATA_FILE}")
    parser = BinanceKlineParser()
    source = CSVFileDataSource(parser, {"file_path": DATA_FILE})
    data   = source.get_data(SYMBOL, START_DATE, END_DATE)
    print(f"  Loaded {len(data):,} bars ({START_DATE} to {END_DATE})")

    keys   = list(GRID.keys())
    combos = list(itertools.product(*GRID.values()))
    total  = len(combos)
    print(f"\nRunning {total} combinations...\n")

    rows = []
    for n, values in enumerate(combos, 1):
        combo = dict(zip(keys, values))
        params = {**BASE_PARAMS, **combo}

        metrics = run_backtest(data, params)
        row = {**combo, **metrics}
        rows.append(row)

        tag = (
            f"ST({combo['supertrend_atr_period']},{combo['supertrend_multiplier']}) "
            f"MH={combo['min_hold_bars']} "
            f"HMA={combo['hma_period']}"
        )
        print(
            f"[{n:2d}/{total}] {tag:45s} "
            f"gross={row['total_return']:+7.2f}%  "
            f"net={row['after_cost_return']:+7.2f}%  "
            f"Sharpe={row['sharpe']:.3f}  "
            f"WR={row['win_rate']:.0f}%  "
            f"DD={row['max_drawdown']:.2f}%  "
            f"trades={row['total_trades']}  "
            f"avgbars={row['avg_bars_held']:.0f}  "
            f"<1hr={row['short_hold_pct']:.0f}%"
        )

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nResults saved to {OUTPUT_CSV}")

    print("\n=== Top 10 by after-cost return ===")
    top = df.nlargest(10, "after_cost_return")[
        ["supertrend_atr_period","supertrend_multiplier","min_hold_bars","hma_period",
         "total_return","after_cost_return","sharpe","max_drawdown",
         "total_trades","win_rate","profit_factor","avg_bars_held","short_hold_pct"]
    ]
    print(top.to_string(index=False))

    print("\n=== Top 10 by Sharpe ===")
    top_s = df.nlargest(10, "sharpe")[
        ["supertrend_atr_period","supertrend_multiplier","min_hold_bars","hma_period",
         "total_return","after_cost_return","sharpe","max_drawdown",
         "total_trades","win_rate","profit_factor","avg_bars_held","short_hold_pct"]
    ]
    print(top_s.to_string(index=False))

    # ── v5 baseline reminder ─────────────────────────────────────────────────
    print("\n=== v5 baseline (ST 10/3.0, MH=0, HMA=21) ===")
    print("  Gross +106.97%  Net +27.48%  Sharpe 1.15  WR 38.1%  DD -12.82%  trades 465")


if __name__ == "__main__":
    main()
