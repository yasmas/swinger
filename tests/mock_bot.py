"""
Minimal ZMQ bot for dashboard API testing.
Connects as a DEALER, sends hello + heartbeats, responds to commands.
Usage: python tests/mock_bot.py <identity> [zmq_endpoint]
"""

import json
import os
import sys
import time

import zmq


def main():
    identity = sys.argv[1]
    endpoint = sys.argv[2] if len(sys.argv) > 2 else "tcp://localhost:5555"

    ctx = zmq.Context()
    sock = ctx.socket(zmq.DEALER)
    sock.identity = identity.encode()
    sock.connect(endpoint)

    sock.send_json({
        "type": "hello",
        "pid": os.getpid(),
        "started_at": time.time(),
        "strategy": "test_strategy",
        "version": "1.0",
        "display_name": "Test Bot",
        "exchange": "test",
        "symbol": "TESTUSDT",
        "portfolio_value": 100000,
        "cash": 100000,
        "position": "FLAT",
        "position_qty": 0,
        "last_price": 50000,
    })

    poller = zmq.Poller()
    poller.register(sock, zmq.POLLIN)
    paused = False

    while True:
        events = dict(poller.poll(1000))
        if sock in events:
            raw = sock.recv()
            msg = json.loads(raw)
            cmd = msg.get("type")
            if cmd == "quit":
                break
            elif cmd == "pause":
                paused = True
                sock.send_json({"type": "paused_ack", "paused": True})
            elif cmd == "resume":
                paused = False
                sock.send_json({"type": "paused_ack", "paused": False})
            elif cmd == "exit_trade":
                pass
        else:
            sock.send_json({
                "type": "status_update",
                "portfolio_value": 100000,
                "cash": 100000,
                "position": "FLAT",
                "position_qty": 0,
                "last_price": 50000,
                "paused": paused,
            })

    sock.close()
    ctx.term()


if __name__ == "__main__":
    main()
