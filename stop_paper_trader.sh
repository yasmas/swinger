#!/bin/bash
# Compatibility wrapper — use stop_swing_bot.sh instead.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/stop_swing_bot.sh" "$@"
