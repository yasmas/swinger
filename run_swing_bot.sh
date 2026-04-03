#!/usr/bin/env bash
#
# Run the SwingBot trading daemon.
#
# Usage:
#   ./run_swing_bot.sh                     # uses default config
#   ./run_swing_bot.sh config/custom.yaml  # custom config
#
# The daemon runs in the foreground. Stop it with Ctrl-C (SIGINT)
# or send SIGTERM — it will save state and shut down cleanly.
#
# Logs go to both stdout and the log file configured in the YAML.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${1:-$SCRIPT_DIR/config/bot/live_coinbase.yaml}"

if [ ! -f "$CONFIG" ]; then
    echo "Error: config file not found: $CONFIG"
    echo "Usage: $0 [config.yaml]"
    exit 1
fi

cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

# Use venv Python if present, otherwise fall back to system Python
if [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

exec "$PYTHON" -m trading.swing_bot "$CONFIG"
