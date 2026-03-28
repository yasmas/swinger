#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_FILE="$SCRIPT_DIR/data/live/swing_bot.lock"

if [ ! -f "$LOCK_FILE" ]; then
    echo "No lock file found at $LOCK_FILE — SwingBot is not running."
    exit 0
fi

PID=$(cat "$LOCK_FILE" 2>/dev/null)

if [ -z "$PID" ]; then
    echo "Lock file is empty — SwingBot is not running."
    rm -f "$LOCK_FILE"
    exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
    echo "PID $PID is not running — stale lock file. Removing."
    rm -f "$LOCK_FILE"
    exit 0
fi

echo "Sending SIGTERM to SwingBot (PID $PID)..."
kill "$PID"

for i in {1..10}; do
    sleep 0.5
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "SwingBot stopped."
        exit 0
    fi
done

echo "Process still running after 5s — sending SIGKILL..."
kill -9 "$PID"
echo "SwingBot killed."
