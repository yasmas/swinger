from brokers.coinbase_broker import CoinbaseBroker
from exchange.coinbase_rest import CoinbaseRestClient


def test_coinbase_broker_uses_cfm_equity_for_snapshot_and_cash(monkeypatch):
    exchange = CoinbaseRestClient({"product_id": "ETP-20DEC30-CDE"})

    monkeypatch.setattr(
        exchange,
        "get_cfm_balance",
        lambda: {
            "buying_power": 4900.0,
            "total_usd_balance": 10050.0,
            "cbi_usd_balance": 5100.0,
            "cfm_usd_balance": 4950.83,
            "unrealized_pnl": 12.75,
            "daily_realized_pnl": -8.25,
            "available_margin": 4800.0,
            "initial_margin": 150.0,
        },
    )
    monkeypatch.setattr(
        exchange,
        "get_cfm_positions",
        lambda: [
            {
                "product_id": "ETP-20DEC30-CDE",
                "side": "LONG",
                "number_of_contracts": 12,
                "avg_entry_price": 2300.0,
                "current_price": 2310.0,
                "unrealized_pnl": 12.75,
            }
        ],
    )

    broker = CoinbaseBroker(exchange)
    broker._contract_size = 0.1

    snapshot = broker.get_portfolio_snapshot()
    pv = broker.portfolio_view("ETP-20DEC30-CDE")

    assert snapshot.cash == 4950.83
    assert snapshot.total_value == 4963.58
    assert pv.cash == 4950.83
