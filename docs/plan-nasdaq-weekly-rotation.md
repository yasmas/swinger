# Plan: Weekly Nasdaq Rotation Script

## Context

The weekly screener simulations showed **momentum Group 1 max_positions=3** is the best stock-picking method (13.1%/week normalized). The user wants a script to run every weekend that:
1. Scores the Nasdaq-100 universe using momentum, picks top-decile stocks
2. Downloads 5m warmup data for the selected stocks
3. Generates a new bot YAML config for the coming week
4. Archives the previous week's config, generates an HTML performance report, appends a cumulative summary to an .md ledger
5. Zips old data feeds for the completed week

## Script: `rotate_nasdaq_weekly.py`

Single script, run manually every weekend. Produces everything needed to start the bot on Monday.

### Parameters

```
--user USER           # e.g. "yasmas" → data/yasmas/
--scoring momentum    # default momentum, extensible
--max-positions 3     # default 3
--provider massive    # 5m data download provider
--daily-dir PATH      # daily CSVs (default data/backtests/nasdaq100)
--template-yaml PATH  # strategy template (default config/strategies/swing_party/apr9-movers.yaml)
--dry-run             # score + print picks, no downloads/writes
```

### Directory Layout

```
data/yasmas/
├── nasdaq-apr14.yaml              ← current week's bot config (Monday date, shortest format)
├── nasdaq-weekly-summary.md       ← cumulative performance ledger (appended each rotation)
├── nasdaq-archive/
│   ├── nasdaq-apr07.yaml          ← archived bot config
│   ├── nasdaq-apr07-report.html   ← HTML performance report for that week
│   ├── nasdaq-apr07-5m.zip        ← zipped 5m CSVs for that week
│   └── ...
└── nasdaq-live/                   ← active 5m data dir (bot's data_dir)
    ├── state.yaml
    ├── trades.csv
    ├── swing_bot.log
    ├── tsla/TSLA-5m-2026-04.csv
    ├── amd/AMD-5m-2026-04.csv
    └── ...
```

Date format: `apr14-26` = lowercase 3-letter month + day + 2-digit year. Examples: `jan5-26`, `feb28-25`.

### Step-by-step Flow

#### Step 1: Determine dates
- `next_monday` = coming Monday (if today is Sat/Sun, the immediately next Monday)
- `last_monday` = `next_monday - 7 days` (the week being archived)
- `date_tag` = format `next_monday` as `apr14-26`-style
- `prev_tag` = format `last_monday` as `apr7-26`-style

#### Step 2: Score universe → pick stocks
- Reuse from `weekly_screener_core`: `load_daily_frames()`, `score_universe()`, `assign_deciles_and_top_groups()`, `symbols_in_bins()`. reuse - meaning use that file directly (add/change methods if needed), dont duplicate code. In future when we modify the scopring system, i expect this script to benefit from that.
- Build a `WeekWindow` for the most recent complete week in daily data (last 5 trading days)
- Score with momentum, take Group 1 (top decile) symbols
- Print picks to stdout

#### Step 3: Archive previous week (if exists)
- Find `data/yasmas/nasdaq-{prev_tag}.yaml` (or scan for the most recent `nasdaq-*.yaml` that isn't the new one)
- Move it to `nasdaq-archive/nasdaq-{prev_tag}.yaml`
- Generate HTML report from `nasdaq-live/trades.csv` using `SwingPartyReporter.generate()` → `nasdaq-archive/nasdaq-{prev_tag}-report.html`
- Append summary row to `nasdaq-weekly-summary.md` using `compute_stats()` on the trades from that week only (filter by date range)
- Zip all 5m CSVs in `nasdaq-live/*/` into `nasdaq-archive/nasdaq-{prev_tag}-5m.zip`, then delete the originals
- Clear `nasdaq-live/state.yaml` (fresh start for new assets)
- Rename `trades.csv` → `nasdaq-archive/nasdaq-{prev_tag}-trades.csv` (keep raw data)

#### Step 4: Download 5m data for new week
- Compute warmup range: `warmup_trading_days_from_strategy()` → `warmup_range_start_day()`
- Download into `nasdaq-live/{symbol_lower}/{SYMBOL}-5m-{YYYY}-{MM}.csv` (monthly format)
- The bot's DataManager will find these files on startup and use them for warmup
- Pre-downloading avoids API rate limits on bot startup

#### Step 5: Generate new bot config YAML
- Write `data/yasmas/nasdaq-{date_tag}.yaml` with:
  - `bot.data_dir: data/yasmas/nasdaq-live`
  - `strategy.config` pointing to `nasdaq-{date_tag}-strategy.yaml` in the same `data/<user>/` folder as the bot YAML
  - Exchange, broker, reporting, logging sections from template
- Write the strategy YAML with the picked assets, max_positions=3, start/end dates for the week

#### Step 6: Summary ledger
`nasdaq-weekly-summary.md` format:
```markdown
# Nasdaq Weekly Rotation — Cumulative Performance

| Week | Stocks | Return % | Win Rate | Trades | Long/Short | Capital Start | Capital End | Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| apr7-26 | AMD,INTC,META,... | 12.34 | 65.0% | 18 | 10/8 | $10,000 | $11,234 | 2.45 |
| apr14-26 | TSLA,NVDA,... | ... | ... | ... | ... | ... | ... | ... |
```

### Files to Modify/Create

| File | Action |
|------|--------|
| `rotate_nasdaq_weekly.py` | **DONE** — main script (repo root) |
| `config/bot/nasdaq_weekly_bot_template.yaml` | **DONE** — bot shell for SwingPartyBot (`strategy.config` is relative to the bot YAML under `data/<user>/`) |
| `docs/plan-nasdaq-weekly-rotation.md` | this plan doc |

### Implementation notes (2026)

- **Strategy path**: Bot is `data/<user>/nasdaq-<tag>.yaml`; strategy is `data/<user>/nasdaq-<tag>-strategy.yaml` beside it (`strategy.config` is relative to the bot file’s directory).
- **5m layout**: Flat under `data/<user>/nasdaq-live/`: `SYMBOL-5m-<warmup_start>_<week_end>.csv`. `SwingPartyBot` uses `bot.use_symbol_subdirs: false` so `DataManager` reads the same layout. Optional `per_symbol_subdir` in backtest YAML still exists for other runs.
- **Group 1**: All symbols in the top decile bin (same as weekly screener Group 1); `max_positions` still limits concurrent slots in the coordinator.
- **Scoring week**: Latest valid `WeekWindow` from `enumerate_week_windows` on daily data (most recent complete Mon–Fri week on the merged calendar).

### Reusable Components (no modifications needed)

| Component | File | What we reuse |
|-----------|------|---------------|
| Scoring | `src/weekly_screener_core.py` | `load_daily_frames()`, `score_universe()`, `assign_deciles_and_top_groups()`, `symbols_in_bins()`, `WeekWindow`, `enumerate_week_windows()` |
| Download | `download_swing_party_day.py` | `download_massive_5m_range()`, `warmup_range_start_day()`, `warmup_trading_days_from_strategy()` |
| Stats | `src/reporting/reporter.py` | `compute_stats()` |
| Reporter | `src/reporting/swing_party_reporter.py` | `SwingPartyReporter.generate()` |
| Daily data | `download_nasdaq_daily.py` | Run separately to refresh `data/backtests/nasdaq100/` before rotation |

### Verification

1. **Dry run**: `python rotate_nasdaq_weekly.py --user yasmas --dry-run` — prints picked stocks, dates, no side effects
2. **First run** (no archive): creates config + downloads warmup data, no archiving since no prior week
3. **Second run**: archives previous week, generates report + summary, creates new config
4. **Check**: `cat data/yasmas/nasdaq-weekly-summary.md` — verify table renders
5. **Check**: open `nasdaq-archive/nasdaq-{tag}-report.html` in browser
6. **Check**: `ls nasdaq-archive/nasdaq-{tag}-5m.zip` — verify zip exists
