# Design Decisions

Logged immediately when made, before implementation. Keep entries short and high-level.
Full technical details (schemas, code snippets, formats) belong in `detailed design.md`.

---

## [2026-02-25] Project Language & Stack

Python with pandas, PyYAML, Jinja2, Plotly, and pytest. See `detailed design.md` for the full dependency list.

---

## [2026-02-25] Data Layer: DataSource / DataParser Split

Separate "where to fetch data" (DataSource) from "how to normalize the format" (DataParser). Adding a new file format = new parser only. Adding a new fetch mechanism = new data source only. See `detailed design.md` for interface details.

---

## [2026-02-25] Standard OHLCV DataFrame Schema

All parsers produce a DataFrame with a `date` index and columns: `open`, `high`, `low`, `close`, `volume`. Schema details in `detailed design.md`.

---

## [2026-02-25] Trade Log CSV Schema

All strategies write to a uniform CSV with columns: `date`, `action`, `symbol`, `quantity`, `price`, `cash_balance`, `portfolio_value`, `details` (JSON). Uniformity enables common reporting. Full column spec in `detailed design.md`.

---

## [2026-02-25] Configuration Format

YAML files drive the controller, specifying backtest parameters, data source, and a list of strategies. Multiple strategies in one config enables comparison runs.

---

## [2026-02-25] Historical BTC Data Source

Use Binance Data Vision for BTCUSDT klines. Downloaded 5m and 1m, Jan 2024 – Jan 2026. Note: timestamps are milliseconds pre-2025, microseconds from Jan 2025 onward.

---

## [2026-02-25] HTML Report Chart Design

Two-panel Plotly chart with shared x-axis: top panel shows asset price with BUY/SELL markers and hover tooltips; bottom panel shows % invested over time. Details in `detailed design.md`.

---

## [2026-02-25] Reporting Output Location

Generated reports go in `reports/`. Source code in `src/`. One-time scripts in `tmp/`.
