@echo off
rem ==============================================================================
rem Cleo Launcher for Windows (cmd.exe, PowerShell, or double-click in Explorer)
rem ==============================================================================
setlocal EnableDelayedExpansion

cd /d "%~dp0"

rem 1. Check for working system Python (python, py launcher, or python3)
python -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :run_cleo
)

py -3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :run_cleo
)

python3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python3"
    goto :run_cleo
)

rem 2. Fall back to local virtualenv Python if present
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
    goto :run_cleo
)

echo ----------------------------------------------------------------------
echo [Error] Python 3 was not found in your system PATH.
echo Please install Python 3.10+ from https://www.python.org/downloads/
echo (Important: Check "Add Python to PATH" during installation)
echo ----------------------------------------------------------------------
pause
exit /b 1

:run_cleo
%PY_CMD% cleo_server.py %*
if %errorlevel% neq 0 (
    echo.
    echo Cleo server exited with error code %errorlevel%.
    pause
)
