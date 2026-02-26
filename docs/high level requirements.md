# High Level Goal

To build an app for backtesting and then trading of stocks or crypto currencies and then to be able to show the performance in chart and tables as needed

# General requirements

## Phase 1 - Back Testing

1. Will be written in Python
2. Main components:
- data source: read stock data from files or apis. Can be real time or historical data
- trading system: implement a methodology for buying, selling or holding assets. Can hold cash and one or more assests at any given time
The trading system will log to a CSV file all decisions along with some detail why it made teh specific choice (hold, buy, sell). The CSV columns across all trading systems should be the same so that we can build common reports on it. The buy/sell decision which could be very detailed with numbers - could be a text field or json field that will not break the .CSV format
- reporting: will show the trades after time and P/L over time and other statistical information typical for trading systems. Reports should be shown on HTML, stylzed
- controller: will drive the whole thing, will define periods of time to simulate, etc
3. Modularity: we will build many data source processors that can be used at will, the controller will be able to instantiate the appropriate one based on configuration file. Over time we will build many trading systems, that will use the same data source or sources. Again the controller can instantiate any of the available trading systems based on config file. It can also run multiple of them and get a comparison report. Maybe plot them on the same timeline
4. Trading systes examples:
- The most basic tadding system will be Buy-And-Hold which will buy at the simulatino start date and sell at the end date. 
- Anoyher one could be "Moving Average Crossover with RSI Confirmation" : buy when short MA (e.g., 10-day) crosses above long MA (e.g., 50-day) and RSI >50 (indicating momentum); sell inversely
5. Testing subsystem - define a basic system level testing to check the correctness of the system. ALso define a way to run the trades based on the generated trading log (CSV file) and double check the P/L

## Phase 2 - Paper Trading

1. Now that we have a good trader, with proven back testing performance, I would like to build a bot that will do paper trading
2. It should use the same infrastrcuture we have (data files, trader logic, etc) but add the ability to read real time price information from a real crypto exchange and make simulated trades locally - without actually making any transactions. It should keep a local portfolio, accounting for cash, etc
3. Every 5 minutes it should query for latest price information and get same info (datetime, open, low, high, close, volume) and append it to the local data files. It should create the hourly data as today
4. Once it has an hourly data it should consult the trader logic and decide if we need to transact. If yes, it needs to decide on a favarouble price. It can query the minute price info and decide a good entry price. It should decide the price, not use market pricing. We need to keep doing that until we can fulfill the simulated trade at a favarouble price. If for some reason the prices have gone far from the the time when we made the decision, we need to decide if to anort the trade attempt
5. the CSV & HTML report should be updated every HR and on every trade. We also need to add informatoin about fulfilment - like how long it took to fulfill, what kind of movements we saw. Data that we can use in the future to improve the fulfilment algorithm
6. The access to exchange for price query and fulfilment simulatin shoud be generalized so that we can create multiple actual fulfilment implementation, dependiong on the exchange we will actually decide to use. We will start with Binance.


