from .buy_and_hold import BuyAndHoldStrategy
from .ma_crossover_rsi import MaCrossoverRsiStrategy
from .macd_rsi_advanced import MACDRSIAdvancedStrategy
from .intraday_trend import IntradayTrendStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    "buy_and_hold": BuyAndHoldStrategy,
    "ma_crossover_rsi": MaCrossoverRsiStrategy,
    "macd_rsi_advanced": MACDRSIAdvancedStrategy,
    "intraday_trend": IntradayTrendStrategy,
}
