@echo off
rem ==============================================================================
rem Cleo Launcher for Windows (cmd.exe, PowerShell, or double-click in Explorer)
rem ==============================================================================
setlocal EnableDelayedExpansion

cd /d "%~dp0"

rem 1. Import Cleo release signing key (once, silently — enables verified auto-updates)
where gpg >nul 2>nul
if %errorlevel% equ 0 (
    if exist "cleo-release.asc" (
        gpg --list-keys 3DB30B754E26A1D0C4B0308AD8C920CBEA46CBF6 >nul 2>nul
        if errorlevel 1 (
            gpg --import cleo-release.asc >nul 2>nul
            if not errorlevel 1 (
                echo [Cleo] Release signing key imported (enables verified updates).
            )
        )
    )
)

rem 2. Auto-pull latest verified signed release (skip if CLEO_NO_UPDATE=1 or --no-update)
if not "%CLEO_NO_UPDATE%"=="1" if not "%1"=="--no-update" (
    where git >nul 2>nul
    if not errorlevel 1 if exist ".git" (
        git fetch --tags --quiet origin 2>nul
        set "LATEST_TAG="
        for /f "tokens=*" %%T in ('git tag -l "v*" --sort=-v:refname 2^>nul') do (
            if not defined LATEST_TAG set "LATEST_TAG=%%T"
        )
        if defined LATEST_TAG (
            git merge-base --is-ancestor !LATEST_TAG! HEAD 2>nul
            if errorlevel 1 (
                where gpg >nul 2>nul
                if not errorlevel 1 (
                    git verify-tag !LATEST_TAG! >nul 2>nul
                    if not errorlevel 1 (
                        echo [Cleo] Updating to verified release !LATEST_TAG!...
                        git checkout !LATEST_TAG! --quiet 2>nul || git merge --ff-only !LATEST_TAG! --quiet 2>nul
                    )
                )
            )
        )
    )
)

rem 3. Check for working system Python (python, py launcher, or python3)
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

rem 4. Fall back to local virtualenv Python if present
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
