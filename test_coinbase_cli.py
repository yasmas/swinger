#!/usr/bin/env python3
"""Manual test CLI for Coinbase Advanced Trade API methods.

Usage:
    PYTHONPATH=src python3 test_coinbase_cli.py <command> [args...]

Commands:
    get-balance                  Show USD balance + CFM futures positions
    get-position [symbol]        Show position for symbol (default: product_id)
    get-bid-ask                  Show best bid/ask for the product
    get-price                    Show last traded price
    get-product-specs            Show product specifications (increments, min size)
    buy <usd_amount>             Place a GTC limit buy for $amount worth of contracts
    sell <num_contracts>         Place a GTC limit sell for N contracts
    chase-buy <usd_amount>       Chase engine buy: adaptive limit → market fallback
    chase-sell <num_contracts>   Chase engine sell: adaptive limit → market fallback
    get-order <order_id>         Check status of a Coinbase order
    cancel-order <order_id>      Cancel a Coinbase order
    list-futures                 List all available futures products
    debug-portfolios             Show raw portfolio data

Environment:
    COINBASE_KEY_FILE            Path to CDP API key JSON file (preferred)
    COINBASE_ADV_API_KEY         CDP API key name (alternative)
    COINBASE_ADV_API_SECRET      CDP API private key PEM (alternative)

Examples:
    PYTHONPATH=src python3 test_coinbase_cli.py get-balance
    PYTHONPATH=src python3 test_coinbase_cli.py get-bid-ask
    PYTHONPATH=src python3 test_coinbase_cli.py chase-buy 700
    PYTHONPATH=src python3 test_coinbase_cli.py chase-sell 1
    PYTHONPATH=src python3 test_coinbase_cli.py get-order abc-123-def
"""

import json
import logging
import os
import sys
import time

# Configure logging to see all API calls
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_cli")


def get_client():
    from exchange.coinbase_rest import CoinbaseRestClient

    # Option 1: JSON key file (preferred — set COINBASE_KEY_FILE=/path/to/cdp_api_key.json)
    # Option 2: Env vars COINBASE_ADV_API_KEY + COINBASE_ADV_API_SECRET
    key_file = os.environ.get("COINBASE_KEY_FILE")

    if key_file:
        logger.info("Loading credentials from file: %s", key_file)
        return CoinbaseRestClient({
            "product_id": "BIP-20DEC30-CDE",
            "api_secret": f"file:{key_file}",
        })

    api_key = os.environ.get("COINBASE_ADV_API_KEY")
    api_secret = os.environ.get("COINBASE_ADV_API_SECRET")

    if not api_key or not api_secret:
        print("ERROR: Set COINBASE_KEY_FILE=/path/to/cdp_api_key.json")
        print("   or: Set COINBASE_ADV_API_KEY and COINBASE_ADV_API_SECRET env vars")
        sys.exit(1)

    return CoinbaseRestClient({
        "product_id": "BIP-20DEC30-CDE",
        "api_key": api_key,
        "api_secret": api_secret,
    })


def pp(data):
    """Pretty-print a dict."""
    print(json.dumps(data, indent=2, default=str))


def cmd_get_balance(args):
    client = get_client()

    # Show all account balances
    all_balances = client.get_all_account_balances()
    print("=== Account Balances ===")
    for b in all_balances:
        if b["total"] > 0:
            print(f"  {b['currency']:>6s}: available={b['available']:.8f}, "
                  f"hold={b['hold']:.8f}, total={b['total']:.8f}")
    if not any(b["total"] > 0 for b in all_balances):
        print("  (all zero)")
    print(f"  ({len(all_balances)} accounts total)")

    # CFM futures balance
    try:
        cfm = client.get_cfm_balance()
        print(f"\n=== CFM Futures Balance ===")
        print(f"  Buying power:   ${cfm['buying_power']:.2f}")
        print(f"  Total USD:      ${cfm['total_usd_balance']:.2f}")
        print(f"  Unrealized PnL: ${cfm['unrealized_pnl']:.2f}")
        print(f"  Avail margin:   ${cfm['available_margin']:.2f}")
        print(f"  Init margin:    ${cfm['initial_margin']:.2f}")
    except Exception as e:
        print(f"\n=== CFM balance: unavailable ({e}) ===")

    # CFM positions
    try:
        positions = client.get_cfm_positions()
        if positions:
            print(f"\n=== Open Positions ({len(positions)}) ===")
            for p in positions:
                print(f"  {p['side']} {p['product_id']}: {p['number_of_contracts']} contracts "
                      f"@ avg={p['avg_entry_price']:.2f}, "
                      f"current={p['current_price']:.2f}, "
                      f"uPnL=${p['unrealized_pnl']:.2f}")
        else:
            print("\n=== Positions: flat (none) ===")
    except Exception as e:
        print(f"\n=== Positions: unavailable ({e}) ===")


def cmd_get_bid_ask(args):
    client = get_client()
    result = client.get_best_bid_ask(client.product_id)
    spread = result["ask_price"] - result["bid_price"]
    spread_bps = (spread / result["bid_price"]) * 10000
    print(f"Bid:    ${result['bid_price']:.2f}  (qty: {result['bid_qty']:.6f})")
    print(f"Ask:    ${result['ask_price']:.2f}  (qty: {result['ask_qty']:.6f})")
    print(f"Spread: ${spread:.2f}  ({spread_bps:.1f} bps)")
    print(f"Mid:    ${(result['bid_price'] + result['ask_price']) / 2:.2f}")


def cmd_get_price(args):
    client = get_client()
    price = client.get_current_price(client.product_id)
    print(f"Last price: ${price:.2f}")


def cmd_get_position(args):
    client = get_client()
    symbol = args[0] if args else client.product_id
    positions = client.get_cfm_positions()
    found = [p for p in positions if p["product_id"] == symbol]
    if not found:
        print(f"No open position for {symbol} (flat)")
    else:
        pp(found[0])


def cmd_get_product_specs(args):
    client = get_client()
    product = args[0] if args else client.product_id
    # Fetch full raw product data — show everything
    data = client._request(f"/api/v3/brokerage/market/products/{product}")
    pp(data)


def cmd_buy(args):
    if not args:
        print("Usage: buy <usd_amount>")
        sys.exit(1)

    usd_amount = float(args[0])
    client = get_client()
    specs = client.get_product_specs()
    contract_size = specs.get("contract_size", 1)

    price = client.get_current_price(client.product_id)
    notional_per_contract = contract_size * price
    num_contracts = int(usd_amount / notional_per_contract)

    bid_ask = client.get_best_bid_ask(client.product_id)
    tick = specs["quote_increment"]
    limit_price = bid_ask["ask_price"] - tick

    print(f"Price:      ${price:.2f}")
    print(f"Bid/Ask:    ${bid_ask['bid_price']:.2f} / ${bid_ask['ask_price']:.2f}")
    print(f"Contract:   {contract_size} {specs.get('contract_root_unit', 'BTC')} "
          f"(${notional_per_contract:.2f} per contract)")
    print(f"Contracts:  {num_contracts}")
    print(f"BTC total:  {num_contracts * contract_size:.4f}")
    print(f"Notional:   ${num_contracts * notional_per_contract:.2f}")
    print(f"Limit:      ${limit_price:.2f}  (ask - {tick} tick)")
    print()

    if num_contracts < 1:
        print(f"Cannot buy: need at least ${notional_per_contract:.2f} for 1 contract")
        return

    confirm = input("Place GTC limit BUY (post_only)? (y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    result = client.place_limit_order_gtc(
        side="BUY",
        base_size=num_contracts,
        limit_price=limit_price,
        post_only=True,
    )
    pp(result)
    print(f"\nOrder placed! Track with: get-order {result['order_id']}")


def cmd_sell(args):
    num_contracts = int(args[0]) if args else 0

    if num_contracts <= 0:
        print("Usage: sell <num_contracts>")
        sys.exit(1)

    client = get_client()
    specs = client.get_product_specs()
    contract_size = specs.get("contract_size", 1)

    bid_ask = client.get_best_bid_ask(client.product_id)
    tick = specs["quote_increment"]
    limit_price = bid_ask["bid_price"] + tick

    print(f"Bid/Ask:    ${bid_ask['bid_price']:.2f} / ${bid_ask['ask_price']:.2f}")
    print(f"Contracts:  {num_contracts}")
    print(f"BTC total:  {num_contracts * contract_size:.4f}")
    print(f"Limit:      ${limit_price:.2f}  (bid + {tick} tick)")
    print()

    confirm = input("Place GTC limit SELL (post_only)? (y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    result = client.place_limit_order_gtc(
        side="SELL",
        base_size=num_contracts,
        limit_price=limit_price,
        post_only=True,
    )
    pp(result)
    print(f"\nOrder placed! Track with: get-order {result['order_id']}")


def cmd_get_order(args):
    if not args:
        print("Usage: get-order <order_id>")
        sys.exit(1)

    client = get_client()
    result = client.get_order(args[0])
    pp(result)


def cmd_cancel_order(args):
    if not args:
        print("Usage: cancel-order <order_id>")
        sys.exit(1)

    client = get_client()
    results = client.cancel_orders([args[0]])
    pp(results)


def cmd_chase_buy(args):
    """Test the full chase engine with a small buy."""
    if not args:
        print("Usage: chase-buy <usd_amount>")
        sys.exit(1)

    from brokers.chase_engine import ChaseEngine, ChaseResult

    usd_amount = float(args[0])
    client = get_client()
    specs = client.get_product_specs()
    contract_size = specs.get("contract_size", 1)

    price = client.get_current_price(client.product_id)
    notional_per_contract = contract_size * price
    num_contracts = int(usd_amount / notional_per_contract)

    if num_contracts < 1:
        print(f"Cannot buy: need at least ${notional_per_contract:.2f} for 1 contract")
        return

    print(f"Chase BUY {num_contracts} contracts ({num_contracts * contract_size:.4f} BTC, ~${num_contracts * notional_per_contract:.2f})")
    confirm = input("Start chase? (y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    chase = ChaseEngine(client, {
        "reprice_interval_sec": 3,
        "offset_bps": 0,
        "min_reprice_bps": 2,
        "max_slippage_bps": 30,
        "timeout_sec": 120,
        "on_timeout": "cancel",  # cancel instead of market for testing
        "on_adverse_move": "cancel",
    })

    chase.start("BUY", num_contracts)

    print("\nChasing... (Ctrl+C to cancel)")
    try:
        while True:
            time.sleep(chase.reprice_interval_sec)
            result, details = chase.check()
            if result != ChaseResult.WAITING:
                print(f"\nResult: {result.value}")
                pp(details)
                break
    except KeyboardInterrupt:
        print("\nCancelling chase...")
        details = chase.cancel()
        if details:
            pp(details)


def cmd_chase_sell(args):
    """Test the full chase engine with a sell."""
    if not args:
        print("Usage: chase-sell <num_contracts>")
        sys.exit(1)

    from brokers.chase_engine import ChaseEngine, ChaseResult

    num_contracts = int(args[0])
    if num_contracts <= 0:
        print("num_contracts must be > 0")
        return

    client = get_client()
    specs = client.get_product_specs()
    contract_size = specs.get("contract_size", 1)
    price = client.get_current_price(client.product_id)

    print(f"Chase SELL {num_contracts} contracts ({num_contracts * contract_size:.4f} BTC, ~${num_contracts * contract_size * price:.2f})")
    confirm = input("Start chase? (y/N): ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return

    chase = ChaseEngine(client, {
        "reprice_interval_sec": 3,
        "offset_bps": 0,
        "min_reprice_bps": 2,
        "max_slippage_bps": 30,
        "timeout_sec": 120,
        "on_timeout": "cancel",
        "on_adverse_move": "cancel",
    })

    chase.start("SELL", num_contracts)

    print("\nChasing... (Ctrl+C to cancel)")
    try:
        while True:
            time.sleep(chase.reprice_interval_sec)
            result, details = chase.check()
            if result != ChaseResult.WAITING:
                print(f"\nResult: {result.value}")
                pp(details)
                break
    except KeyboardInterrupt:
        print("\nCancelling chase...")
        details = chase.cancel()
        if details:
            pp(details)


def cmd_debug_portfolios(args):
    client = get_client()
    data = client._auth_request("GET", "/api/v3/brokerage/portfolios")
    pp(data)

    # Also try listing INTX portfolios if there's a separate endpoint
    try:
        data2 = client._auth_request("GET", "/api/v3/brokerage/intx/portfolio")
        print("\n=== INTX Portfolio ===")
        pp(data2)
    except Exception as e:
        print(f"\n=== INTX portfolio endpoint: {e}")


def cmd_list_futures(args):
    """List all available futures products."""
    client = get_client()

    # List futures products
    data = client._request(
        "/api/v3/brokerage/market/products",
        params={"product_type": "FUTURE"},
    )
    products = data.get("products", [])
    print(f"=== Futures Products ({len(products)}) ===")
    for p in products:
        print(f"  {p.get('product_id'):30s}  status={p.get('status'):10s}  "
              f"price={p.get('price', 'N/A')}")

    # Also check for CFM (Coinbase Financial Markets) futures balance
    print("\n=== Futures Balance ===")
    try:
        data2 = client._auth_request("GET", "/api/v3/brokerage/cfm/balance_summary")
        pp(data2)
    except Exception as e:
        print(f"  CFM balance endpoint: {e}")

    # Check futures positions
    print("\n=== Futures Positions ===")
    try:
        data3 = client._auth_request("GET", "/api/v3/brokerage/cfm/positions")
        pp(data3)
    except Exception as e:
        print(f"  CFM positions endpoint: {e}")


COMMANDS = {
    "get-balance": cmd_get_balance,
    "get-position": cmd_get_position,
    "debug-portfolios": cmd_debug_portfolios,
    "list-futures": cmd_list_futures,
    "get-bid-ask": cmd_get_bid_ask,
    "get-price": cmd_get_price,
    "get-product-specs": cmd_get_product_specs,
    "buy": cmd_buy,
    "sell": cmd_sell,
    "get-order": cmd_get_order,
    "cancel-order": cmd_cancel_order,
    "chase-buy": cmd_chase_buy,
    "chase-sell": cmd_chase_sell,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        print("Available commands:")
        for name in COMMANDS:
            print(f"  {name}")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS)}")
        sys.exit(1)

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
