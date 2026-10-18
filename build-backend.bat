@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "TARGET=x86_64-pc-windows-msvc"
set "BIN_DIR=src-tauri\binaries"
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"
set "OUT_BIN=%BIN_DIR%\cleo-server-%TARGET%.exe"

if exist "%OUT_BIN%" if not "%1"=="--force" (
    echo [Backend] Binary already exists: %OUT_BIN% (pass --force to rebuild)
    exit /b 0
)

echo [Backend] Building Cleo standalone backend for %TARGET%...

rem 1. Resolve Python interpreter (check .venv, python, py -3, python3)
set "PY_CMD="
if exist ".venv\Scripts\python.exe" (
    set "PY_CMD=.venv\Scripts\python.exe"
    goto :python_found
)
python -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :python_found
)
py -3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py -3"
    goto :python_found
)
python3 -c "import sys" >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python3"
    goto :python_found
)

:python_found
if not defined PY_CMD (
    echo [Backend] ERROR: Python 3 was not found in PATH or .venv.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo (Important: Check "Add Python to PATH" during installation)
    exit /b 1
)

echo [Backend] Using Python interpreter: %PY_CMD%

rem 2. Resolve or install PyInstaller
set "PYINSTALLER="
if exist ".venv\Scripts\pyinstaller.exe" (
    set "PYINSTALLER=.venv\Scripts\pyinstaller.exe"
    goto :pyinstaller_found
)
where pyinstaller >nul 2>nul
if not errorlevel 1 (
    set "PYINSTALLER=pyinstaller"
    goto :pyinstaller_found
)
%PY_CMD% -c "import PyInstaller" >nul 2>nul
if not errorlevel 1 (
    set "PYINSTALLER=%PY_CMD% -m PyInstaller"
    goto :pyinstaller_found
)

echo [Backend] PyInstaller not detected. Preparing virtual environment and dependencies...
if not exist ".venv\Scripts\python.exe" (
    echo [Backend] Creating isolated virtual environment in .venv...
    %PY_CMD% -m venv .venv
)
if exist ".venv\Scripts\pip.exe" (
    echo [Backend] Installing requirements.txt and PyInstaller into .venv...
    call .venv\Scripts\pip.exe install --upgrade pip
    call .venv\Scripts\pip.exe install -r requirements.txt pyinstaller
    if exist ".venv\Scripts\pyinstaller.exe" (
        set "PYINSTALLER=.venv\Scripts\pyinstaller.exe"
        goto :pyinstaller_found
    )
) else (
    echo [Backend] Installing requirements.txt and PyInstaller into environment...
    call %PY_CMD% -m pip install -r requirements.txt pyinstaller
    %PY_CMD% -c "import PyInstaller" >nul 2>nul
    if not errorlevel 1 (
        set "PYINSTALLER=%PY_CMD% -m PyInstaller"
        goto :pyinstaller_found
    )
)

:pyinstaller_found
if not defined PYINSTALLER (
    echo [Backend] ERROR: PyInstaller could not be located or installed.
    echo Please run manually:
    echo    pip install -r requirements.txt pyinstaller
    exit /b 1
)

echo [Backend] Using PyInstaller: %PYINSTALLER%
call %PYINSTALLER% --noconfirm --clean --onefile ^
  --name "cleo-server-%TARGET%" ^
  --distpath "%BIN_DIR%" ^
  --workpath "build\pyinstaller" ^
  --add-data "cleo_ui.html;." ^
  --add-data ".agents\skills\cloud-finops;.agents\skills\cloud-finops" ^
  --hidden-import "uvicorn" ^
  --hidden-import "uvicorn.logging" ^
  --hidden-import "uvicorn.loops" ^
  --hidden-import "uvicorn.loops.auto" ^
  --hidden-import "uvicorn.protocols" ^
  --hidden-import "uvicorn.protocols.http" ^
  --hidden-import "uvicorn.protocols.http.auto" ^
  --hidden-import "uvicorn.protocols.websockets" ^
  --hidden-import "uvicorn.lifespan" ^
  --hidden-import "uvicorn.lifespan.on" ^
  --hidden-import "uvicorn.lifespan.off" ^
  --hidden-import "uvicorn.lifespan.auto" ^
  --hidden-import "fastapi" ^
  --hidden-import "starlette" ^
  --hidden-import "zstandard" ^
  --hidden-import "cleo_agent" ^
  --hidden-import "cleo_mcp" ^
  --hidden-import "cleo_finops_refs" ^
  --hidden-import "cleo_query" ^
  --hidden-import "cleo_charts" ^
  --hidden-import "cleo_llm" ^
  --hidden-import "cleo_logger" ^
  --hidden-import "cleo_memory" ^
  --hidden-import "cleo_market_data" ^
  --hidden-import "auth" ^
  --hidden-import "auth.oauth" ^
  --hidden-import "cryptography" ^
  --hidden-import "cleo_crypto" ^
  cleo_server.py

if exist "%OUT_BIN%" (
    echo [Backend] Backend built successfully: %OUT_BIN%
) else (
    echo [Backend] ERROR: Failed to produce %OUT_BIN%
    exit /b 1
)
