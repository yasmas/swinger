# SwingBot Refactoring Plan

## Context

The paper trader (`src/paper_trading/paper_trader.py`) tightly couples orchestration, order execution, portfolio management, and fulfillment simulation into one class. To support live trading on Binance spot or Coinbase INTX futures, we need to extract the execution/portfolio layer into a pluggable **Broker** interface. The first implementation (PaperBroker) wraps the existing simulation logic so behavior is identical. The orchestrator is renamed from `PaperTrader` to `SwingBot`.

---

## Broker Interface (`src/brokers/base.py`)

```python
class BrokerBase(ABC):
    # -- Lifecycle --
    def startup(config: dict) -> None           # Init, restore state
    def shutdown() -> None                      # Cancel orders, persist state

    # -- Capabilities --
    def capabilities() -> BrokerCapabilities    # supports_shorting, supports_margin, etc.

    # -- Portfolio (broker owns all state) --
    def get_portfolio_snapshot(prices: dict | None) -> PortfolioSnapshot  # cash, positions, total_value
    def get_position(symbol: str) -> dict | None     # {qty, avg_cost, side} or None
    def portfolio_view(symbol: str) -> PortfolioView # frozen view for strategy

    # -- Orders (broker handles sizing) --
    def submit_order(symbol, side, notional=None) -> str   # returns order_id
    def check_order(order_id) -> FillResult | None         # None = still pending
    def cancel_order(order_id) -> bool
    def has_pending_order() -> bool
    def get_pending_order_info() -> dict | None

    # -- Emergency --
    def emergency_close(symbol) -> FillResult | None   # cancel pending + market close

    # -- Crash recovery --
    def export_state() -> dict
    def import_state(state: dict) -> None
```

**Data types:**
- `OrderSide(str, Enum)`: BUY, SELL, SHORT, COVER
- `OrderStatus(str, Enum)`: PENDING, FILLED, CANCELLED, REJECTED
- `FillResult`: status, side, symbol, filled_qty, filled_price, fill_type, details
- `PortfolioSnapshot`: cash, positions dict, total_value
- `BrokerCapabilities`: supports_shorting, supports_margin, supports_leverage, max_leverage

**Key design:** The caller passes a direction + optional notional. The broker handles sizing (lot sizes, margin, cash reserves) and returns what actually happened via `FillResult`.

---

## Phase 1: Broker Interface + PaperBroker (no other files change)

**New files:**
- `src/brokers/__init__.py` — empty
- `src/brokers/base.py` (~90 lines) — ABC + data types above
- `src/brokers/paper_broker.py` (~250 lines) — wraps `Portfolio` + `FulfillmentEngine`
- `src/brokers/registry.py` (~10 lines) — `BROKER_REGISTRY = {"paper": PaperBroker}`

**PaperBroker internals:**
- `startup(config)` → creates `Portfolio(initial_cash)`, stores fulfillment config
- `submit_order(symbol, side, notional)`:
  1. Fetches current price from `self.exchange`
  2. Computes qty: entries use `notional / price` (default notional = 99.5% of cash); exits use full position qty
  3. Creates `FulfillmentEngine(exchange, symbol, ful_config)` for this order
  4. Calls `fulfillment.start(side, qty)`, stores order_id
- `check_order(order_id)`:
  1. Calls `fulfillment.check()` → WAITING/FILLED/ABORTED
  2. If filled: calls `self._portfolio.buy/sell/short_sell/cover` → returns `FillResult(FILLED, ...)`
  3. If cancelled: returns `FillResult(CANCELLED, ...)`
  4. If waiting: returns `None`
- `emergency_close(symbol)`:
  1. Cancel pending fulfillment if any
  2. Check position — if flat, return None
  3. Get market price, apply to portfolio, return FillResult
- `export_state()` → `{cash, positions, short_positions, pending_order}`
- `import_state(state)` → restore Portfolio + resume fulfillment
- `reconstruct_from_trades(trade_log_path)` — replay trade log (moved from StrategyRunner)
- `portfolio_view(symbol)` → builds `PortfolioView` from `self._portfolio` (same as existing `portfolio_view_from`)

**FulfillmentEngine** stays at `src/paper_trading/fulfillment.py` for now (moved in Phase 5). No changes to its API — PaperBroker wraps it.

**Test:** Unit test PaperBroker with mock ExchangeClient. Verify order lifecycle, portfolio state, crash recovery round-trip.

---

## Phase 2: TraderBase Config Generalization

**File:** `src/trading/trader_base.py`

**Change** (~5 lines in `__init__`):
```python
# Before:
pt = config["paper_trading"]
self.symbol = pt["symbol"]
self.initial_cash = pt["initial_cash"]

# After:
bot_cfg = config.get("bot") or config.get("paper_trading", {})
self.symbol = bot_cfg["symbol"]
self.initial_cash = bot_cfg.get("initial_cash", config.get("broker", {}).get("initial_cash", 100000))
```

The rest of TraderBase (ZMQ, signals, heartbeat) is already generic.

**Test:** Run existing PaperTrader with unchanged config — should work identically.

---

## Phase 3: SwingBot (replaces PaperTrader)

**New file:** `src/trading/swing_bot.py` (~400 lines)

SwingBot subclasses TraderBase and uses a Broker instead of directly managing Portfolio + FulfillmentEngine. Structure mirrors PaperTrader but with these key differences:

| PaperTrader | SwingBot |
|-------------|----------|
| Creates FulfillmentEngine directly | Creates Broker via registry |
| `self.strategy_runner.portfolio.buy()` | `self.broker.submit_order()` |
| `self.fulfillment_engine.check()` | `self.broker.check_order()` |
| `self.strategy_runner.portfolio` for ZMQ state | `self.broker.get_portfolio_snapshot()` |
| `portfolio_view_from(portfolio, symbol)` | `self.broker.portfolio_view(symbol)` |
| `_force_close()` mutates portfolio directly | `self.broker.emergency_close(symbol)` |

**`_startup_hook`:**
1. Create ExchangeClient (same as today)
2. Create DataManager (same)
3. Create StateManager, load state
4. Create Broker via `BROKER_REGISTRY[config["broker"]["type"]](exchange)`
5. Broker startup: `broker.startup(broker_config)` or `broker.import_state(state["broker_state"])`
6. If no broker state but trade log exists: `broker.reconstruct_from_trades(trade_log_path)`
7. Create StrategyRunner (no longer owns Portfolio — receives `portfolio_view` from broker)
8. Check capabilities, log what's supported
9. Init trade logger, reporter (same)

**`_evaluate_strategy`:**
```python
action = self.strategy_runner.on_5m_bar(self._df_5m, portfolio_view=self.broker.portfolio_view(self.symbol))
if action.action == ActionType.HOLD:
    return
# Capability gate
caps = self.broker.capabilities()
if action.action in (ActionType.SHORT, ActionType.COVER) and not caps.supports_shorting:
    logger.warning("Broker doesn't support shorting — ignoring %s signal", action.action.value)
    return
side = OrderSide(action.action.value)
self._current_order_id = self.broker.submit_order(self.symbol, side)
```

**`_check_fulfillment`:**
```python
result = self.broker.check_order(self._current_order_id)
if result is None:
    return  # still pending
if result.status == OrderStatus.FILLED:
    self._log_trade(date, result.side.value, result.filled_qty, result.filled_price, result.details)
    self._send_trade_event(...)
    self.strategy_runner.strategy.reset_position() if is_exit else ...
self._current_order_id = None
self._save_state()
```

**`_get_portfolio_state`:** reads from `self.broker.get_portfolio_snapshot()` + `self.broker.get_position(symbol)`.

**`_force_close`:** calls `self.broker.emergency_close(self.symbol)`, logs result, resets strategy.

**`_save_state`:** persists `broker_state=self.broker.export_state()` + `strategy_state`.

**`_log_trade`:** same as today but gets cash from `self.broker.get_portfolio_snapshot()` instead of `self.strategy_runner.portfolio.cash`.

**StrategyRunner changes** (in `src/paper_trading/strategy_runner.py`, moved in Phase 5):
- `on_5m_bar(df, portfolio_view=None)` — accepts external `PortfolioView` parameter
- If provided, uses it; if not, falls back to self.portfolio (backward compatible for Phase 3)
- Portfolio reconstruction and ownership still present (removed in Phase 4)

**New config format** (`config/swing_bot.yaml`):
```yaml
trader_name: "swing_trend_btc"
zmq:
  endpoint: "tcp://localhost:5555"
bot:
  symbol: "BTCUSDT"
  data_dir: "data/live"
  state_file: "data/live/state.yaml"
  warm_up_hours: 250
  fetch:
    delay_seconds: 3
    poll_interval_seconds: 3.0
    timeout_seconds: 30
broker:
  type: "paper"
  initial_cash: 100000
  fulfillment:
    target_improvement_pct: 0.001
    abort_threshold_pct: 0.1
    timeout_minutes: 30
    on_timeout: "market"
exchange:
  type: "binance"
  base_url: "https://api.binance.us"
strategy:
  type: "swing_trend"
  version: "v16"
  params: { ... }
reporting:
  trade_log: "data/live/trades.csv"
  output_dir: "reports/live"
logging:
  file: "data/live/swing_bot.log"
  level: "DEBUG"
```

`load_config()` in swing_bot.py accepts both old (`paper_trading:`) and new (`bot:` + `broker:`) formats.

**Test:** Run SwingBot with PaperBroker against the exchange. Compare trade log output with a PaperTrader run on the same data window.

---

## Phase 4: Remove Portfolio from StrategyRunner

**File:** `src/paper_trading/strategy_runner.py` (or after move: `src/trading/strategy_runner.py`)

- Remove `self.portfolio = Portfolio(...)` from `__init__`
- Remove `_reconstruct_portfolio()` — this now lives in PaperBroker
- Remove `_cross_check_portfolio()` — moved to PaperBroker
- `on_5m_bar(df, portfolio_view)` — `portfolio_view` is now required, no fallback
- StrategyRunner becomes purely about strategy lifecycle: prepare, on_bar, diagnostics, state export/import

**Test:** Run SwingBot end-to-end. Verify StrategyRunner has no portfolio attribute.

---

## Phase 5: File Moves + Rename

**Moves:**
| From | To |
|------|-----|
| `src/paper_trading/data_manager.py` | `src/trading/data_manager.py` |
| `src/paper_trading/strategy_runner.py` | `src/trading/strategy_runner.py` |
| `src/paper_trading/state_manager.py` | `src/trading/state_manager.py` |
| `src/paper_trading/logging_config.py` | `src/trading/logging_config.py` |
| `src/paper_trading/fulfillment.py` | `src/brokers/fulfillment.py` |

**Keep** `src/paper_trading/paper_trader.py` as a thin compatibility shim:
```python
from trading.swing_bot import SwingBot as PaperTrader, load_config, main
if __name__ == "__main__":
    main()
```

**New shell scripts:**
- `run_swing_bot.sh` — same as `run_paper_trader.sh` but spawns `src/trading/swing_bot.py`
- `bg_swing_bot.sh`, `stop_swing_bot.sh`
- Old scripts kept as wrappers pointing to new ones

**State file version:** Bump from 2 → 3. StateManager handles migration: wraps old `pending_order` into `broker_state` format.

**Lock file:** Rename from `paper_trader.lock` to `swing_bot.lock`.

**Update all imports** across the codebase.

---

## Phase 6: Dashboard Updates

**`dashboard/server/process-manager.js`:**
- Spawn target: `src/trading/swing_bot.py` instead of `src/paper_trading/paper_trader.py`

**`dashboard/server/bot-state.js`:**
- `enrichFromConfig`: read from `config.bot || config.paper_trading` for symbol, data_dir
- Read `config.broker.type` → set `bot.brokerType`
- Add `brokerType` to `toJSON()` output

**`dashboard/server/routes/api.js`:**
- `getDataDir()`: check `config.bot.data_dir || config.paper_trading.data_dir`
- `getTradeLogPath()`: check `config.reporting.trade_log || config.bot.data_dir/trades.csv`

**`dashboard/dashboard.yaml`:**
- Point to new config file: `config_path: "config/swing_bot.yaml"`

**ZMQ hello message** (in trader_base.py): add `broker_type` field.

---

## Phase 7: Cleanup

- Delete `src/paper_trading/` directory (after verifying nothing imports from it)
- Remove old shell scripts or convert to deprecation wrappers
- Remove `paper_trading:` config fallback from `load_config` (breaking change, only after all configs migrated)
- Update MEMORY.md with new run commands

---

## Edge Cases

**Transition safety:** Phases 1-2 don't touch PaperTrader. SwingBot is a new file. The switchover happens when you stop old trader, update dashboard config, start SwingBot. State file and trade log formats are compatible.

**Crash recovery:** StateManager persists `broker_state` (from `broker.export_state()`). PaperBroker exports `{cash, positions, short_positions, pending_order}`. On restart, `broker.import_state()` restores everything. If upgrading from old state format (v2), SwingBot falls back to trade-log reconstruction.

**Capability gating:** SwingBot checks `broker.capabilities()` before submitting orders. SHORT/COVER signals are dropped with a warning if broker doesn't support shorting.

**Emergency close semantics:** Cancel any pending order → check position → if in position, market-close at current price → return FillResult. For paper broker this is instant. For real broker: submit market order, poll with 30s timeout, SIGKILL protection.

---

## Critical Files

| File | Role |
|------|------|
| `src/paper_trading/paper_trader.py` | Source of truth for all logic being refactored |
| `src/paper_trading/fulfillment.py` | Wrapped by PaperBroker, API unchanged |
| `src/paper_trading/strategy_runner.py` | Portfolio ownership removed, portfolio_view param added |
| `src/trading/trader_base.py` | Config section generalized |
| `src/portfolio.py` | Moved inside PaperBroker, interface unchanged |
| `src/strategies/base.py` | PortfolioView reused as-is |
| `dashboard/server/process-manager.js` | Spawn target updated |
| `dashboard/server/bot-state.js` | Config reading updated |
| `dashboard/server/routes/api.js` | Data path extraction updated |

## Verification

1. **Phase 1:** Unit test PaperBroker: startup → submit_order → check_order → verify portfolio state
2. **Phase 3:** Run SwingBot with PaperBroker against live exchange for several bars, compare with existing PaperTrader behavior
3. **Phase 5:** `grep -r "paper_trading" src/` should return only the compatibility shim
4. **Phase 6:** Start/stop bot from dashboard, verify trades appear, logs work, ZMQ heartbeat flows
5. **End-to-end:** Stop old PaperTrader → start SwingBot with same state file → verify portfolio value matches, pending order resumes
