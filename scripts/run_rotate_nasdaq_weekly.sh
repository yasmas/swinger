#!/usr/bin/env bash
# Run weekly Nasdaq rotation for a dashboard user (not dry-run).
# Refills data/backtests/nasdaq100 daily bars via Massive, then scores, 5m warmup, YAML.
# Add --bypass-daily-refill to skip daily download and last-Friday checks (best effort).
# Loads API keys from data/<user>/.env before running.
#
# Usage (from anywhere):
#   ./scripts/run_rotate_nasdaq_weekly.sh yasmas

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${1:-}"

if [[ -z "$USER_NAME" ]]; then
  echo "Usage: $0 <user>" >&2
  echo "  Example: $0 yasmas" >&2
  exit 1
fi

ENV_FILE="${REPO_ROOT}/data/${USER_NAME}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: missing ${ENV_FILE}" >&2
  echo "Create it with MASSIVE_API_KEY (and any other keys the rotation needs)." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

cd "${REPO_ROOT}"
export PYTHONPATH="${REPO_ROOT}/src"

if [[ ! -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  echo "ERROR: ${REPO_ROOT}/.venv/bin/python not found. Create the venv first." >&2
  exit 1
fi

exec "${REPO_ROOT}/.venv/bin/python" "${REPO_ROOT}/rotate_nasdaq_weekly.py" --user "${USER_NAME}"
