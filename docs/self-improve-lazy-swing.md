
**Role and Primary Objective**
Your goal is to autonomously improve the win rate and PnL per trade for the lazy swing strategy. The logic is implemented at `src/strategies/lazy_swing.py`. You should run ONE and ONE cycle only of the below steps.

**Step 1: State Recovery & Context**
Read `docs/context-lazyswing.md` if exists. 
If there is an active, unfinished task, resume implementation immediately.
If the previous task is marked as completed or abandoned, begin a new improvement cycle. If the file does not exist - assume this is the first time you have started on this project, create all the .md files mentioned here and the benchmark-lazyswing.csv file from scratch. Add 1st entry to the benchmark.csv (columns described below) which is the performance analysis of the latest trader version.

**Step 2: Analysis & Ideation**
Analyze the most recent `docs/benchmark-lazyswing.csv` and trade logs to identify the current weakest link (e.g., premature exits, low win rate, excessive drawdown, lagging entries).
Research and generate one single, focused hypothesis to solve this issue. You can decide to add technical indicators, tune existing ones, change enter/exit logic, etc, consult the web - shortly for 2-3 artciles don't waste all your time just reading the web. Do not recycle previously failed ideas unless you are applying a fundamentally new approach.

**Step 3: Planning & Documentation**
Create a new section at the top of `docs/context-lazyswing.md` with your hypothesis.
Write a strict, step-by-step implementation plan using markdown checkboxes `[ ]`.
If the document exceeds 500 lines, move the oldest completed experiments into `docs/experiment-lazyswing.md` to keep your working memory clean.

**Step 4: Execution & Timeboxing**
Execute the steps in your plan independently. Do not wait for human confirmation.
Use `git checkout -b experiment-[short-name]` to create a safe branch for your work.
*Timebox Rule:* If you spend more than 5 consecutive attempts trying to fix a bug related to your new idea, abandon the idea, document the failure in the working doc, and stop.

**Step 5: Backtesting & Validation**
Run the backtest. You must test your strategy on the `Dev dataset` first. If it shows promise, validate it on a separate out-of-sample dataset which we called `Test dataset` to ensure it is not curve-fitted.
Generate the HTML report and save the CSV trade log.
Update `docs/benchmark-lazyswing.csv` with the new version's metrics: Overall Return, Win Rate, Max Drawdown, Sharpe Ratio, Number of Trades, and Average PnL% per trade.

**Step 6: Evaluation & Version Control**
Evaluate the progress objectively. A successful iteration must meet at least one of these criteria without severely degrading the others:
- Higher overall return.
- Higher win rate.
- Significantly lower drawdown (slight drawdown increases are acceptable if returns are exponentially higher).
- Higher average PnL per trade.
- Simpler algorithm logic with identical performance.

Document your final verdict concisely in `docs/context-lazyswing.md` and mark the phase as DONE.
If the result is positive, commit the changes using `git commit -am "Implement [Idea]"` and merge it to the main branch and bump the `version` field and add any new strategy params.
If the result is negative, discard the code changes using `git reset --hard` and `git checkout main`, keeping only the updated documentation to remember the failure.

**Step 7: Reflect**
Review your work this cycle, and critique what you did and what you can do better next time - i mean with the process itself. We wil summerize such conclusions in the section below called "How to be an effecient BOT"
- was there anything that took a lot of time? If you found a solution and you think you could re-use it in the future, write it under that section
- anything that was not clear and took much time to find, write the findings on that section
- Failures that can be avoided in the future


# How to be an effecient BOT
- Any scratch code that I write that I think can be re-usable, put it in a utility file and document here so it can be reused later
- **Entry filters are incompatible with always-in-market strategies.** LazySwing's power comes from immediate flip on ST reversal — being always invested. Any filter that causes a flat period (sitting in cash) destroys compounding returns even if it improves win rate. Chop Index < 50 improved WR from 68.9% → 70.6% but cut returns by 30x. Future improvements should focus on: (1) exit quality (smarter stops, trailing), (2) position sizing in choppy markets instead of skipping entirely, (3) tuning Supertrend parameters to reduce false flips.
- **Most entries go through the pending flip path, not fresh entries.** When testing entry filters, always check BOTH the fresh entry path AND the pending flip path. In the first attempt, filtering only fresh entries had zero effect because ~99% of entries are flip entries.
- **Analysis code for filter candidates is reusable.** The approach of adding all candidate indicators to the backtest CSV (via `on_bar` details), then analyzing WR/PnL splits in a Jupyter-style script, works well for quickly evaluating many filter ideas before implementing any. Do this analysis FIRST before writing strategy code.
- **Grid search simulation before full backtests saves time.** For pure-ST strategies, a flip-by-flip simulation (using hourly open prices, not close prices) closely approximates the full backtest. Use this to quickly narrow 20+ param combinations down to 2-3 candidates, then run full backtests only on those. **Critical:** use `hourly['open']` not `hourly['close']` — the strategy exits/enters at the first 5m bar of the new hour (≈ hourly open), not the last bar. Using close prices inverts the WR (32% vs actual 68.9%).
