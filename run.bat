@echo off
echo ====================================================
echo Starting TVPM Validation Hub Services on Windows...
echo ====================================================

:: Check if virtual environment directory exists
if not exist .venv (
    echo [.venv] folder not found. Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment. Make sure python is on PATH.
        pause
        exit /b 1
    )
    echo Installing dependencies...
    .venv\Scripts\pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

:: Check if .env file exists, copy from example if not
if not exist .env (
    if exist .env.example (
        echo [.env] file not found. Copying from .env.example...
        copy .env.example .env
    ) else (
        echo [WARNING] Neither .env nor .env.example was found.
    )
)

:: Start the service runner
echo Launching services...
.venv\Scripts\python run.py

pause
