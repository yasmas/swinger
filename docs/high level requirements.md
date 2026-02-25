# High Level Goal

To build an app for backtesting and then trading of stocks or crypto currencies and then to be able to show the performance in chart and tables as needed

# General requirements

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

