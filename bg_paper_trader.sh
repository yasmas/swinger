#!/usr/bin/env bash

./run_paper_trader.sh & echo $! > paper_trader.pid

echo "Paper trader running in background with PID $(cat paper_trader.pid)"

