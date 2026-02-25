# Session Context

This file keeps track of what has been done and what comes next.
At the start of every new session, read this file to get back up to speed.
Also read docs/rules.md and docs/decisions.md to restore full context.

---

## How to use this file

1. Read this at the start of each session to know where we left off
2. After completing work, update the "Last completed" and "Next steps" sections
3. Keep it concise -- bullet points, not prose

---

## Project Summary

Building a modular Python backtesting and trading system called **Swinger**.
Full design is in `docs/detailed design.md`.

Key components (in build order):
1. Data sources + parsers (Phase 1) -- DONE
2. Portfolio model + trade logging (Phase 2) -- DONE
3. Strategies: Buy-and-Hold (Phase 3) -- DONE
4. Controller + YAML config (Phase 4) -- DONE
5. HTML reporting (Phase 5) -- DONE
6. Trade log replay verifier (Phase 6) -- DONE
7. MA Crossover + RSI strategy (Phase 7) -- DONE
8. Multi-strategy comparison (Phase 8)

---

## Directory Structure

```
swinger/
  src/
    portfolio.py        # Portfolio class (cash, positions, buy/sell)
    trade_log.py        # TradeLogger + TradeLogReader (CSV format)
    trade_replay.py     # TradeReplayVerifier (independent P/L checker)
    config.py           # YAML config loader
    controller.py       # Controller + BacktestResult
    data_sources/
      __init__.py
      base.py           # DataSourceBase ABC
      csv_file.py       # CsvFileDataSource
      registry.py       # PARSER_REGISTRY + DATA_SOURCE_REGISTRY
      download_binance.py # Reusable Binance data downloader
      parsers/
        __init__.py
        base.py         # DataParserBase ABC + STANDARD_COLUMNS
        nasdaq.py       # NasdaqHistoricalParser
        binance.py      # BinanceKlineParser (handles ms + µs timestamps)
    strategies/
      __init__.py
      base.py           # StrategyBase ABC, Action, ActionType
      buy_and_hold.py       # BuyAndHoldStrategy
      ma_crossover_rsi.py   # MaCrossoverRsiStrategy
      registry.py           # STRATEGY_REGISTRY
    reporting/
      __init__.py
      reporter.py       # Reporter (three-panel Plotly chart + stats table)
      templates/
        report.html     # Jinja2 template (self-contained HTML)
    tests/                       # 87 tests total, all passing
    test_parsers.py            # 13 tests
    test_data_sources.py       # 12 tests
    test_portfolio.py          # 14 tests
    test_trade_log.py          # 3 tests
    test_strategies.py         # 6 tests
    test_ma_crossover_rsi.py   # 14 tests (RSI, crossover signals, warmup)
    test_controller.py         # 9 tests
    test_reporting.py          # 7 tests
    test_trade_replay.py       # 4 tests (+ deliberate error detection)
  config/
    btc_buy_and_hold.yaml     # Sample config for BTC Buy-and-Hold
    btc_ma_crossover_rsi.yaml # Sample config for BTC MA Crossover RSI
  data/                   # Price data CSVs
  reports/                # Generated HTML reports (output)
  tmp/
  docs/
  .venv/
  requirements.txt
```

---

## Running Tests

```bash
cd /Users/yossi/Code/swinger
source .venv/bin/activate
PYTHONPATH=src pytest src/tests/ -v
```

All 87 tests passing as of 2026-02-25.

---

## Last Completed

- [2026-02-25] Phases 1-3: Data layer, portfolio, trade log, buy-and-hold strategy
- [2026-02-25] Phase 4: Config loader, Controller, strategy registry, sample YAML config
- [2026-02-25] Phase 5: Reporter with two-panel Plotly chart (price + markers, % invested), Jinja2 HTML template, stats computation
- [2026-02-25] Phase 6: TradeReplayVerifier -- independently replays trade log, catches P/L discrepancies
- [2026-02-25] Phase 7: MaCrossoverRsiStrategy, RSI helper, 14 new tests, YAML config, backtest report
- [2026-02-25] 87 tests all passing

---

## Next Steps

- [ ] Phase 8: Multi-strategy comparison in controller
- [ ] Phase 8: Comparison report (overlay equity curves, side-by-side stats)
