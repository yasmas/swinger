from brokers.coinbase_broker import CoinbaseBroker
from exchange.coinbase_rest import CoinbaseRestClient


def test_coinbase_broker_uses_available_margin_for_equity_and_buying_power_for_cash(monkeypatch):
    exchange = CoinbaseRestClient({"product_id": "ETP-20DEC30-CDE"})

    monkeypatch.setattr(
        exchange,
        "get_cfm_balance",
        lambda: {
            "buying_power": 4844.65,
            "total_usd_balance": 4950.83,
            "cbi_usd_balance": 4950.83,
            "cfm_usd_balance": 0.0,
            "total_open_orders_hold_amount": 12.0,
            "unrealized_pnl": 0.0,
            "daily_realized_pnl": -68.60,
            "available_margin": 4882.23,
            "initial_margin": 0.0,
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

    assert snapshot.cash == 4844.65
    assert snapshot.total_value == 4894.23
    assert pv.cash == 4844.65
