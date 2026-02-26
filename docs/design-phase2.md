# Phase 2: Paper Trading — High-Level Design

## Overview

A long-running daemon that feeds real-time price data from Binance into the existing strategy engine, simulates trade execution with realistic limit-order fulfillment, and continuously updates reports. Reuses all Phase 1 infrastructure (strategies, portfolio, reporting) without modification.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        PaperTrader                              │
│                     (daemon main loop)                          │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ DataManager  │  │StrategyRunner│  │ FulfillmentEngine    │   │
│  │              │  │              │  │                      │   │
│  │ • gap-fill   │  │ • wraps      │  │ • target price calc  │   │
│  │ • append 5m  │  │   Strategy   │  │ • 1m price polling   │   │
│  │ • resample   │  │ • incremental│  │ • fill / abort logic │   │
│  │   5m → 1h    │  │   on_bar()   │  │ • slippage tracking  │   │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘   │
│         │                 │                     │               │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────────┴───────────┐   │
│  │ExchangeClient│  │  Portfolio   │  │   ReportManager      │   │
│  │ (abstract)   │  │  (reused)    │  │ • hourly + on-trade  │   │
│  │              │  │              │  │ • fulfillment details│   │
│  │ Binance impl │  │              │  │                      │   │
│  └──────────────┘  └──────────────┘  └──────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    StateManager                          │   │
│  │  • saves to YAML on every material event                 │   │
│  │  • portfolio, strategy state, pending orders, timestamps │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. ExchangeClient (abstract + Binance implementation)

Generic interface for exchange operations. Only public market-data endpoints — no API key required for paper trading.

**Interface:**

```python
class ExchangeClient(ABC):
    def fetch_ohlcv(self, symbol, interval, start_time_ms=None,
                    end_time_ms=None, limit=1000) -> pd.DataFrame:
        """Fetch OHLCV bars. Returns DataFrame with standard columns:
        open_time, open, high, low, close, volume, close_time."""

    def get_current_price(self, symbol) -> float:
        """Current last traded price."""

    def get_best_bid_ask(self, symbol) -> dict:
        """Best bid/ask: {bid_price, bid_qty, ask_price, ask_qty}."""
```

The interface uses generic names. Each exchange implementation maps these to its own API (e.g., Binance's `/api/v3/klines`, `/api/v3/ticker/price`, `/api/v3/ticker/bookTicker`).

**Binance implementation (`BinanceRestClient`):**

Maps generic interface to Binance REST endpoints:

| Generic method | Binance endpoint | Weight | Max per call | Frequency |
|---|---|---|---|---|
| `fetch_ohlcv(interval="5m")` | `GET /api/v3/klines` | 2 | 1000 bars (~3.5 days) | Every 5 min |
| `fetch_ohlcv(interval="1m")` | `GET /api/v3/klines` | 2 | 1000 bars (~16.7 hours) | Every 1 min during fulfillment |
| `get_current_price()` | `GET /api/v3/ticker/price` | 2 | — | On trade decision |
| `get_best_bid_ask()` | `GET /api/v3/ticker/bookTicker` | 2 | — | On trade decision |
| Historical backfill | `fetch_ohlcv()` paginated | 2 per call | 1000 bars per page | On startup only |

Rate limit budget: Binance allows 6,000 weight/min. Steady-state usage: ~0.4 weight/min (one 5m kline fetch). During fulfillment: ~2.4 weight/min (adding 1m kline checks). Well within limits.

Base URL: `https://api.binance.us` (default; `api.binance.com` returns HTTP 451 from the US). Configurable to switch to international Binance or other failover endpoints.

**Response format:** The `/api/v3/klines` endpoint returns arrays in the same format as the downloaded CSV files from Binance Data Vision, so the existing `BinanceKlineParser` can be reused directly after minimal column mapping.

**Short selling note:** For Phase 2 (paper trading), short positions are simulated locally — no exchange support needed. For Phase 3 (real trading), short selling requires margin trading on the exchange. Binance (international) fully supports margin + futures shorting. Binance.US offers margin trading but with restrictions that vary by state and regulatory status — availability should be verified before going live. The exchange abstraction is designed so a different exchange (Kraken, Bybit, etc.) can be swapped in if Binance.US doesn't support the required features.

---

### 2. DataManager

Manages local data files and ensures continuity between historical and live data.

**Responsibilities:**

1. **Gap detection on startup:** Compare local CSV file's latest timestamp against current time. Calculate missing 5m bars.

2. **Historical backfill:** Fetch missing bars one calendar day at a time (UTC midnight to midnight). Each day has 288 five-minute bars, well within the 1000-bar API limit per call. For a 250-hour warm-up (~11 days), that's 11 calls. Clean day boundaries make gap detection simple — just check which date files or date ranges are missing — and align with how Binance Data Vision organizes its historical downloads. Store locally in the same CSV format the backtester uses.

3. **Warm-up guarantee:** On first start with no local data, fetch at least 250 hours (~10.5 days) of 5m data. This provides enough history for EMA-200 warm-up on 1h resampled bars. If an existing data file covers the needed range, skip fetching.

4. **Live append:** Every 5 minutes, fetch the latest closed 5m kline and append one row to the local CSV. The kline is only appended once its `close_time` has passed (i.e., the bar is finalized).

5. **Hourly resampling:** Track when 5m bars cross an hour boundary. When 12 consecutive 5m bars complete an hour (XX:00 through XX:55), resample them into a single 1h OHLCV bar and signal the StrategyRunner.

**File layout:**

```
data/live/
├── BTCUSDT-5m-2026-01.csv  # January 2026 5m bars
├── BTCUSDT-5m-2026-02.csv  # February 2026 5m bars (current month, appended every 5 min)
├── BTCUSDT-1h-2026-01.csv  # January 2026 resampled 1h bars
├── BTCUSDT-1h-2026-02.csv  # February 2026 resampled 1h bars (appended every hour)
└── state.yaml              # Daemon state (see StateManager)
```

Files are named `{symbol}-{interval}-{YYYY}-{MM}.csv`, one file per calendar month. On month rollover, a new file is created automatically. Old months remain on disk for future analysis.

The 5m CSVs use the same column format as the backtester's data files (`open_time, open, high, low, close, volume, close_time, ...`). This means the existing `BinanceKlineParser` works without changes.

**On startup, only load what's needed:** The warm-up requires ~11 days of 5m data. DataManager loads at most the current month + previous month (~17K rows max). Older months are left on disk untouched.

**Deduplication:** Before appending, check if the bar's `open_time` already exists in the current month's file. This handles restarts that might re-fetch the same bar.

---

### 3. StrategyRunner

Wraps the existing `StrategyBase` subclass and manages incremental bar feeding.

**Key design decision:** The existing strategy's `prepare()` method precomputes indicators on the full dataset. For live trading, we call `prepare()` once on startup with all available historical data, then call `on_bar()` for each new bar with the full `data_so_far` DataFrame (historical + newly appended bars).

**Startup sequence (reconstruct from data files + trade log):**

1. Load 5m data from the current + previous month CSVs.
2. Instantiate the strategy and call `strategy.prepare(full_5m_data)` — all indicators (MACD, RSI, ADX, ATR, EMA, OBV) are now precomputed.
3. **Reconstruct portfolio from trade log:** Read the CSV trade log, replay each BUY/SELL/SHORT/COVER on a fresh `Portfolio(initial_cash)`. The trade log is the source of truth — not the strategy logic. This means a strategy version upgrade won't cause divergence.
   - **Cross-check:** After reconstruction, independently verify cash and positions using a separate calculation (same approach as the existing `TradeReplayVerifier`). If the two disagree, log a warning with the discrepancy details — this catches bugs in the trade log or reconstruction logic.
   - **Price sanity check:** Query the exchange for the current price and compare against the last price in our local data. Log a warning if they differ by more than a reasonable threshold (e.g., 1%), which would indicate stale or corrupt local data.
   - **Phase 3 extension:** In real trading, this step will also query the exchange for actual balances and positions. If exchange state differs from the reconstructed local state, warn and trust the exchange as the final authority.
4. **Derive strategy tracking state** from the trade log + precomputed indicators (no `on_bar()` replay):
   - `_prev_macd`, `_prev_signal`, `_prev_rsi`, `_prev_histogram` — look up the last 1h bar's values in the precomputed indicator arrays
   - `_entry_price` — last BUY or SHORT price from the trade log (if currently in a position)
   - `_peak_since_entry` — `max(high)` of all 5m bars since the last BUY timestamp
   - `_trough_since_entry` — `min(low)` of all 5m bars since the last SHORT timestamp
   - `_bars_since_exit` — count 1h bars since the last SELL/COVER timestamp
   - `_last_exit_profitable` — compare last exit price vs its entry price in the trade log
   - `_pending_cross_bars` — reset to 0 (any pending cross from before shutdown is expired)
5. If a `pending_order` exists in the state file, hand it to the FulfillmentEngine.

This approach is **version-safe**: if the strategy logic changes between restarts (e.g., v9 → v10), the portfolio reflects what actually happened (from the trade log), not what the new logic would have done. The new version picks up cleanly from the current state and applies its logic going forward.

**On new 1h bar:**

1. Append the new 1h bar to the in-memory DataFrame **and** to the monthly 1h CSV file (`BTCUSDT-1h-YYYY-MM.csv`). On restart, the 1h bars are loaded directly from the CSV — no need to re-derive from 5m data.
2. Re-run `prepare()` on the updated dataset (see below).
3. Call `strategy.on_bar(date, row, data_so_far, is_last_bar=False)`.
4. If the returned `Action` is BUY/SELL/SHORT/COVER, pass it to the FulfillmentEngine.
5. If HOLD, do nothing.

**Incremental prepare():** The MACD RSI Advanced strategy precomputes indicators in `prepare()` using the resampled DataFrame. When a new 1h bar arrives, we need to incrementally update the indicator series. Two approaches:

- **Option A (simple):** Re-run `prepare()` on the entire growing dataset each hour. Computationally cheap — 1h bars accumulate slowly (~8,760/year). A few thousand rows of indicator computation takes milliseconds.
- **Option B (optimized):** Extend the precomputed indicator series incrementally. More complex, risk of drift.

**Decision: Option A.** Simplicity wins. Re-running `prepare()` hourly on the full dataset is negligible cost and guarantees indicator correctness.

---

### 4. FulfillmentEngine

Simulates limit-order execution with realistic price mechanics.

**When a trade signal fires:**

1. **Capture the decision context:**
   - `decision_time`: current timestamp
   - `decision_price`: last traded price (from `ticker/price`)
   - `bid_price`, `ask_price`: from `ticker/bookTicker`
   - `action`: BUY, SELL, SHORT, or COVER

2. **Calculate the target price:**
   - **BUY / COVER** (we're buying): `target = bid_price * (1 - target_improvement_pct)`
     Try to buy slightly below the current bid. We're placing a limit buy.
   - **SELL / SHORT** (we're selling): `target = ask_price * (1 + target_improvement_pct)`
     Try to sell slightly above the current ask. We're placing a limit sell.
   - `target_improvement_pct` default: **0.02%** (2 basis points). For BTCUSDT at $85,000, this is ~$17 improvement per trade.

3. **Set abort conditions:**
   - **Timeout:** `fulfillment_timeout_minutes` (default: **30 minutes**)
   - **Adverse movement:** `abort_threshold_pct` (default: **0.3%**)
     - For BUY: abort if price rises above `decision_price * (1 + abort_threshold_pct)`
     - For SELL: abort if price drops below `decision_price * (1 - abort_threshold_pct)`
   - On abort: configurable `on_abort` action — `"market"` (fill at current price) or `"cancel"` (give up entirely). Default: `"market"`.

4. **Polling loop (every ~60 seconds):**
   - Fetch the latest closed 1m kline.
   - For BUY/COVER: check if `low <= target_price`. If yes → filled at `target_price`.
   - For SELL/SHORT: check if `high >= target_price`. If yes → filled at `target_price`.
   - Check abort conditions (adverse movement or timeout).
   - If neither filled nor aborted, continue polling.

5. **On fill:**
   - Execute on the Portfolio (`buy()`, `sell()`, `short_sell()`, or `cover()`).
   - Log to CSV with fulfillment details.
   - Trigger report regeneration.

6. **On abort with `on_abort: "market"`:**
   - Fill at the current last price (simulating a market order conversion).
   - Log with fulfillment details noting the market fill.

7. **On abort with `on_abort: "cancel"`:**
   - Do not execute. Log as a cancelled trade attempt.
   - The strategy will get a chance to re-signal on the next hourly bar if conditions still hold.

**Fulfillment detail fields** (added to the CSV `details` JSON):

```json
{
  "reason": "MACD golden cross (3.2bps) + RSI/ADX/EMA",
  "fulfillment": {
    "decision_time": "2026-02-26T14:00:00Z",
    "decision_price": 85500.00,
    "bid_at_decision": 85498.50,
    "ask_at_decision": 85501.50,
    "target_price": 85481.30,
    "fill_time": "2026-02-26T14:07:00Z",
    "fill_price": 85481.30,
    "fill_type": "limit",
    "time_to_fill_seconds": 420,
    "checks_count": 7,
    "slippage_vs_decision_pct": -0.022,
    "price_range_during_fill": [85350.0, 85620.0]
  }
}
```

**Interaction with the 5m data loop:** Single-threaded — no separate threads. During fulfillment, the main loop runs at 1-minute intervals instead of 5-minute. On each 1-minute wake-up, it checks the fulfillment AND checks if a 5-minute boundary has passed (i.e., the faster poll subsumes the slower one). If a 5m bar is due, it fetches and appends it. If a new hourly bar forms while a fulfillment is pending, the strategy is NOT consulted — we don't feed new bars to the strategy until the pending fulfillment resolves (filled or aborted). This prevents conflicting signals.

---

### 5. StateManager

Persists only what cannot be reconstructed from the data files: in-flight fulfillment orders.

Strategy state, portfolio, and indicator values are all reconstructed by replaying bars on startup (see StrategyRunner). Data timestamps are derived from the CSV files themselves — the DataManager reads the last row of the current month's file to determine where to resume fetching, which also serves as an integrity check.

**State file (`state.yaml`):**

```yaml
version: 1
last_updated: "2026-02-26T15:07:00Z"

pending_order: null
# or:
# pending_order:
#   action: "BUY"
#   quantity: 0.51234567
#   decision_time: "2026-02-26T14:00:00Z"
#   decision_price: 85500.00
#   target_price: 85481.30
#   abort_price: 85756.50
#   timeout_at: "2026-02-26T14:30:00Z"
#   checks: 3
```

If there is no pending order, the state file can be empty or absent — the system is fully recoverable from the data files alone.

**When to save:**
- When a fulfillment order is created (pending_order set)
- When a fulfillment resolves (pending_order cleared)
- On clean shutdown (SIGTERM/SIGINT handler)

**Save strategy:** Atomic write (write to temp file, then rename) to prevent corruption from crashes mid-write.

**Data file integrity on startup:** DataManager validates the CSV files before use:
1. Read the last row — verify `open_time` is parseable and monotonically increasing.
2. Check for obvious corruption (truncated rows, non-numeric fields, timestamps out of order).
3. If corruption is detected at the tail (e.g., partial write from a crash), truncate to the last valid row and log a warning.
4. If corruption is deeper, fail with a clear error message identifying the file and line.

---

### 6. ReportManager

Triggers report regeneration using the existing `Reporter` class.

**When to regenerate:**
- Every hour (after strategy processes the new 1h bar)
- On every trade execution (fill or market-abort)

**How:**
- Reads the growing trade log CSV (same format as backtester output)
- Calls the existing `Reporter.generate()` with updated price data
- Overwrites the same HTML file each time (not versioned — it's a living report)

**Auto-refresh in browser:** Paper trading reports include a `<meta http-equiv="refresh" content="300">` tag, causing the browser to reload every 5 minutes. Backtest reports are unaffected (no meta tag). This is passed as a flag to the Reporter so it can inject the tag into the template.

**Additional report content for paper trading:**
- Fulfillment statistics summary:
  - Average time to fill
  - % of fills at limit vs market
  - Average slippage vs decision price
  - Number of aborted attempts
- These are computed from the `fulfillment` objects in the CSV `details` column

---

### 7. Logging

All major operations log to a rotating log file using Python's standard `logging` module. Log to both file and stdout (useful when running interactively, file persists for post-mortem analysis).

**Log file:** `data/live/paper_trader.log` (configurable). Rotated daily, keep last 30 days.

**What gets logged:**

| Level | Event |
|---|---|
| INFO | Daemon started / stopped (with config summary, portfolio value) |
| INFO | Historical backfill: fetching N days, progress per day |
| INFO | Data integrity check: files validated, rows loaded, any repairs |
| INFO | Portfolio reconstructed: cash, positions, cross-check result |
| INFO | New 5m bar appended (timestamp, close price) |
| INFO | New 1h bar formed (timestamp, OHLCV summary) |
| INFO | Strategy `on_bar()` decision: action + full reason string from details |
| INFO | Fulfillment started: action, decision price, target price, abort threshold |
| INFO | Fulfillment check: current price, target, status (waiting/filled/aborted) |
| INFO | Trade executed: action, fill price, fill type (limit/market), time to fill |
| INFO | Report regenerated |
| WARNING | Data gap detected on startup (from–to, number of missing bars) |
| WARNING | CSV tail corruption detected and repaired |
| WARNING | Portfolio cross-check mismatch (expected vs actual) |
| WARNING | Price sanity check: local vs exchange price divergence |
| WARNING | Exchange API retry (attempt N, error details) |
| WARNING | Fulfillment aborted: reason (adverse movement / timeout) |
| ERROR | Exchange API failed after all retries |
| ERROR | CSV corruption too deep to auto-repair |
| ERROR | Unhandled exception (with traceback) |

**Example log output:**

```
2026-02-26 14:01:02 INFO  Appended 5m bar: 2026-02-26 14:00 close=85,432.50
2026-02-26 14:01:02 INFO  Hourly bar formed: 2026-02-26 13:00 O=85,100 H=85,600 L=85,050 C=85,432 V=1,234
2026-02-26 14:01:02 INFO  prepare() updated: 8,761 1h bars
2026-02-26 14:01:03 INFO  on_bar() → BUY | MACD golden cross (3.2bps) + RSI/ADX/EMA
2026-02-26 14:01:03 INFO  Fulfillment started: BUY target=85,415.14 abort=85,688.80 timeout=14:31:03
2026-02-26 14:02:01 INFO  Fulfillment check: 1m low=85,420.00 > target → waiting
2026-02-26 14:03:01 INFO  Fulfillment check: 1m low=85,390.00 <= target → FILLED at 85,415.14
2026-02-26 14:03:01 INFO  Trade executed: BUY 0.5843 BTCUSDT @ 85,415.14 (limit, 120s to fill)
2026-02-26 14:03:02 INFO  Report regenerated: reports/live/report.html
```

---

### 8. PaperTrader (daemon main loop)

The top-level orchestrator.

**Startup:**

```
1. Load config (paper trading YAML)
2. Initialize ExchangeClient (BinanceRestClient)
3. Initialize DataManager
   a. Check local 5m CSV — find latest timestamp
   b. If no data or data older than warm-up requirement:
      fetch historical 5m klines from Binance (paginated)
      Store to local CSV
   c. If gap between local data and now:
      fetch missing 5m bars, append to local CSV
4. Initialize StrategyRunner
   a. Load or create Portfolio
   b. Instantiate strategy from registry
   c. Call prepare() with full historical data
   d. If state file exists, restore strategy internal state
5. Initialize FulfillmentEngine
   a. If pending order in state file, resume monitoring it
6. Initialize ReportManager
7. Log: "Paper trader started. Portfolio value: $X. Last bar: YYYY-MM-DD HH:MM"
```

**Main loop (single-threaded, every ~60 seconds):**

```
while running:
    now = current UTC time

    # 1. Data collection — runs every 5 minutes regardless of fulfillment state
    if time_for_5m_fetch(now):
        new_bar = data_manager.fetch_and_append_5m()

        # 2. Hourly strategy evaluation (only if no pending fulfillment)
        if new_bar and is_hour_boundary(new_bar):
            hourly_bar = data_manager.resample_latest_hour()
            data_manager.append_1h(hourly_bar)

            if not pending_order:
                action = strategy_runner.on_new_bar(hourly_bar)

                if action.action != HOLD:
                    pending_order = fulfillment_engine.start(action)
                    save state

            regenerate report (hourly update)

    # 3. Fulfillment check — runs every minute while order is pending
    if pending_order:
        result = fulfillment_engine.check()
        if result == FILLED or result == ABORTED:
            execute on portfolio (if filled/market)
            log to CSV
            save state
            regenerate report
            pending_order = None

    # Sleep interval adapts: 1 min during fulfillment, 5 min otherwise
    sleep until next minute boundary
```

The loop always runs at 1-minute intervals when a fulfillment is pending (to poll 1m price data), and relaxes to 5-minute intervals when idle. The 5m data fetch triggers on 5-minute boundaries regardless — the faster polling during fulfillment naturally subsumes the slower schedule.

**Shutdown handling:**
- Register SIGTERM and SIGINT handlers
- On signal: set `running = False`, save state, exit cleanly
- On unhandled exception: save state, log error, exit with non-zero code

**Scheduling precision:** The loop wakes up every ~60 seconds aligned to minute boundaries. The 5m fetch triggers when `minute % 5 == 1` (one minute after bar close, ensuring the bar is finalized). This is simple and avoids complex scheduling libraries.

---

## Configuration

```yaml
mode: "paper_trading"

paper_trading:
  symbol: "BTCUSDT"
  initial_cash: 100000
  data_dir: "data/live"
  state_file: "data/live/state.yaml"
  warm_up_hours: 250       # Minimum historical hours for indicator warm-up

exchange:
  type: "binance"
  base_url: "https://api.binance.us"
  request_timeout_seconds: 10
  max_retries: 3

fulfillment:
  target_improvement_pct: 0.02   # 2bps better than market (limit order)
  abort_threshold_pct: 0.3       # Abort if price moves 0.3% against us
  timeout_minutes: 30            # Max time to attempt fill
  on_timeout: "market"           # "market" = fill at current price, "cancel" = give up
  check_interval_seconds: 60     # Poll 1m klines this often during fulfillment

strategy:
  type: "macd_rsi_advanced"
  version: "v9"
  params:
    resample_interval: "1h"
    macd_fast: 12
    macd_slow: 26
    macd_signal: 9
    rsi_period: 14
    rsi_entry_low: 40
    rsi_overbought: 70
    rsi_exit_confirm: 65
    adx_period: 14
    adx_threshold: 20
    atr_period: 14
    atr_stop_multiplier: 3.0
    atr_trailing_multiplier: 3.0
    stop_loss_pct: 8.0
    trailing_stop_pct: 8.0
    ema_trend_period: 200
    cooldown_bars: 4
    exit_on_macd_cross: false
    trend_reentry: true
    trend_reentry_cooldown: 2
    trend_reentry_rsi_max: 70
    enable_short: true
    short_adx_threshold: 25
    short_rsi_entry_high: 60
    short_rsi_oversold: 30
    short_rsi_exit_confirm: 35
    short_stop_loss_pct: 6.0
    short_trailing_stop_pct: 6.0
    short_size_pct: 50
    min_cross_hist_bps: 2.0
    cross_confirm_window: 2

reporting:
  output_dir: "reports/live"
  trade_log: "reports/live/trades.csv"
  report_file: "reports/live/report.html"
  cost_per_trade_pct: 0.05
```

---

## File Layout (new files)

```
src/
├── exchange/
│   ├── __init__.py
│   ├── base.py              # ExchangeClient ABC
│   └── binance_rest.py      # BinanceRestClient implementation
├── paper_trading/
│   ├── __init__.py
│   ├── daemon.py            # PaperTrader main loop
│   ├── data_manager.py      # DataManager (gap-fill, append, resample)
│   ├── strategy_runner.py   # StrategyRunner (wraps existing strategies)
│   ├── fulfillment.py       # FulfillmentEngine
│   ├── state_manager.py     # StateManager (YAML persistence)
│   └── report_manager.py    # ReportManager (triggers report regen)
├── strategies/              # No changes needed — replay reconstructs state

config/
└── paper_trading.yaml       # Paper trading config

data/live/                   # Created at runtime
├── BTCUSDT-5m-2026-01.csv  # One file per month (5m bars)
├── BTCUSDT-5m-2026-02.csv
├── BTCUSDT-1h-2026-01.csv  # One file per month (resampled 1h bars)
├── BTCUSDT-1h-2026-02.csv
├── state.yaml
└── paper_trader.log         # Rotating daily log (last 30 days)

run_paper_trader.py          # Entry point: python run_paper_trader.py config/paper_trading.yaml
```

---

## Changes to Existing Code

Minimal changes — Phase 1 code remains fully functional:

1. **`BinanceKlineParser`** — Add a `parse_api_response(data: list[list]) -> pd.DataFrame` method that handles the REST API's array-of-arrays format (vs the CSV string format used by `parse()`). The column mapping is identical.

2. **`MACDRSIAdvancedStrategy`** — The StrategyRunner needs to set a handful of tracking fields (`_entry_price`, `_peak_since_entry`, `_bars_since_exit`, etc.) derived from the trade log and data. The StrategyRunner sets these directly on the strategy instance after construction — no new interface method needed, just direct attribute assignment from the reconstruction logic.

3. **No changes** to StrategyBase, Controller, Portfolio, Reporter, TradeLogger, or any config files. The paper trader creates its own instances of Portfolio and Strategy, independent of the backtester.

---

## Fulfillment Parameter Rationale

| Parameter | Default | Rationale |
|---|---|---|
| `target_improvement_pct` | 0.02% | ~$17 on BTC at $85k. BTCUSDT spread is typically 0.01% ($8.50), so 2bps improvement is achievable but not trivial. Provides meaningful savings over market orders (~$34/round-trip). |
| `abort_threshold_pct` | 0.3% | ~$255 on BTC. If price moves this much against our intended direction, the opportunity cost of waiting exceeds the benefit of a better fill. At 0.3%, we're aborting roughly once per 5-10 trade attempts based on BTC 1m volatility patterns. |
| `timeout_minutes` | 30 | Half an hour gives sufficient time for price to revisit our target in normal conditions. Beyond 30 min, the market microstructure that prompted the trade signal may have changed. |
| `on_timeout` | `"market"` | When the strategy signals a trade, it's because macro conditions (MACD, RSI, ADX, EMA) align. Missing the trade entirely because we couldn't get 2bps improvement is worse than paying market price. Default to market fill on timeout. |

---

## Sequence Diagrams

### Normal flow: 5m bar → hourly strategy evaluation → trade

```
Time     PaperTrader          DataManager       StrategyRunner    FulfillmentEngine
─────────────────────────────────────────────────────────────────────────────────
14:01    wake up
         ├─ fetch_5m() ──────► fetch from Binance
         │                     append to CSV
         │                     return bar (14:00)
         │
         │  is_hour_boundary?  YES (14:00)
         ├─ resample_hour() ──► aggregate 13:00-13:55
         │                      append 1h bar
         │
         ├─ on_new_bar() ─────────────────────► prepare()
         │                                      on_bar()
         │                                      return Action(BUY)
         │
         ├─ start(BUY) ──────────────────────────────────────► get ticker
         │                                                     calc target
         │                                                     return pending
         │  save state
         │
14:02    wake up
         ├─ check() ─────────────────────────────────────────► fetch 1m kline
         │                                                     low > target
         │                                                     return WAITING
         │
14:03    wake up
         ├─ check() ─────────────────────────────────────────► fetch 1m kline
         │                                                     low <= target
         │                                                     return FILLED
         │
         │  portfolio.buy(target_price)
         │  log to CSV
         │  save state
         │  regenerate report
```

### Abort flow: price moves against us

```
Time     PaperTrader          FulfillmentEngine
──────────────────────────────────────────────────
14:01    start(BUY)
         target = 85,481
         abort  = 85,757

14:02    check()              1m low = 85,520 → WAITING
14:03    check()              1m low = 85,550 → WAITING
14:04    check()              1m high = 85,800 > abort → ABORT

         on_timeout = "market"
         portfolio.buy(current_price=85,780)
         log with fill_type="market_abort"
```

---

## Error Handling & Resilience

| Scenario | Handling |
|---|---|
| **Binance API timeout** | Retry up to `max_retries` with exponential backoff. If all retries fail, log warning and skip this cycle. Try again on next wake-up. |
| **Binance API rate limit (429)** | Back off for the duration specified in the `Retry-After` header. Should not happen at our request rate. |
| **Network down** | Same as timeout. The daemon keeps running and retries. Data gaps in the CSV are filled on next successful fetch. |
| **Crash during fulfillment** | State file has `pending_order`. On restart, portfolio is reconstructed from trade log, indicators from `prepare()`, then resume fulfillment monitoring. If timeout has passed, treat as expired. |
| **Crash mid-write to CSV** | On startup, DataManager validates the tail of the CSV. Truncated/partial rows are trimmed to the last valid row (with a warning). The gap is then backfilled from the exchange. |
| **Stale data on restart** | DataManager reads the last valid `open_time` from the CSV, compares to current time, and backfills the gap from the exchange before reconstruction. |
| **Corrupt CSV (deeper)** | DataManager detects non-monotonic timestamps or unparseable rows. Fails with a clear error identifying the file and line, so the operator can inspect and fix. |
| **Duplicate 5m bars** | Deduplication check before append (compare `open_time` with last row in CSV). |

---

## Implementation & Testing Checklist

Build sequence — each step is independently testable. Check off as completed.

### Step 1: ExchangeClient + BinanceRestClient

- [ ] Define `ExchangeClient` ABC in `src/exchange/base.py` (`fetch_ohlcv`, `get_current_price`, `get_best_bid_ask`)
- [ ] Implement `BinanceRestClient` in `src/exchange/binance_rest.py`
  - [ ] `fetch_ohlcv()` — calls `/api/v3/klines`, returns DataFrame
  - [ ] `get_current_price()` — calls `/api/v3/ticker/price`
  - [ ] `get_best_bid_ask()` — calls `/api/v3/ticker/bookTicker`
  - [ ] Retry logic with exponential backoff
  - [ ] Configurable base URL, timeout, max retries
- [ ] **Test:** Mock HTTP responses, verify DataFrame output matches expected OHLCV format
- [ ] **Test:** Live smoke test — fetch 10 bars of BTCUSDT 5m data from real Binance API

### Step 2: DataManager

- [ ] Monthly file naming: `{symbol}-{interval}-{YYYY}-{MM}.csv`
- [ ] Gap detection: read last row of current month CSV, compare to current time
- [ ] Historical backfill: fetch one day at a time, append to correct monthly file
- [ ] Warm-up: on first start, fetch at least 250 hours of 5m data
- [ ] Live append: fetch latest closed 5m bar, deduplicate, append to current month file
- [ ] Hourly resampling: detect hour boundary, aggregate 12 five-minute bars into 1h bar
- [ ] Append 1h bar to monthly 1h CSV file
- [ ] CSV integrity validation on startup (parseable, monotonic timestamps, detect truncation)
- [ ] Auto-repair truncated tail rows with warning
- [ ] Fail with clear error on deeper corruption
- [ ] Month rollover: create new file when month changes
- [ ] Load only current + previous month on startup
- [ ] **Test:** Create a mock exchange returning known data; verify gap detection and backfill produce correct CSV files
- [ ] **Test:** Simulate crash mid-write (truncated row), verify auto-repair on next startup
- [ ] **Test:** Verify 5m → 1h resampling produces identical results to the backtester's `resample_ohlcv()`
- [ ] **Test:** Month boundary rollover creates new file correctly

### Step 3: StateManager

- [ ] Save/load `state.yaml` with `pending_order` (or null)
- [ ] Atomic write (temp file + rename)
- [ ] Handle missing state file (fresh start)
- [ ] Handle empty/corrupt state file (warn, treat as fresh)
- [ ] **Test:** Save state, load it back, verify round-trip fidelity
- [ ] **Test:** Corrupt the file, verify graceful handling

### Step 4: StrategyRunner ✅

- [x] Reconstruct portfolio from trade log CSV (replay BUY/SELL/SHORT/COVER on fresh Portfolio)
- [x] Cross-check portfolio with independent verification calculation
- [x] Price sanity check: query exchange for current price, compare to local data
- [x] Derive strategy tracking state from trade log + precomputed indicators:
  - [x] `_prev_macd`, `_prev_signal`, `_prev_rsi`, `_prev_histogram` from last 1h bar
  - [x] `_entry_price` from last BUY/SHORT in trade log
  - [x] `_peak_since_entry` from max(high) of 5m bars since last BUY
  - [x] `_trough_since_entry` from min(low) of 5m bars since last SHORT
  - [x] `_bars_since_exit` count of 1h bars since last SELL/COVER
  - [x] `_last_exit_profitable` from trade log price comparison
  - [x] `_pending_cross_bars` reset to 0
- [x] Incremental `prepare()`: re-run on updated dataset when new 1h bar arrives
- [x] `on_bar()` call with updated `data_so_far`
- [x] **Test:** Synthetic trade log reconstruction verified (flat + open position + fresh start)
- [x] **Test:** Feed one more bar after reconstruction, verify strategy returns valid Action
- [x] **Test:** Cross-check portfolio cash against trade log's cash_balance column

### Step 5: FulfillmentEngine ✅

- [x] Target price calculation (buy: below bid, sell: above ask)
- [x] Abort threshold calculation (configurable %)
- [x] Timeout tracking (configurable minutes)
- [x] Poll 1m klines: check low (for buys) or high (for sells) against target
- [x] Fill execution: return fill details (Portfolio mutation handled by caller/PaperTrader)
- [x] Abort with market fill: fill at current price
- [x] Abort with cancel: log as cancelled, no portfolio change
- [x] Build fulfillment detail dict for trade log CSV
- [x] Handle expired pending order on startup (resume with timeout warning)
- [x] **Test:** BUY limit fill — price dips to target → verified
- [x] **Test:** SELL limit fill — price rises to target → verified
- [x] **Test:** BUY aborted — adverse price movement → verified market_abort
- [x] **Test:** BUY timeout → market fill → verified
- [x] **Test:** BUY waiting — price in range, no fill yet → verified
- [x] **Test:** Resume pending order from saved state → verified
- [x] **Test:** Cancel mode (on_timeout=cancel) → verified

### Step 6: ReportManager

- [ ] Thin wrapper: call existing `Reporter.generate()` with updated data
- [ ] Auto-refresh meta tag (`<meta http-equiv="refresh" content="300">`) for paper trading reports only
- [ ] Pass auto-refresh flag through Reporter to template
- [ ] Overwrite same HTML file on each regeneration
- [ ] Fulfillment statistics summary (avg time to fill, limit vs market %, slippage, aborts)
- [ ] **Test:** Generate a paper trading report, verify meta refresh tag is present
- [ ] **Test:** Generate a backtest report, verify meta refresh tag is absent
- [ ] **Test:** Verify fulfillment statistics render correctly with sample data

### Step 7: Logging ✅

- [x] Configure Python `logging` module: rotating file handler (daily, 30-day retention) + stdout
- [x] Log file location: `data/live/paper_trader.log` (configurable)
- [x] Add logging calls to all components:
  - [x] DataManager: backfill progress, 5m append, 1h formed, integrity checks
  - [x] StrategyRunner: reconstruction summary, `on_bar()` decisions with reason strings
  - [x] FulfillmentEngine: start, each check, fill/abort
  - [x] StateManager: save/load events
  - [x] Reporter: regeneration events (via PaperTrader)
  - [x] PaperTrader: startup/shutdown, config summary, portfolio value
- [x] WARNING level for: gaps, corruption repairs, cross-check mismatches, retries, aborts
- [x] ERROR level for: API failures after retries, deep corruption, unhandled exceptions
- [x] **Test:** Verified logging setup: file + stdout, re-init safety, noisy logger suppression
- [x] **Test:** TimedRotatingFileHandler configured with daily rotation + 30-day backupCount

### Step 8: PaperTrader Daemon ✅

- [x] Load config from YAML (with validation of required fields)
- [x] Initialize all components in correct order
- [x] Main loop: adaptive sleep (1 min during fulfillment, 5 min otherwise)
- [x] 5m fetch on 5-minute boundaries (minute % 5 == 1)
- [x] Fulfillment check on every wake-up when pending
- [x] Strategy evaluation on hour boundary (only when no pending fulfillment)
- [x] SIGTERM/SIGINT handler: set running=False, save state, clean shutdown
- [x] Unhandled exception handler: save state, log error, exit non-zero
- [x] Auto-refresh meta tag in paper trading report (300s)
- [x] Trade log append mode (append to existing CSV on restart)
- [x] **Test:** Config validation — valid loads, missing fields rejected
- [x] **Test:** Startup with mock exchange — all components initialized
- [x] **Test:** Tick at non-5m boundary — no-op verified
- [x] **Test:** Tick at 5m boundary — fetch triggered
- [x] **Test:** Tick with pending fulfillment — check triggered
- [x] **Test:** SIGINT sets running=False
- [x] **Test:** State save/load round-trip for pending orders
- [x] **Test:** Sleep calculation completes without error

### Step 9: Integration Testing ✅

- [x] **Simulated real-time test:** Full PaperTrader startup with mock exchange, multi-tick cycle
  - [x] Data startup + strategy init
  - [x] Tick cycles run without error
  - [x] State save/load works
- [x] **Restart resilience test:**
  - [x] Portfolio reconstructs correctly from trade log (0.5 BTC, cash matches)
  - [x] Pending fulfillment resumes from state file
  - [x] No duplicate bars in CSV
- [x] **Data corruption + repair:**
  - [x] Truncated CSV tail detected and repaired
  - [x] CSV valid after repair (row count + monotonic timestamps)
- [x] **Month boundary test:**
  - [x] Jan + Feb monthly files exist with correct row counts
  - [x] Data continuity verified (last Jan ts < first Feb ts)
  - [x] 1h append goes to correct monthly file
- [x] **End-to-end fulfillment cycle:**
  - [x] Start → WAITING → FILLED with correct target price
  - [x] Details contain action, quantity, slippage
  - [x] Portfolio updated, trade log written and verified
- [x] **Backtester unchanged:**
  - [x] Reporter backward compatible (auto_refresh_seconds defaults to None)
  - [x] Controller instantiates with existing v9 config
  - [x] HTML template: no refresh tag without arg, refresh tag with arg
- [x] **Corrupt state recovery:**
  - [x] Invalid YAML → graceful fallback
  - [x] Empty file → fresh state
  - [x] Normal save/load round-trip
- [ ] **Live Binance smoke test:** (manual — run `./run_paper_trader.sh` for ~1 hour)
