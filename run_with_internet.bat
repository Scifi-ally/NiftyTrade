@echo off
setlocal enabledelayedexpansion
title NiftyTrades with Public Internet Access

:: Always change directory to the folder containing this batch script
cd /d "%~dp0"

echo ======================================================================
echo   NiftyTrades: Starting with Public Internet Access
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
    echo 3. Finish installation, then double-click run_with_internet.bat again.
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: 2. Check if running inside unextracted zip
if not exist "main.py" (
    echo ======================================================================
    echo [ERROR] main.py not found in current directory!
    echo If you downloaded a ZIP, please right-click and "Extract All..." first.
    echo ======================================================================
    echo.
    pause
    exit /b 1
)

:: 3. Setup Virtual Environment
if not exist "venv\Scripts\activate.bat" (
    echo [SETUP 1/2] Creating virtual environment (venv)... This takes ~15 seconds.
    %PY_CMD% -m venv venv
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

:: 5. Start Server in background window
echo.
echo [START] Launching NiftyTrades server in background...
start "NiftyTrades Server" cmd /k "cd /d ""%~dp0"" && call venv\Scripts\activate.bat 2>nul && %RUNNER% main.py --host 0.0.0.0 --port 8000"

echo [WAIT] Waiting 3 seconds for server to initialize...
timeout /t 3 /nobreak >nul

:: 6. Launch Cloudflare Internet Tunnel
echo [START] Starting Cloudflare Internet Tunnel...
%RUNNER% tunnel.py 8000

echo.
echo Tunnel stopped.
pause
