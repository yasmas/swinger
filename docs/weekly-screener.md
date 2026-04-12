System Role: Act as an Expert Quantitative Trading Developer. Your task is to write a complete, robust, and well-documented script that builds a discrete backtesting pipeline for the party swing trading bot.

## 1. GOAL
Develop a pipeline that evaluates and compares different stock-picking scoring algorithms. The script will rank stocks from a local nasdaq100 directory, select the top deciles, simulate a backtest for the subsequent week, and generate configuration files for the swing party bot. The empirical results will help determine optimal selection criteria and ideal portfolio sizing.

## 2. PARAMETERS & SCORING CRITERIA
The script must accept two primary parameters at runtime:

N: An integer representing the number of sample weeks to process.

scoring_method: The specific algorithm used to rank the stocks. The script should be designed to simulate ONE of these at a time (`run_weekly_screener.py --scoring …`), supporting:

1. **relative_volume** — mean(volume in W) / mean(volume in prior 21 sessions).

2. **momentum** — absolute weekly return over W.

3. **atr_roc5** — Cross-sectional filter: keep the top fraction (`--atr-keep-top`, default 0.35) of the universe by normalized ATR(14) at end of W; then rank survivors by **absolute** 5-day return over W. Deciles apply only to survivors (requires at least 10).

4. **atr_vwap_dev** — Same ATR filter as **atr_roc5**; second stage ranks survivors by deviation of Friday close from a **daily** weekly VWAP (typical price × volume over W).

5. **bb_pctb** — Bollinger Band **%B** at Friday close of W: \((\text{close} - \text{lower}) / (\text{upper} - \text{lower})\) where upper/lower are from a 20-day simple moving average of close ± 2 standard deviations of close, using the 20 trading days ending at `end_W`. Full-universe deciles (same as momentum / relative_volume).

6. **shock_vol_roc** — “Shock” score: \(\bigl(\sum \text{volume on W}\bigr) / \overline{\text{(sum of volume on each of the prior 10 non-overlapping 5-day blocks)}}\) × **|** 5-day ROC **|** over W. Large **up or down** moves with abnormal volume score highest. Full-universe deciles. Valid windows require at least 50 trading days before `start_W` on the master calendar.

7. **roc_acceleration** — **|** ROC(current week) − ROC(previous week) **|** where each ROC is close\(_\text{last}\)/close\(_\text{first}\) − 1 over its 5 trading days. Captures parabolic acceleration or cascading selloffs. Full-universe deciles.

8. **range_expansion** — Two-stage: (1) **Range expansion ratio** = \((\max \text{high} - \min \text{low})\) over W divided by the **mean** of daily Wilder ATR(14) over the **70 trading sessions** before `start_W` (≈14 weeks). (2) Keep the top fraction `--range-expansion-keep-top` (default 0.2) by that ratio; rank survivors by **close extremity** \(|2p - 1|\) with \(p = (\text{Friday close} - \text{week low}) / (\text{week high} - \text{week low})\), so names closing near the **week high or week low** score highest. Deciles among survivors only. Valid windows require a longer master-calendar history before W (implementation: `start_W` index ≥ 83).

For **atr_roc5** and **atr_vwap_dev**, `--atr-keep-top` must lie in (0, 1] (default 0.35).

For **range_expansion**, `--range-expansion-keep-top` must lie in (0, 1] (default 0.2).

## 3. THE SIMULATION OUTER LOOP

Evaluate the 1-year historical daily data available in the local nasdaq100 folder.

**Week definition:** A scoring week **W** is **Monday–Friday** of a single **calendar** week (five US equity sessions). The simulation week **W+1** is **Monday–Friday** of the **following** calendar week. Any calendar week where one of Mon–Fri is missing from the merged master calendar (e.g. market holiday with no row in the daily CSVs) is **skipped**—there is no partial-week fill.

Select N valid (W, W+1) pairs that are evenly spaced across the available date range (distinct sample weeks, not necessarily consecutive on the calendar).

For each of the N weeks (let's call this Week W), execute the following:

1. Calculate the score for all available stocks using the specified scoring_method up to the end of Week W (Friday close of W).

2. Rank the stocks based on this score and divide them into 10 equal groups/deciles.

3. Select the top 3 groups (3 top deciles) to pass into the Group Simulation.

## 4. GROUP SIMULATION (Execution)
For each of the 3 selected groups, perform a simulated backtest on the strictly following week (Week W+1). Note: do not score or run simulations on weekend dates.

Directory Setup: Create the directory `data/backtests/nasdaq-scoring-simulation-{criteria_name}/{w1_start}_{w1_end}/` (simulation week **W+1**, Monday–Friday). All subsequent files for this run should go here.

Data Ingestion: Fetch 5-minute intraday data for Week W+1 using standard data feeds. Include necessary warmup data.

Bot Configuration: Generate the standard swing party YAML configuration file, naming it appropriately, and specifying the start and end dates for Week W+1 along with the selected symbols. Store these in the directory created above.

Backtest Execution: Assume a starting portfolio of $1000. Run three separate simulations per each of the 3 groups, allowing a maximum of 3, 4, or 5 concurrent stock positions. 

## 5. DOCUMENTATION & REPORTING
Inside the simulation folder, programmatically generate a results.md file summarizing the simulation.
Create three Markdown tables (one for the 1st, 2nd, and 3rd group rankings). Each table must contain the following columns:

Simulation week dates (Week W+1)

Scoring (The metric value from Week W, e.g., the momentum score)

Stocks selected

Return with 3 stocks

Return with 4 stocks

Return with 5 stocks

Accumulated return with 3 stocks (assuming we compound the N sample weeks)

Accumulated return with 4 stocks (assuming we compound the N sample weeks)

Accumulated return with 5 stocks (assuming we compound the N sample weeks)

Conclude the document with a programmatic summary of the results.