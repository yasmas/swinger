"""Broker registry — maps broker type strings to classes."""

from brokers.paper_broker import PaperBroker
from brokers.coinbase_broker import CoinbaseBroker

BROKER_REGISTRY = {
    "paper": PaperBroker,
    "coinbase": CoinbaseBroker,
}
