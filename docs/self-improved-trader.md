
**Role and Primary Objective**
Your goal is to autonomously improve the win rate and PnL per trade for the BTC long/short swing trader. The core logic is documented in `docs/swing-trend-algorithm-design.md` and implemented at `src/strategies/swing_trend.py`. You should run ONE and ONE cycle only of the below steps.

**Step 1: State Recovery & Context**
Read `docs/what-im-working-on.md` if exists. 
If there is an active, unfinished task, resume implementation immediately.
If the previous task is marked as completed or abandoned, begin a new improvement cycle. If the file does not exist - assume this is the first time you have started on this project, create all the .md files mentioned here and the benchmark.csv file from scratch. Add 1st entry to the benchmark.csv (columns described below) which is the performance analysis of the latest trader version.

**Step 2: Analysis & Ideation**
Analyze the most recent `docs/benchmark.csv` and trade logs to identify the current weakest link (e.g., premature exits, low win rate, excessive drawdown, lagging entries).
Research and generate one single, focused hypothesis to solve this issue. Do not recycle previously failed ideas unless you are applying a fundamentally new approach.
*Crucial Architecture Note:* When testing new entry logic (such as Multi-Timeframe Regime Filtering), explicitly verify that looking at higher timeframes does not cause you to enter at a bad localized timeframe, resulting in short losing trades that immediately fail lower-timeframe check-safes. 

**Step 3: Planning & Documentation**
Create a new section at the top of `docs/what-im-working-on.md` detailing your hypothesis.
Write a strict, step-by-step implementation plan using markdown checkboxes `[ ]`.
If the document exceeds 200 lines, move the oldest completed experiments into `docs/experiment-archive.md` to keep your working memory clean.

**Step 4: Execution & Timeboxing**
Execute the steps in your plan independently. Do not wait for human confirmation.
Use `git checkout -b experiment-[short-name]` to create a safe branch for your work.
*Timebox Rule:* If you spend more than 5 consecutive attempts trying to fix a bug related to your new idea, abandon the idea, document the failure in the working doc, and stop.

**Step 5: Backtesting & Validation**
Run the backtest. You must test your strategy on the `Dev dataset` first. If it shows promise, validate it on a separate out-of-sample dataset which we called `Test dataset` to ensure it is not curve-fitted.
Generate the HTML report and save the CSV trade log.
Update `docs/benchmark.csv` with the new version's metrics: Overall Return, Win Rate, Max Drawdown, Sharpe Ratio, Number of Trades, and Average PnL% per trade.

**Step 6: Evaluation & Version Control**
Evaluate the progress objectively. A successful iteration must meet at least one of these criteria without severely degrading the others:
- Higher overall return.
- Higher win rate.
- Significantly lower drawdown (slight drawdown increases are acceptable if returns are exponentially higher).
- Higher average PnL per trade.
- Simpler algorithm logic with identical performance.

Document your final verdict concisely in `docs/what-im-working-on.md` and mark the phase as DONE.
If the result is positive, commit the changes using `git commit -am "Implement [Idea]"` and merge it to the main branch.
If the result is positive, also update the paper trader config (`config/paper_trading.yaml`) to use the new version — bump the `version` field and add any new strategy params.
If the result is negative, discard the code changes using `git reset --hard` and `git checkout main`, keeping only the updated documentation to remember the failure.

**Step 7: Reflect**
Review your work this cycle, and critique what you did and what you can do better next time - i mean with the process itself. We wil summerize such conclusions in the section below called "How to be an effecient BOT"
- was there anything that took a lot of time? If you found a solution and you think you could re-use it in the future, write it under that section
- anything that was not clear and took much time to find, write the findings on that section
- Failures that can be avoided in the future


# How to be an effecient BOT
- Any scratch code that I write that I think can be re-usable, put it in a utility file and document here so it can be reused later
- The CSV trade log column names are in the `details` JSON field (pnl_pct, exit_reason, bars_held, etc.), not as top-level CSV columns. Parse with `json.loads(row['details'])`.
- When analyzing trade logs, always look at exit_reason + trigger + direction + hold_duration cross-tabulations — single-dimensional analysis misses the real signal.
- The inline analysis script pattern (python3 -c "...") is efficient for quick grid searches — no need to create separate analysis scripts for one-off explorations.
- When a binary filter (e.g., histogram > 0) is too aggressive, try the **derivative** instead (e.g., histogram delta > 0). The derivative preserves entries where the base value is wrong-side but improving, which are often good trades.
- The MFE/MAE fields in trade log details are named `max_favorable_excursion_pct` and `max_adverse_excursion_pct`, NOT `mfe_pct` and `mae_pct`. Always check field names against actual JSON before building analysis.
- When pairing entries to exits for trigger→exit_reason cross-tabs, entries are BUY/SHORT rows and exits are SELL/COVER rows. Reset both indexes and zip by position.
- The HTML report metrics (Sharpe, MaxDD) may differ from what's documented in the working doc — always extract from the HTML report directly for consistent comparison. Use `grep -A2 'stat-label'` to extract from the HTML.
- When a filter works on one trigger type (e.g., histogram delta on kc_midline_hold), always check if extending it to other triggers helps. It's a low-risk, high-leverage test since the mechanism is already validated.
- Large dev/test return asymmetry (e.g., +46% dev vs +0.4% test) doesn't necessarily mean overfitting — check if the test set simply has fewer instances of the filtered entry type. If no test metric degrades, the change is safe.
- **Check existing config params before writing code.** The `trailing_supertrend_multiplier` infrastructure existed since v1 but was never used (set to 0). Changing it from 3.0 to 2.0 doubled returns — no code change needed. Always scan configs for unused levers before building new features.
- **Capital recycling is the #1 return driver.** Tighter trailing (v11), thesis invalidation (v8), and histogram delta filters (v9/v10) all share the same mechanism: exit faster → free capital → more trades → compound more. Any change that reduces hold time on marginal trades will likely improve returns.
- **Thesis_invalidation is a capital recycler, not just a loss-cutter.** Don't try to "save" the ~37% of TI trades that are currently profitable — the capital freed by cutting them generates MORE value through fresh entries than keeping them. Tested PnL gate (only TI when losing): dev return dropped -27.5%, 166 fewer trades, WR dropped 51%→44%.
- **Challenge user intuition with data.** User suggested faster HMACD for shorts, but analysis showed ADX was the bottleneck (23K blocked hours vs 3 hours for histogram filter). Always identify the actual blocker before building a fix.
- **Check for data gaps in test set.** The test-combined dataset has a 3-year gap (2021→2025). Any position held across this gap will have catastrophic PnL. Added gap detection to controller.py (>24h gap → force close). Always verify test results aren't corrupted by data artifacts.
- **LONG vs SHORT asymmetry is a feature, not a bug.** SHORTs have higher quality (57.7% WR, 0.59% avgPnL) but fewer entries. The constraint isn't trade quality — it's entry opportunity. Lowering barriers to SHORT entry is high-leverage.
- **The `short_adx_threshold` param already existed separately from `adx_threshold`.** Before writing new indicators or complex code, check if existing but under-used config params can solve the problem. v12 was config-only (18 vs 25) and +138% on test.
- **HTML report metric extraction pattern:** `re.search(r'stat-label">Return.*?stat-value[^>]*>([^<]+)', html, re.DOTALL)` for Return, MaxDD, Sharpe, Longs, Shorts. Labels are "Return", "Max DD", "Sharpe", "Longs", "Shorts" (not "Total Return", "Win Rate" etc.).
- For new assets, first run one-factor threshold sweeps on a Dev/OOS split before trying multi-parameter combinations; this reduces overfitting and surfaces true high-leverage knobs quickly.
- On silver, pairing lower `short_adx_threshold` with earlier `breakeven_trigger_pct` improved both return and Sharpe OOS without changing code; prioritize these existing threshold levers before any strategy refactor.