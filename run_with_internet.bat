@echo off
setlocal enabledelayedexpansion
title NiftyTrades with Public Internet Access

echo ======================================================================
echo   NiftyTrades: Starting with Public Internet Access
echo ======================================================================

REM Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.10+ from https://python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

REM Setup venv
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP] Creating Python virtual environment (venv)...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet

echo [START] Starting NiftyTrades server in background...
start "NiftyTrades Server" cmd /k "venv\Scripts\activate.bat && python main.py --host 0.0.0.0 --port 8000"

echo [WAIT] Waiting 3 seconds for server to initialize...
timeout /t 3 /nobreak >nul

echo [START] Launching Cloudflare Internet Tunnel...
python tunnel.py 8000

pause
