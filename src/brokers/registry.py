"""Broker registry — maps broker type strings to classes."""

from brokers.paper_broker import PaperBroker

BROKER_REGISTRY = {
    "paper": PaperBroker,
}
