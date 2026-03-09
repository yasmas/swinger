"""Analyze intraday strategy trade log with detailed metrics."""
import json
import sys
from pathlib import Path

import pandas as pd
import numpy as np

# Regime classification from 5m-high-level-plan.md
REGIME_MAP = {
    (2022, 1): "Bear", (2022, 2): "Bear", (2022, 3): "Bull", (2022, 4): "Bear",
    (2022, 5): "Bear", (2022, 6): "Bear", (2022, 7): "Choppy", (2022, 8): "Bear",
    (2022, 9): "Flat", (2022, 10): "Flat", (2022, 11): "Bear", (2022, 12): "Flat",
    (2023, 1): "Bull", (2023, 2): "Flat", (2023, 3): "Bull", (2023, 4): "Flat",
    (2023, 5): "Flat", (2023, 6): "Bull", (2023, 7): "Flat", (2023, 8): "Bear",
    (2023, 9): "Flat", (2023, 10): "Bull", (2023, 11): "Flat", (2023, 12): "Flat",
    (2024, 1): "Flat", (2024, 2): "Bull", (2024, 3): "Bull", (2024, 4): "Bear",
    (2024, 5): "Flat", (2024, 6): "Bear", (2024, 7): "Flat", (2024, 8): "Bear",
    (2024, 9): "Flat", (2024, 10): "Flat", (2024, 11): "Bull", (2024, 12): "Flat",
}


def analyze(csv_path: str, initial_cash: float = 100000):
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])

    # Parse details JSON
    df["details_parsed"] = df["details"].apply(
        lambda x: json.loads(x) if isinstance(x, str) and x.strip() else {}
    )

    # Separate entries and exits
    entries = df[df["action"].isin(["BUY", "SHORT"])].copy()
    exits = df[df["action"].isin(["SELL", "COVER"])].copy()

    # Build trade pairs
    trades = []
    entry_stack = []
    for _, row in df.iterrows():
        if row["action"] in ("BUY", "SHORT"):
            entry_stack.append(row)
        elif row["action"] in ("SELL", "COVER") and entry_stack:
            entry = entry_stack.pop(0)
            d = row["details_parsed"]
            entry_d = entry["details_parsed"]

            if entry["action"] == "BUY":
                pnl_pct = (row["price"] - entry["price"]) / entry["price"] * 100
            else:
                pnl_pct = (entry["price"] - row["price"]) / entry["price"] * 100

            trades.append({
                "entry_date": entry["date"],
                "exit_date": row["date"],
                "direction": entry_d.get("direction", entry["action"]),
                "entry_price": entry["price"],
                "exit_price": row["price"],
                "pnl_pct": pnl_pct,
                "bars_held": d.get("bars_held", 0),
                "exit_reason": d.get("exit_reason", "unknown"),
                "trigger": entry_d.get("trigger", "unknown"),
                "mfe_pct": d.get("max_favorable_excursion_pct", 0),
                "mae_pct": d.get("max_adverse_excursion_pct", 0),
            })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        print("No trades found!")
        return

    # Basic stats
    final_value = df.iloc[-1]["portfolio_value"]
    total_return = (final_value / initial_cash - 1) * 100
    days = (df["date"].iloc[-1] - df["date"].iloc[0]).days
    years = days / 365.25

    winners = trades_df[trades_df["pnl_pct"] > 0]
    losers = trades_df[trades_df["pnl_pct"] <= 0]
    win_rate = len(winners) / len(trades_df) * 100

    avg_win = winners["pnl_pct"].mean() if len(winners) > 0 else 0
    avg_loss = losers["pnl_pct"].mean() if len(losers) > 0 else 0
    profit_factor = abs(winners["pnl_pct"].sum() / losers["pnl_pct"].sum()) if len(losers) > 0 and losers["pnl_pct"].sum() != 0 else float("inf")

    # Max consecutive losses
    is_loss = (trades_df["pnl_pct"] <= 0).values
    max_consec_losses = 0
    current = 0
    for l in is_loss:
        if l:
            current += 1
            max_consec_losses = max(max_consec_losses, current)
        else:
            current = 0

    # Sharpe ratio
    pv = df.set_index("date")["portfolio_value"].resample("D").last().dropna()
    daily_returns = pv.pct_change().dropna()
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

    # Max drawdown
    cummax = pv.cummax()
    drawdown = (pv - cummax) / cummax * 100
    max_dd = drawdown.min()

    # Transaction costs
    trade_actions = df[df["action"].isin(["BUY", "SELL", "SHORT", "COVER"])]
    total_costs = (trade_actions["price"] * trade_actions["quantity"] * 0.05 / 100).sum()
    after_cost_return = ((final_value - total_costs) / initial_cash - 1) * 100

    print("=" * 70)
    print(f"  INTRADAY TREND STRATEGY — ANALYSIS REPORT")
    print("=" * 70)
    print(f"\n  Period: {df['date'].iloc[0].date()} to {df['date'].iloc[-1].date()} ({days} days)")
    print(f"  Initial cash: ${initial_cash:,.0f}")
    print(f"  Final value:  ${final_value:,.0f}")
    print(f"  Total return: {total_return:+.2f}%")
    print(f"  After costs:  {after_cost_return:+.2f}% (costs: ${total_costs:,.0f})")
    print(f"  Sharpe ratio: {sharpe:.2f}")
    print(f"  Max drawdown: {max_dd:.2f}%")

    print(f"\n  --- Trade Statistics ---")
    print(f"  Total trades:    {len(trades_df)}")
    print(f"  Trades/day:      {len(trades_df)/days:.2f}")
    print(f"  Win rate:        {win_rate:.1f}%")
    print(f"  Avg win:         {avg_win:+.2f}%")
    print(f"  Avg loss:        {avg_loss:+.2f}%")
    print(f"  Profit factor:   {profit_factor:.2f}")
    print(f"  Avg bars held:   {trades_df['bars_held'].mean():.1f}")
    print(f"  Max consec loss: {max_consec_losses}")

    # Long vs Short breakdown
    longs = trades_df[trades_df["direction"] == "LONG"]
    shorts = trades_df[trades_df["direction"] == "SHORT"]
    print(f"\n  --- Direction Breakdown ---")
    print(f"  {'':15s} {'Count':>6s} {'Win%':>6s} {'AvgPnL':>8s} {'TotalPnL':>10s}")
    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        if len(subset) == 0:
            continue
        w = (subset["pnl_pct"] > 0).sum() / len(subset) * 100
        print(f"  {label:15s} {len(subset):6d} {w:5.1f}% {subset['pnl_pct'].mean():+7.2f}% {subset['pnl_pct'].sum():+9.2f}%")

    # Exit reason breakdown
    print(f"\n  --- Exit Reasons ---")
    for reason, group in trades_df.groupby("exit_reason"):
        wr = (group["pnl_pct"] > 0).sum() / len(group) * 100
        print(f"  {reason:25s} {len(group):5d} trades, win {wr:5.1f}%, avg PnL {group['pnl_pct'].mean():+.2f}%")

    # Entry trigger breakdown
    print(f"\n  --- Entry Triggers ---")
    for trigger, group in trades_df.groupby("trigger"):
        wr = (group["pnl_pct"] > 0).sum() / len(group) * 100
        print(f"  {trigger:25s} {len(group):5d} trades, win {wr:5.1f}%, avg PnL {group['pnl_pct'].mean():+.2f}%")

    # Regime breakdown
    trades_df["year"] = trades_df["entry_date"].dt.year
    trades_df["month"] = trades_df["entry_date"].dt.month
    trades_df["regime"] = trades_df.apply(
        lambda r: REGIME_MAP.get((r["year"], r["month"]), "Unknown"), axis=1
    )

    print(f"\n  --- Regime Breakdown ---")
    print(f"  {'Regime':10s} {'Count':>6s} {'Win%':>6s} {'AvgPnL':>8s} {'TotalPnL':>10s}")
    for regime in ["Bull", "Bear", "Choppy", "Flat"]:
        subset = trades_df[trades_df["regime"] == regime]
        if len(subset) == 0:
            continue
        w = (subset["pnl_pct"] > 0).sum() / len(subset) * 100
        print(f"  {regime:10s} {len(subset):6d} {w:5.1f}% {subset['pnl_pct'].mean():+7.2f}% {subset['pnl_pct'].sum():+9.2f}%")

    # MFE/MAE analysis
    print(f"\n  --- MFE/MAE Analysis ---")
    print(f"  Avg MFE (winners):  {winners['mfe_pct'].mean():+.2f}%")
    print(f"  Avg MAE (winners):  {winners['mae_pct'].mean():+.2f}%")
    print(f"  Avg MFE (losers):   {losers['mfe_pct'].mean():+.2f}%")
    print(f"  Avg MAE (losers):   {losers['mae_pct'].mean():+.2f}%")
    if len(winners) > 0:
        capture = (winners["pnl_pct"] / winners["mfe_pct"].replace(0, np.nan)).dropna()
        print(f"  Capture ratio:      {capture.mean():.2f} (of available MFE)")

    # Top 10 worst trades
    print(f"\n  --- 10 Worst Trades ---")
    worst = trades_df.nsmallest(10, "pnl_pct")
    for _, t in worst.iterrows():
        print(f"  {str(t['entry_date'])[:16]} {t['direction']:5s} {t['trigger']:20s} "
              f"PnL={t['pnl_pct']:+.2f}% exit={t['exit_reason']} bars={t['bars_held']}")

    print("=" * 70)


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "reports/BTC_Intraday_Trend_Dev_intraday_trend_v1.csv"
    analyze(csv_path)
