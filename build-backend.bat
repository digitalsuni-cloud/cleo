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

if exist ".venv\Scripts\pyinstaller.exe" (
    set "PYINSTALLER=.venv\Scripts\pyinstaller.exe"
) else (
    where pyinstaller >nul 2>nul
    if !errorlevel! equ 0 (
        set "PYINSTALLER=pyinstaller"
    ) else (
        if exist ".venv\Scripts\pip.exe" (
            echo [Backend] Installing PyInstaller into .venv...
            .venv\Scripts\pip.exe install -r requirements.txt pyinstaller
            set "PYINSTALLER=.venv\Scripts\pyinstaller.exe"
        ) else (
            set "PYINSTALLER=python -m PyInstaller"
        )
    )
)

%PYINSTALLER% --noconfirm --clean --onefile ^
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
