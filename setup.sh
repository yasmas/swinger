#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "=== Python venv ==="
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

echo ""
echo "=== Dashboard server ==="
cd dashboard
npm install

echo ""
echo "=== Dashboard client ==="
cd client
npm install
npm run build

echo ""
echo "=== Done ==="
echo "To run the dashboard:  cd dashboard && npm start"
echo "To run a backtest:     source .venv/bin/activate && PYTHONPATH=src python3 run_backtest.py config/swing_trend_dev_v14.yaml"
