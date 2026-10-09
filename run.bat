@echo off
setlocal enabledelayedexpansion
title NiftyTrades Automated Trading System

:: Always change directory to the folder containing this batch script
cd /d "%~dp0"

echo ======================================================================
echo   NiftyTrades: Starting Automated Trading System
echo ======================================================================
echo.

:: 1. Detect Python command (python or py launcher)
set "PY_CMD="
where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel% equ 0 (
        set "PY_CMD=py"
    )
)

if "%PY_CMD%"=="" (
    echo ======================================================================
    echo [ERROR] Python is NOT installed or NOT added to PATH on this laptop!
    echo ======================================================================
    echo Python 3.10+ is required to run NiftyTrades.
    echo.
    echo Quick fix (takes 2 minutes):
    echo 1. Download Python: https://www.python.org/downloads/
    echo 2. Run installer and CHECK the box: "Add python.exe to PATH"
    echo 3. Finish installation, then double-click run.bat again.
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

echo [OK] Python found:
%PY_CMD% --version
echo.

:: 2. Check if running inside unextracted zip
if not exist "main.py" (
    echo ======================================================================
    echo [ERROR] main.py not found in the current directory!
    echo.
    echo If you downloaded a ZIP file from GitHub, please:
    echo 1. Right-click the ZIP file.
    echo 2. Select "Extract All...".
    echo 3. Open the extracted folder and double-click run.bat there.
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: 3. Setup Virtual Environment
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP 1/2] Creating virtual environment (venv)... This takes ~15 seconds.
    %PY_CMD% -m venv venv
    if %errorlevel% neq 0 (
        echo [WARNING] Could not create venv. Will use global python.
    )
)

if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    set "RUNNER=python"
) else (
    set "RUNNER=%PY_CMD%"
)

:: 4. Install Dependencies
echo [SETUP 2/2] Checking dependencies...
%RUNNER% -m pip install -r requirements.txt

:: 5. Check .env
if not exist ".env" (
    echo.
    echo ======================================================================
    echo [WARNING] .env file not found!
    echo Please make sure you have created .env with your Angel One credentials:
    echo ANGEL_API_KEY=...
    echo ANGEL_CLIENT_CODE=...
    echo ANGEL_PIN=...
    echo ANGEL_TOTP_SECRET=...
    echo ======================================================================
    echo.
)

:: 6. Launch Server
echo.
echo ======================================================================
echo   [START] Launching NiftyTrades Server...
echo ======================================================================
echo.
%RUNNER% main.py --host 0.0.0.0 --port 8000

echo.
echo [INFO] Server stopped.
pause
