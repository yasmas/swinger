from .buy_and_hold import BuyAndHoldStrategy
from .ma_crossover_rsi import MaCrossoverRsiStrategy
from .macd_rsi_advanced import MACDRSIAdvancedStrategy
from .macd_vortex_adx import MACDVortexADXStrategy
from .intraday_trend import IntradayTrendStrategy
from .swing_trend import SwingTrendStrategy
from .lazy_swing import LazySwingStrategy
from .swing_party import SwingPartyCoordinator

STRATEGY_REGISTRY: dict[str, type] = {
    "buy_and_hold": BuyAndHoldStrategy,
    "ma_crossover_rsi": MaCrossoverRsiStrategy,
    "macd_rsi_advanced": MACDRSIAdvancedStrategy,
    "macd_vortex_adx": MACDVortexADXStrategy,
    "intraday_trend": IntradayTrendStrategy,
    "swing_trend": SwingTrendStrategy,
    "lazy_swing": LazySwingStrategy,
    "swing_party": SwingPartyCoordinator,
}


def get_display_name(strategy_type: str) -> str:
    """Get the human-readable display name for a strategy type."""
    cls = STRATEGY_REGISTRY.get(strategy_type)
    return cls.display_name if cls and cls.display_name else strategy_type
