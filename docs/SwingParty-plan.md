# SwingParty: Multi-Asset Rotation Strategy

## Context

SwingParty is a new strategy that extends LazySwing to track N assets simultaneously, investing in up to I of them at any time. Each asset runs its own Supertrend with per-asset ATR period and multiplier. When more assets want entry than slots are available, a pluggable scoring function (starting with volume breakout) ranks candidates and evicts the weakest holding. Building for **backtesting first**.

## Architecture: Strategy + Coordinator (Option 3)

Three layers:
1. **Signal**: One `LazySwingStrategy` instance per asset — generates flip signals, reused unchanged
2. **Coordinator**: `SwingPartyCoordinator` — collects signals, manages I slots, ranks by score, decides rotations
3. **Controller**: `MultiAssetController` — loads N DataFrames, iterates in lockstep, feeds coordinator

## Key Design Decisions

- **Wait for flips at startup** — don't enter immediately (LazySwing's natural behavior)
- **Skip if weaker** — only rotate in when new flip scores higher than the weakest holding
- **Same resample interval** for all assets (e.g., all 1h)
- **LazySwing state rollback** — use `export_state()` / `import_state()` to undo rejected entries. Cheap dict copy of ~12 fields before each `on_bar()`, restore only when coordinator rejects an entry. Keeps LazySwing completely unmodified.
- **Re-score holdings each resampled bar** — prevents stale scores from blocking rotation
- **1/I of portfolio total_value** per slot, recomputed on each entry

## New Files

### 1. `src/strategies/swing_party.py` — Coordinator

```python
class SwingPartyCoordinator:
    __init__(config)       # create N LazySwing instances, I slots, scorer
    prepare(datasets)      # call prepare() on each LazySwing
    on_bar(date, rows, datasets_so_far, is_last_bar, portfolio) -> list[(symbol, Action)]
```

**on_bar flow:**
1. For each symbol with data at this timestamp:
   - Save LazySwing state via `export_state()`
   - Call `on_bar()` with synthetic PV (1/I cash, actual position from portfolio)
   - Capture proposed action
2. Separate into exits and entries
3. Execute all exits first (frees slots)
4. Score entries, sort by score descending
5. For each entry: fill free slot, or evict weakest if score is higher
6. Roll back LazySwing state for rejected entries via `import_state()`
7. Return final `[(symbol, Action)]` list

**Slot tracking:** `self.slots = {}` mapping `symbol -> {direction, entry_price, score, entry_bar}`

**Flip on held symbol (exit + re-enter):** LazySwing proposes SELL, sets `_pending_short`. The exit frees the slot. On next bar, LazySwing proposes SHORT — treated as normal entry, gets the free slot without contention.

### 2. `src/strategies/scorers/base.py` — Scorer Interface

```python
class FlipScorer(ABC):
    def score(self, symbol, data_so_far, direction, resample_interval) -> float
    def score_holding(self, symbol, data_so_far, direction, resample_interval) -> float
```

Two methods: `score()` for new flip candidates, `score_holding()` for re-scoring current positions.

### 3. `src/strategies/scorers/volume_breakout.py` — First Scorer

Compares recent resampled volume (short_window bars) to long-running average (long_window bars). Returns ratio. Config: `short_window` (default 5), `long_window` (default 50).

### 4. `src/strategies/scorers/registry.py`

```python
SCORER_REGISTRY = {"volume_breakout": VolumeBreakoutScorer}
```

### 5. `src/multi_asset_controller.py` — Backtest Controller

- Loads N DataFrames (one per asset from config)
- Builds union timestamp index across all symbols
- Iterates chronologically, gathering available rows per symbol at each timestamp
- Calls coordinator.on_bar(), executes returned actions on portfolio
- Handles data gaps (>24h) per symbol — force-close that symbol's position
- Logs trades per symbol (unified trade log with symbol column)

### 6. `run_backtest_multi.py` — Entry Point

Thin script: loads config, creates MultiAssetController, runs, generates report.

### 7. `config/strategies/swing_party/dev.yaml` — Config

```yaml
backtest:
  name: "SwingParty Dev"
  version: "v1"
  initial_cash: 100000
  start_date: "2022-01-01"
  end_date: "2024-12-31"

data_source:
  type: csv_file
  parser: binance_kline
  params:
    # File path computed per asset: data/{symbol}-5m-{start_year}-{end_year}-combined.csv
    data_dir: "data"
    file_pattern: "{symbol}-5m-{start_year}-{end_year}-combined.csv"

strategy:
  type: swing_party
  max_positions: 3
  resample_interval: "1h"
  supertrend_atr_period: 10
  supertrend_multiplier: 2.0

  scorer:
    type: volume_breakout
    params:
      short_window: 5
      long_window: 50

  assets:
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
```

Assets is a simple list of tickers. Data file paths are computed from the `file_pattern` template + `start_date`/`end_date` years. ST parameters are shared across all assets. Per-asset overrides can be added later if needed.

## Files to Modify

- `src/strategies/registry.py` — add `swing_party` entry (or coordinator uses registry internally)

## Edge Cases

1. **Simultaneous flips**: Multiple symbols flip on same bar -> score all, fill slots best-first
2. **Flip on held symbol**: Exit frees slot, re-entry on next bar competes normally
3. **Eviction cascade**: Evicted symbol's LazySwing gets `reset_position()`, may re-enter on future flip
4. **Score decay**: Re-score all holdings each resampled bar boundary so stale scores don't persist
5. **Data gaps**: Per-symbol >24h gap detection, force-close that symbol only
6. **Missing data**: Not all symbols have data at every timestamp (e.g., equities vs crypto) — skip symbols without data at that bar

## Implementation Order

1. Scorer interface + VolumeBreakoutScorer (independent, testable)
2. SwingPartyCoordinator with slot management
3. MultiAssetController with multi-symbol data loading
4. Config parsing for new format
5. `run_backtest_multi.py` entry point
6. Test config with BTC + ETH (need ETH data file)
7. Run backtest, validate results

## Verification

1. Run single-asset SwingParty (N=1, I=1) and compare output to LazySwing backtest — results should be identical
2. Run 2-asset SwingParty (N=2, I=1) — verify rotation happens on flips, scores logged
3. Run 3-asset SwingParty (N=3, I=2) — verify slot management, eviction logic
4. Check trade log: entries show score, evictions show "evicted by {symbol}"
5. Verify portfolio value never exceeds initial cash (no leverage)
