"""Exchange registry — maps exchange type strings to client classes."""

from exchange.alpaca_rest import AlpacaRestClient
from exchange.binance_rest import BinanceRestClient
from exchange.coinbase_rest import CoinbaseRestClient
from exchange.base import ExchangeClient

EXCHANGE_REGISTRY: dict[str, type[ExchangeClient]] = {
    "alpaca":   AlpacaRestClient,
    "binance":  BinanceRestClient,
    "coinbase": CoinbaseRestClient,
}


def create_exchange(config: dict) -> ExchangeClient:
    """Create an exchange client from a config dict.

    Config must have a 'type' key (default: 'binance').
    """
    exchange_type = config.get("type", "binance")
    if exchange_type not in EXCHANGE_REGISTRY:
        raise ValueError(
            f"Unknown exchange type: '{exchange_type}'. "
            f"Available: {list(EXCHANGE_REGISTRY.keys())}"
        )
    return EXCHANGE_REGISTRY[exchange_type](config)
