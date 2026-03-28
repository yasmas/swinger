#!/usr/bin/env bash
# Compatibility wrapper — use run_swing_bot.sh instead.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_swing_bot.sh" "$@"
