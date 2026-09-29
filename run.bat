@echo off
rem ==============================================================================
rem Cleo Launcher for Windows (cmd.exe, PowerShell, or double-click in Explorer)
rem ==============================================================================
setlocal EnableDelayedExpansion

cd /d "%~dp0"

rem 1. Check for Python in local virtualenv
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
    goto :run_cleo
)

rem 2. Check for system Python (py launcher, python, or python3)
where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :run_cleo
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :run_cleo
)

where python3 >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python3"
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
