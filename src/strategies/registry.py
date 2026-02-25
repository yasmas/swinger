from .buy_and_hold import BuyAndHoldStrategy
from .ma_crossover_rsi import MaCrossoverRsiStrategy

STRATEGY_REGISTRY: dict[str, type] = {
    "buy_and_hold": BuyAndHoldStrategy,
    "ma_crossover_rsi": MaCrossoverRsiStrategy,
}
