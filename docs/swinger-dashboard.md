# The UI
A modern dashboard UI design for a webapp that controls multiple trading bots. Here is a high level description:

## Top level
1. On the top line is a tab selector, to switch between different running traders. Each may be running on a different exchange using different algorithm and/or different asset
2. Each tab is showing the following:

## Status line
- Trader name, version, exchange, asset, PID
- Status: Running, Stopped. Next to it a button to Start or Stop the trader
- Protfolio Value
- Asset Status: LONG, SHORT, HOLD
- PnL% year to date, month to date, week to date
- narrow chart showing protfolio value over time
- narrow chart showing 3 lines: trending avg short Pnl% and long pnl% and win rate

## Chart
- Show price chart for the asset 
- show buy/close/short/cover markers on the price chart
- buttons to switch view from week, month, 6month, year view
- scrollbar to pan the time axis

## Trades Table
- Button at the top to download the whole list
- Columns: datetime, type (LONG, SHORT), Qty, enter price, Enter reason, Duration HH:MM, exit price, PnL%, exit reason 
- It will show the last 100 trades, latest at the top

# Integration with trader bots

The trader bots are written in Python, each of them run as a separate process that can be started, and stopped independatly. The dashboard has the ability to start and stop them from the UI. The communication with them is using ZeroMQ and support the JSON messages below. Please think critically about these messages and see if they fulfill all the needs of this dashboard. If there us anything else you need, add it. If you see ways to improve on it, do it.

- Bot->Dashboard: "Hello", sent upon startup with unique name, pid & time started
- Bot->Dashboard: "Trade-Entry", sent when a new trade is started (with price, qty, reason, etc)
- Bot->Dashboard: "Trade-Exit", sent when a new trade is exited (with price, qty, PNL, reason, etc)
- Bot->Dashboard: "Profile", sent upon request and includes unique name, pid, stragety name, version, exchange, asset name
- Bot->Dashboard: "Protfolio", sent upon request and includes protfolio value, trade state (long, short, nil), traded datetime, asset qty held
- Bot->Dashboard: "PnL-Info", sent upon request and return historical PnL info
- Bot->Dashboard: "Trades", sent upon request and includes list of N last trades
- Dashboard->Bot: "Request-Info", with a list of messages that are being requested and parameters needed for each
- Dashboard->Bot: "Exit-Trade" - will make the bot exit his last trade
- Dashboard->Bot: "Quit" - will make the bot stop and quit

This should cover most of the info shown in the dashboard. If you need anything else, please come up with more messages or improve messages.
