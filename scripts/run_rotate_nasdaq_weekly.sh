#!/usr/bin/env bash
# Run weekly Nasdaq rotation for a dashboard user (not dry-run).
# Refills data/backtests/nasdaq100 daily bars via Massive, then scores, 5m warmup, YAML.
#
# By default runs **two** parallel-ready configs for the upcoming equity week:
#   1) ``--profile momentum`` — Group 1 picks via momentum scoring.
#   2) ``--profile atr_roc5`` — Group 1 picks via atr_roc5. ATR keep-top fraction
#      defaults to 0.35; override with env ``NASDAQ_ATR_KEEP_TOP=0.5`` (example).
#
# Each profile writes ``data/<user>/nasdaq-<profile>-<date_tag>.yaml`` plus
# ``nasdaq-<profile>-<date_tag>-strategy.yaml`` and a dedicated week folder
# ``data/<user>/nasdaq-<profile>-<date_tag>/`` (CSV warmup, state, trades, logs, reports).
# Ledgers: ``nasdaq-weekly-summary-momentum.md`` and ``nasdaq-weekly-summary-atr_roc5.md``.
#
# Extra CLI args after <user> are passed to **each** ``rotate_nasdaq_weekly.py`` invocation, e.g.:
#   ./scripts/run_rotate_nasdaq_weekly.sh yasmas --dry-run
#   ./scripts/run_rotate_nasdaq_weekly.sh yasmas --bypass-daily-refill
#
# Legacy single-bot layout (``nasdaq-live``, ``nasdaq-<tag>.yaml`` only): run Python directly:
#   PYTHONPATH=src python rotate_nasdaq_weekly.py --user yasmas
#
# Loads API keys from data/<user>/.env before running.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="${1:-}"
shift || true
EXTRA_ARGS=("$@")

if [[ -z "$USER_NAME" ]]; then
  echo "Usage: $0 <user> [extra rotate_nasdaq_weekly.py args...]" >&2
  echo "  Example: $0 yasmas" >&2
  echo "  Example: NASDAQ_ATR_KEEP_TOP=0.5 $0 yasmas --bypass-daily-refill" >&2
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

PY="${REPO_ROOT}/.venv/bin/python"
ROT="${REPO_ROOT}/rotate_nasdaq_weekly.py"
ATR_TOP="${NASDAQ_ATR_KEEP_TOP:-0.35}"

echo "=== Nasdaq rotation: momentum profile (ZMQ tcp://localhost:5555) ===" >&2
"${PY}" "${ROT}" --user "${USER_NAME}" --profile momentum --scoring momentum "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

echo "=== Nasdaq rotation: atr_roc5 profile (ZMQ tcp://localhost:5555, --atr-keep-top ${ATR_TOP}) ===" >&2
"${PY}" "${ROT}" --user "${USER_NAME}" --profile atr_roc5 --scoring atr_roc5 --atr-keep-top "${ATR_TOP}" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
