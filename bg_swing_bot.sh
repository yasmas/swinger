#!/usr/bin/env bash

./run_swing_bot.sh "$@" & echo $! > swing_bot.pid

echo "SwingBot running in background with PID $(cat swing_bot.pid)"
