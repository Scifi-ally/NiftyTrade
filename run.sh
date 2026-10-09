#!/usr/bin/env bash
set -e

echo "======================================================================"
echo "  NiftyTrades: Starting on Local Machine"
echo "======================================================================"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed or not in PATH."
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "[SETUP] Creating Python virtual environment (venv)..."
    python3 -m venv venv
fi

echo "[SETUP] Activating virtual environment..."
source venv/bin/activate

echo "[SETUP] Installing dependencies..."
pip install -r requirements.txt --quiet

echo "[START] Launching NiftyTrades server..."
python3 main.py --host 0.0.0.0 --port 8000
