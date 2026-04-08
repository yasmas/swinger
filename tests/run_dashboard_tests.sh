#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

.venv/bin/python3 -m pytest tests/test_dashboard_api.py -v "$@"
