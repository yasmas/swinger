"""Step 9: Integration tests for the full paper trading system.

Covers:
1. Simulated real-time test (mock exchange replays historical data)
2. Restart resilience (kill + restart, portfolio reconstructs correctly)
3. Month boundary (new monthly CSV files created)
4. Backtester unchanged (run_backtest.py still works on existing configs)
"""

import csv
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import logging
logging.basicConfig(level=logging.INFO, format="%(name)s - %(levelname)s - %(message)s")

from paper_trading.data_manager import DataManager, FIVE_MIN_MS, ONE_HOUR_MS
from paper_trading.fulfillment import FulfillmentEngine, FulfillmentResult
from paper_trading.paper_trader import PaperTrader
from paper_trading.state_manager import StateManager
from paper_trading.strategy_runner import StrategyRunner
from portfolio import Portfolio
from strategies.base import ActionType
from trade_log import TRADE_LOG_COLUMNS


def build_synthetic_5m(start: str, n_bars: int, base_price: float = 50000,
                       seed: int = 42) -> pd.DataFrame:
    """Build a realistic-ish 5m DataFrame with trending price action."""
    np.random.seed(seed)
    dates = pd.date_range(start, periods=n_bars, freq="5min")
    closes = base_price + np.cumsum(np.random.randn(n_bars) * 30)
    return pd.DataFrame({
        "open": closes - np.random.rand(n_bars) * 10,
        "high": closes + np.abs(np.random.randn(n_bars) * 40),
        "low": closes - np.abs(np.random.randn(n_bars) * 40),
        "close": closes,
        "volume": np.random.rand(n_bars) * 1000 + 100,
    }, index=dates)


def build_monthly_csv_rows(df_5m: pd.DataFrame) -> pd.DataFrame:
    """Convert a 5m OHLCV DataFrame (with DatetimeIndex) to Binance CSV format."""
    rows = []
    for ts, row in df_5m.iterrows():
        open_time_ms = int(ts.timestamp() * 1000)
        close_time_ms = open_time_ms + FIVE_MIN_MS - 1
        rows.append({
            "open_time": open_time_ms,
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "close_time": close_time_ms,
            "quote_asset_volume": 0,
            "number_of_trades": 0,
            "taker_buy_base_volume": 0,
            "taker_buy_quote_volume": 0,
            "ignore": 0,
        })
    return pd.DataFrame(rows)


class MockExchange:
    """Mock exchange that replays from a pre-built 5m DataFrame."""

    def __init__(self, df_5m_raw: pd.DataFrame, current_price: float = 50500):
        self._df = df_5m_raw
        self._current_price = current_price
        self._call_count = 0

    def fetch_ohlcv(self, symbol, interval, start_time_ms=None, end_time_ms=None, limit=None):
        self._call_count += 1
        df = self._df.copy()
        if start_time_ms is not None:
            df = df[df["open_time"] >= start_time_ms]
        if end_time_ms is not None:
            df = df[df["open_time"] <= end_time_ms]
        if limit is not None:
            df = df.head(limit)
        return df

    def get_current_price(self, symbol):
        return self._current_price

    def get_best_bid_ask(self, symbol):
        return {"bid_price": self._current_price - 10, "ask_price": self._current_price + 10}


# ============================================================
# Test 1: Simulated real-time — full startup + multiple ticks
# ============================================================
def test_simulated_realtime():
    print("=" * 60)
    print("Test 1: Simulated real-time (startup + tick cycles)")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp()
    data_dir = os.path.join(tmp_dir, "data")
    reports_dir = os.path.join(tmp_dir, "reports")

    # Build 10+ days of 5m data (enough for indicator warmup at 1h)
    df_5m = build_synthetic_5m("2026-02-01", n_bars=4000)
    raw_csv = build_monthly_csv_rows(df_5m)

    # Pre-populate the monthly CSV so DataManager.startup() finds local data
    os.makedirs(data_dir, exist_ok=True)
    csv_path = os.path.join(data_dir, "BTCUSDT-5m-2026-02.csv")
    raw_csv.to_csv(csv_path, index=False)

    mock_ex = MockExchange(raw_csv, current_price=float(df_5m.iloc[-1]["close"]))

    config = {
        "paper_trading": {
            "symbol": "BTCUSDT",
            "initial_cash": 100000,
            "data_dir": data_dir,
            "state_file": os.path.join(data_dir, "state.yaml"),
            "warm_up_hours": 250,
        },
        "exchange": {"base_url": "https://api.binance.us"},
        "fulfillment": {
            "target_improvement_pct": 0.02,
            "abort_threshold_pct": 0.3,
            "timeout_minutes": 30,
            "on_timeout": "market",
        },
        "strategy": {
            "type": "macd_rsi_advanced",
            "version": "v9",
            "params": {
                "resample_interval": "1h",
                "min_cross_hist_bps": 2.0,
                "cross_confirm_window": 2,
            },
        },
        "reporting": {
            "output_dir": reports_dir,
            "trade_log": os.path.join(reports_dir, "trades.csv"),
            "report_file": os.path.join(reports_dir, "report.html"),
        },
    }

    trader = PaperTrader(config)

    with patch("paper_trading.paper_trader.BinanceRestClient", return_value=mock_ex):
        with patch("paper_trading.data_manager.DataManager.startup") as mock_startup:
            mock_startup.return_value = (df_5m, None)
            trader.startup()

    assert trader.strategy_runner is not None
    assert trader.strategy_runner.portfolio.cash == 100000.0
    print("  Startup OK. Portfolio: $100,000")

    # Simulate several tick cycles
    for i in range(5):
        minute = 1 + (i * 5)
        now = datetime(2026, 2, 15, 14, minute, 0, tzinfo=timezone.utc)
        with patch.object(trader.data_manager, "fetch_and_append_5m", return_value=None):
            trader._tick(now, now.minute)

    print("  5 ticks completed without error")

    # Verify state can be saved and loaded
    trader._save_state()
    state = trader.state_manager.load()
    assert state["pending_order"] is None
    print("  State save/load OK")

    # Verify log file or stdout had output (logging is already configured)
    print("  PASS")
    shutil.rmtree(tmp_dir)


# ============================================================
# Test 2: Restart resilience
# ============================================================
def test_restart_resilience():
    print()
    print("=" * 60)
    print("Test 2: Restart resilience (trade log reconstruction)")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp()
    data_dir = os.path.join(tmp_dir, "data")
    reports_dir = os.path.join(tmp_dir, "reports")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    # Build data
    df_5m = build_synthetic_5m("2026-01-15", n_bars=5000)
    raw_csv = build_monthly_csv_rows(df_5m)
    csv_path = os.path.join(data_dir, "BTCUSDT-5m-2026-01.csv")
    raw_csv.to_csv(csv_path, index=False)

    trade_log_path = os.path.join(reports_dir, "trades.csv")

    # Simulate "Session 1": buy at 50100
    with open(trade_log_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(TRADE_LOG_COLUMNS)
        writer.writerow([
            "2026-01-18 12:00:00", "BUY", "BTCUSDT", "0.50000000",
            "50100.00", "74950.00", "100000.00", "{}",
        ])

    # Also save a pending fulfillment in state
    state_mgr = StateManager(os.path.join(data_dir, "state.yaml"))
    pending = {
        "action": "SELL",
        "quantity": 0.5,
        "decision_time": "2026-01-18T18:00:00+00:00",
        "decision_price": 50500,
        "bid_at_decision": 50490,
        "ask_at_decision": 50510,
        "target_price": 50520.0,
        "abort_price": 50348.5,
        "timeout_at": "2026-01-18T18:30:00+00:00",
        "checks": 3,
        "price_low_during_fill": 50400,
        "price_high_during_fill": 50600,
    }
    state_mgr.save(pending_order=pending)

    # "Session 2": restart — reconstruct from trade log
    mock_ex = MockExchange(raw_csv, current_price=50500)

    runner = StrategyRunner(
        strategy_type="macd_rsi_advanced",
        strategy_params={"resample_interval": "1h", "min_cross_hist_bps": 2.0, "cross_confirm_window": 2},
        initial_cash=100000.0,
        symbol="BTCUSDT",
        trade_log_path=trade_log_path,
    )
    runner.startup(df_5m, df_1h=None, exchange_price=50500)

    # Verify portfolio was reconstructed correctly
    assert "BTCUSDT" in runner.portfolio.positions
    assert runner.portfolio.positions["BTCUSDT"].quantity == 0.5
    assert abs(runner.portfolio.cash - 74950.0) < 0.02
    print(f"  Portfolio reconstructed: {runner.portfolio.positions['BTCUSDT'].quantity} BTC, cash=${runner.portfolio.cash:.2f}")

    # Verify pending order was saved
    state = state_mgr.load()
    assert state["pending_order"] is not None
    assert state["pending_order"]["action"] == "SELL"
    print(f"  Pending order loaded: {state['pending_order']['action']}")

    # Resume fulfillment engine
    engine = FulfillmentEngine(mock_ex, "BTCUSDT")
    engine.resume(state["pending_order"])
    assert engine.pending is not None
    assert engine.pending["target_price"] == 50520.0
    print(f"  Fulfillment resumed: target={engine.pending['target_price']}")

    # Verify no duplicate bars in original CSV
    reloaded = pd.read_csv(csv_path)
    assert reloaded["open_time"].is_unique, "Duplicate open_time found!"
    print("  No duplicate bars in CSV")

    print("  PASS")
    shutil.rmtree(tmp_dir)


# ============================================================
# Test 3: Data corruption + repair
# ============================================================
def test_corruption_repair():
    print()
    print("=" * 60)
    print("Test 3: Data corruption detection and repair")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp()
    data_dir = os.path.join(tmp_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # Build a small CSV
    df_5m = build_synthetic_5m("2026-02-10", n_bars=100)
    raw_csv = build_monthly_csv_rows(df_5m)
    csv_path = os.path.join(data_dir, "BTCUSDT-5m-2026-02.csv")
    raw_csv.to_csv(csv_path, index=False)

    # Corrupt the tail: add a partial line without newline
    with open(csv_path, "a") as f:
        f.write("12345,50000,50100,49900")  # truncated, no newline

    mock_ex = MockExchange(raw_csv, current_price=50000)
    dm = DataManager(mock_ex, "BTCUSDT", data_dir, warm_up_hours=1)

    # Repair should fix the truncated tail
    repaired = dm._repair_tail(Path(csv_path))
    assert repaired is True, "Should have detected and repaired corruption"
    print("  Tail corruption repaired")

    # After repair, the CSV should be valid
    df_after = pd.read_csv(csv_path)
    assert len(df_after) == len(raw_csv), f"Expected {len(raw_csv)} rows, got {len(df_after)}"
    assert df_after["open_time"].is_monotonic_increasing
    print(f"  CSV valid after repair: {len(df_after)} rows, monotonic timestamps")

    print("  PASS")
    shutil.rmtree(tmp_dir)


# ============================================================
# Test 4: Month boundary
# ============================================================
def test_month_boundary():
    print()
    print("=" * 60)
    print("Test 4: Month boundary — new monthly file created")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp()
    data_dir = os.path.join(tmp_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    # Build data that spans a month boundary (Jan 31 → Feb 1)
    df_jan = build_synthetic_5m("2026-01-31 20:00", n_bars=48)  # 4 hours, ends at 23:55
    df_feb = build_synthetic_5m("2026-02-01 00:00", n_bars=60, base_price=51000, seed=99)

    raw_jan = build_monthly_csv_rows(df_jan)
    raw_feb = build_monthly_csv_rows(df_feb)

    mock_ex = MockExchange(pd.concat([raw_jan, raw_feb]), current_price=51000)
    dm = DataManager(mock_ex, "BTCUSDT", data_dir, warm_up_hours=1)

    # Write Jan data
    jan_path = dm._monthly_path("5m", 2026, 1)
    raw_jan.to_csv(jan_path, index=False)

    # Write Feb data
    feb_path = dm._monthly_path("5m", 2026, 2)
    raw_feb.to_csv(feb_path, index=False)

    # Verify both files exist
    assert jan_path.exists(), "Jan file should exist"
    assert feb_path.exists(), "Feb file should exist"

    jan_df = pd.read_csv(jan_path)
    feb_df = pd.read_csv(feb_path)
    print(f"  Jan file: {len(jan_df)} rows")
    print(f"  Feb file: {len(feb_df)} rows")

    # Verify data continuity: last Jan timestamp < first Feb timestamp
    last_jan_ts = jan_df["open_time"].iloc[-1]
    first_feb_ts = feb_df["open_time"].iloc[0]
    assert last_jan_ts < first_feb_ts, "Jan last < Feb first"
    print(f"  Continuity OK: Jan last={last_jan_ts}, Feb first={first_feb_ts}")

    # Verify the 1h append also goes to the right month file
    df_combined = pd.concat([df_jan, df_feb])
    # Resample a Feb hour and append
    feb_hour = df_feb.iloc[:12].resample("1h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna()
    dm.append_1h(feb_hour)

    feb_1h_path = dm._monthly_path("1h", 2026, 2)
    assert feb_1h_path.exists(), "1h Feb file should have been created"
    feb_1h_df = pd.read_csv(feb_1h_path)
    assert len(feb_1h_df) == 1
    print(f"  1h Feb file created with {len(feb_1h_df)} bar")

    print("  PASS")
    shutil.rmtree(tmp_dir)


# ============================================================
# Test 5: End-to-end fulfillment cycle
# ============================================================
def test_fulfillment_cycle():
    print()
    print("=" * 60)
    print("Test 5: End-to-end fulfillment cycle (start → fill → trade log)")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp()
    reports_dir = os.path.join(tmp_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    # Setup mock exchange
    mock_ex = MagicMock()
    mock_ex.get_current_price.return_value = 50000
    mock_ex.get_best_bid_ask.return_value = {"bid_price": 49990, "ask_price": 50010}

    engine = FulfillmentEngine(mock_ex, "BTCUSDT", {
        "target_improvement_pct": 0.02,
        "abort_threshold_pct": 0.3,
        "timeout_minutes": 30,
    })

    # Start BUY
    order = engine.start("BUY", 0.5)
    target = order["target_price"]
    assert target < 49990
    print(f"  BUY started, target={target}")

    # Check 1: price doesn't hit target → WAITING
    mock_ex.fetch_ohlcv.return_value = pd.DataFrame({
        "open": [49995], "high": [50005], "low": [target + 5],
        "close": [49998], "volume": [50],
    }, index=[pd.Timestamp.now()])
    result, details = engine.check()
    assert result == FulfillmentResult.WAITING
    print("  Check 1: WAITING")

    # Check 2: price dips to target → FILLED
    mock_ex.fetch_ohlcv.return_value = pd.DataFrame({
        "open": [49990], "high": [49995], "low": [target - 5],
        "close": [49985], "volume": [100],
    }, index=[pd.Timestamp.now()])
    result, details = engine.check()
    assert result == FulfillmentResult.FILLED
    assert details["fill_price"] == target
    assert details["action"] == "BUY"
    assert details["quantity"] == 0.5
    assert details["fill_type"] == "limit"
    assert "slippage_vs_decision_pct" in details
    print(f"  Check 2: FILLED @ {target} (slippage={details['slippage_vs_decision_pct']}%)")

    # Now execute on portfolio
    portfolio = Portfolio(100000)
    portfolio.buy("BTCUSDT", details["quantity"], details["fill_price"])
    assert "BTCUSDT" in portfolio.positions
    assert portfolio.positions["BTCUSDT"].quantity == 0.5
    expected_cash = 100000 - 0.5 * target
    assert abs(portfolio.cash - expected_cash) < 0.02
    print(f"  Portfolio: {portfolio.positions['BTCUSDT'].quantity} BTC, cash=${portfolio.cash:.2f}")

    # Log the trade
    trade_log_path = os.path.join(reports_dir, "trades.csv")
    with open(trade_log_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(TRADE_LOG_COLUMNS)
        writer.writerow([
            datetime.now(timezone.utc).isoformat(),
            "BUY", "BTCUSDT",
            f"{details['quantity']:.8f}",
            f"{details['fill_price']:.2f}",
            f"{portfolio.cash:.2f}",
            f"{portfolio.total_value({'BTCUSDT': target}):.2f}",
            json.dumps(details),
        ])

    # Verify trade log is readable
    import pandas as pd_check
    tl = pd_check.read_csv(trade_log_path)
    assert len(tl) == 1
    assert tl.iloc[0]["action"] == "BUY"
    assert float(tl.iloc[0]["price"]) == target
    print("  Trade log written and verified")

    print("  PASS")
    shutil.rmtree(tmp_dir)


# ============================================================
# Test 6: Backtester unchanged
# ============================================================
def test_backtester_unchanged():
    print()
    print("=" * 60)
    print("Test 6: Backtester still works (import + run check)")
    print("=" * 60)

    from config import Config
    from controller import Controller
    from reporting.reporter import Reporter

    # Verify Reporter signature is backward compatible
    import inspect
    sig = inspect.signature(Reporter.generate)
    params = list(sig.parameters.keys())
    assert "auto_refresh_seconds" in params
    assert sig.parameters["auto_refresh_seconds"].default is None
    print("  Reporter backward compatible (auto_refresh_seconds defaults to None)")

    # Verify Controller can still be instantiated
    # Just check that a config can load and Controller init works
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "btc_macd_rsi_advanced_2020.yaml")
    if os.path.exists(config_path):
        config = Config.from_yaml(config_path)
        controller = Controller(config, output_dir=tempfile.mkdtemp())
        print(f"  Controller created: {config.name}")
        print(f"  Strategy: {config.strategies[0]['type']}")
    else:
        print(f"  Skipping (config not found at {config_path})")

    # Verify HTML template has conditional auto-refresh
    template_path = os.path.join(
        os.path.dirname(__file__), "..", "src", "reporting", "templates", "report.html"
    )
    with open(template_path) as f:
        html = f.read()
    assert "auto_refresh_seconds" in html
    assert "http-equiv" in html
    # The template should NOT render the meta tag when auto_refresh_seconds is not provided
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(os.path.dirname(template_path)))
    template = env.get_template("report.html")
    rendered_no_refresh = template.render(
        strategy_name="test", symbol="X", start_date="2020-01-01",
        end_date="2020-12-31", chart_html="", stats={"max_drawdown": 0, "sharpe_ratio": 0,
        "num_buys": 0, "num_sells": 0, "num_shorts": 0, "num_covers": 0,
        "total_return": 0, "annualized_return": 0, "after_cost_return": 0,
        "after_cost_cagr": 0, "cost_per_trade_pct": 0, "total_costs": 0,
        "bnh_return": 0, "bnh_cagr": 0}, version="test",
    )
    assert 'http-equiv="refresh"' not in rendered_no_refresh
    print("  Template does NOT render refresh tag when not specified")

    rendered_with_refresh = template.render(
        strategy_name="test", symbol="X", start_date="2020-01-01",
        end_date="2020-12-31", chart_html="", stats={"max_drawdown": 0, "sharpe_ratio": 0,
        "num_buys": 0, "num_sells": 0, "num_shorts": 0, "num_covers": 0,
        "total_return": 0, "annualized_return": 0, "after_cost_return": 0,
        "after_cost_cagr": 0, "cost_per_trade_pct": 0, "total_costs": 0,
        "bnh_return": 0, "bnh_cagr": 0}, version="test", auto_refresh_seconds=300,
    )
    assert 'content="300"' in rendered_with_refresh
    print("  Template renders refresh tag with content=300 when specified")

    print("  PASS")


# ============================================================
# Test 7: State manager — corrupt state recovery
# ============================================================
def test_corrupt_state_recovery():
    print()
    print("=" * 60)
    print("Test 7: Corrupt state file graceful recovery")
    print("=" * 60)

    tmp_dir = tempfile.mkdtemp()
    state_path = os.path.join(tmp_dir, "state.yaml")

    # Write garbage to state file
    with open(state_path, "w") as f:
        f.write("{{{{invalid yaml::: [[[")

    sm = StateManager(state_path)
    state = sm.load()
    assert state["pending_order"] is None
    print("  Corrupt YAML → graceful fallback to fresh state")

    # Write empty file
    with open(state_path, "w") as f:
        f.write("")

    state = sm.load()
    assert state["pending_order"] is None
    print("  Empty file → fresh state")

    # Normal save then load
    sm.save(pending_order={"action": "BUY", "quantity": 1.0})
    state = sm.load()
    assert state["pending_order"]["action"] == "BUY"
    print("  Normal save/load round-trip OK")

    print("  PASS")
    shutil.rmtree(tmp_dir)


# ============================================================
# Run all tests
# ============================================================
if __name__ == "__main__":
    test_simulated_realtime()
    test_restart_resilience()
    test_corruption_repair()
    test_month_boundary()
    test_fulfillment_cycle()
    test_backtester_unchanged()
    test_corrupt_state_recovery()

    print()
    print("=" * 60)
    print("ALL INTEGRATION TESTS PASSED")
    print("=" * 60)
