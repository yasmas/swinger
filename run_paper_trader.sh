#!/usr/bin/env bash
#
# Run the paper trading daemon.
#
# Usage:
#   ./run_paper_trader.sh                     # uses default config
#   ./run_paper_trader.sh config/custom.yaml  # custom config
#
# The daemon runs in the foreground. Stop it with Ctrl-C (SIGINT)
# or send SIGTERM — it will save state and shut down cleanly.
#
# Logs go to both stdout and data/live/paper_trader.log (configurable).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${1:-$SCRIPT_DIR/config/paper_trading.yaml}"

if [ ! -f "$CONFIG" ]; then
    echo "Error: config file not found: $CONFIG"
    echo "Usage: $0 [config.yaml]"
    exit 1
fi

# Activate venv if present
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

cd "$SCRIPT_DIR"
export PYTHONPATH="$SCRIPT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -m paper_trading.paper_trader "$CONFIG"
