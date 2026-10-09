@echo off
setlocal enabledelayedexpansion
title NiftyTrades Automated Trading System

echo ======================================================================
echo   NiftyTrades: Starting on Local Machine
echo ======================================================================

REM Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in PATH!
    echo Please install Python 3.10+ from https://python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

REM Check or create virtual environment
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP] Creating Python virtual environment (venv)...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo [SETUP] Activating virtual environment...
call venv\Scripts\activate.bat

echo [SETUP] Checking and installing dependencies...
pip install -r requirements.txt --quiet

REM Check if .env exists
if not exist ".env" (
    echo.
    echo ======================================================================
    echo [NOTICE] .env file not found! Please create .env with your credentials:
    echo ANGEL_API_KEY=...
    echo ANGEL_CLIENT_CODE=...
    echo ANGEL_PIN=...
    echo ANGEL_TOTP_SECRET=...
    echo ======================================================================
    echo.
)

echo [START] Launching NiftyTrades server...
echo.
python main.py --host 0.0.0.0 --port 8000

pause
