# Plan: MACD-Vortex-ADX Breakout

## Summary

Add a new intraday strategy, `macd_vortex_adx`, that computes signals on
completed `30min` bars and executes from native `5min` bars without
look-ahead. The entry sequence is:

1. `MACD(12,26,9)` alert
2. `Vortex(14)` intent filter
3. `ADX(14)` rising above a soft floor
4. price breakout trigger

The first implementation includes:

- the strategy module
- registry wiring
- starter YAML configs with all tunable parameters exposed flat under
  `strategies[].params`
- focused tests

It intentionally does **not** include a dedicated grid-search script yet.

## Strategy Rules

### Alert

- A bullish or bearish alert starts when a completed `30min` bar has:
  - a MACD signal-line crossover, or
  - a histogram zero-flip when `use_histogram_flip=true`
- Alerts stay actionable for the signal bar plus the next
  `macd_fresh_bars` completed signal bars.
- If MACD bias reverses before entry, the alert is cancelled.

### Vortex Filter

- Compute `spread = abs(+VI - -VI)`.
- Compare the current spread against the mean spread of the prior
  `vortex_baseline_bars` completed signal bars.
- `strong`:
  - spread is expanding versus the prior bar
  - `spread_now >= vortex_strong_spread_mult * baseline`
- `hugging`:
  - `spread_now <= vortex_hugging_spread_mult * baseline`, or
  - the Vortex winner flips within the last `vortex_weave_lookback` bars
- Anything else is `borderline`.

### ADX Confirmation

- Require `ADX >= adx_floor`
- Require ADX rising versus the prior completed signal bar when
  `require_adx_rising=true`

### Entry

- Immediate entry:
  - alert is fresh
  - Vortex is `strong`
  - ADX passes
  - the signal bar breaks the prior `breakout_lookback_bars` signal-bar
    high/low in the trade direction
- Armed breakout:
  - alert is fresh
  - Vortex is `borderline`
  - ADX passes
  - store a trigger at the signal bar high/low
  - fill when a later `5min` bar breaches that trigger
  - expire after `armed_breakout_expiry_bars` completed signal bars
- `hugging` Vortex cancels breakout arming and blocks entry.

Under the current execution API, breakout fills occur on the `5min` breach
bar as a strategy action, so the simulated fill still happens at the bar
close price used by the backtest executor.

### Exit

- Initial stop is based on the wider of:
  - the entry signal bar’s opposite wick
  - `atr_stop_multiplier * ATR(atr_period)`
- Trailing stop uses peak/trough since entry with
  `atr_trailing_multiplier * ATR(atr_period)`
- Stops are evaluated on every `5min` bar.
- An opposite fully confirmed setup exits the current position first.
  The opposite alert may remain armed for the next eligible entry, but the
  strategy does not same-bar reverse via a single action.

### Direction

- Longs and shorts are both enabled in v1
- `enable_short=false` disables only new short entries

## Parameters Exposed In YAML

- `resample_interval`
- `macd_fast`
- `macd_slow`
- `macd_signal`
- `use_histogram_flip`
- `macd_fresh_bars`
- `vortex_period`
- `vortex_baseline_bars`
- `vortex_strong_spread_mult`
- `vortex_hugging_spread_mult`
- `vortex_weave_lookback`
- `adx_period`
- `adx_floor`
- `require_adx_rising`
- `breakout_lookback_bars`
- `armed_breakout_expiry_bars`
- `atr_period`
- `atr_stop_multiplier`
- `atr_trailing_multiplier`
- `enable_short`

## Files

- `src/strategies/macd_vortex_adx.py`
- `src/strategies/registry.py`
- `config/strategies/macd_vortex_adx/macd_vortex_adx_dev.yaml`
- `config/strategies/macd_vortex_adx/macd_vortex_adx_test.yaml`
- `src/tests/test_macd_vortex_adx.py`

## Test Coverage

- no-lookahead timing on resampled bars
- bullish immediate entry
- bearish immediate entry
- armed breakout fill
- armed breakout expiry
- rejection when Vortex is hugging
- rejection when ADX is falling
- initial stop and trailing stop exits
- opposite confirmed setup exit
- export/import of armed breakout state
- registry lookup
