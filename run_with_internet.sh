#!/usr/bin/env bash
set -e

echo "======================================================================"
echo "  NiftyTrades: Starting with Public Internet Access"
echo "======================================================================"

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is not installed or not in PATH."
    exit 1
fi

if [ ! -d "venv" ]; then
    echo "[SETUP] Creating Python virtual environment (venv)..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt --quiet

echo "[START] Starting NiftyTrades server..."
python3 main.py --host 0.0.0.0 --port 8000 &
SERVER_PID=$!

trap "kill $SERVER_PID 2>/dev/null || true" EXIT

sleep 3
echo "[START] Launching Cloudflare Internet Tunnel..."
python3 tunnel.py 8000
