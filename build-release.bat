@echo off
setlocal enabledelayedexpansion
:: Set code page to UTF-8 to support emojis in the terminal
chcp 65001 > nul

cd /d "%~dp0"

echo 🚀 Starting build process for Windows...

:: 1. Archive any existing release packages
echo 📂 Checking for existing releases...
if exist "release\Tauri\*.exe" (
    echo 📦 Archiving previous releases...
    set "TS=%date:~-4,4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
    set "TS=!TS: =0!"
    set "DEST_DIR=release\archive\previous\!TS!"
    mkdir "!DEST_DIR!" 2>nul
    move /Y "release\Tauri\*.exe" "!DEST_DIR!\" >nul
)

:: 2. Build Python sidecar if needed
echo 🐍 Building Python backend sidecar...
if exist "%~dp0build-backend.bat" (
    call "%~dp0build-backend.bat" --force
) else if exist "build-backend.bat" (
    call build-backend.bat --force
) else (
    echo [Backend] Running build-backend.sh...
    bash build-backend.sh --force
)

if not exist "src-tauri\binaries\cleo-server-x86_64-pc-windows-msvc.exe" (
    echo.
    echo ❌ ERROR: Python backend sidecar was not generated:
    echo    src-tauri\binaries\cleo-server-x86_64-pc-windows-msvc.exe
    echo.
    echo Please resolve the backend build error above before building the Tauri package.
    exit /b 1
)

:: 3. Build Tauri App
echo 🏗️ Building Tauri application (portable, no installer)...
call npx @tauri-apps/cli build --no-bundle
if errorlevel 1 (
    echo.
    echo ❌ ERROR: Tauri build failed!
    exit /b 1
)

:: 4. Prepare release directory
echo 📂 Preparing release directory...
if not exist release\Tauri mkdir release\Tauri

:: 5. Extract version
for /f "tokens=*" %%a in ('node -p "require('./package.json').version"') do set VERSION=%%a
echo 📌 Version detected: %VERSION%

:: 6. Copy the cleo.exe directly
echo 🔍 Copying cleo.exe to release\Tauri...
set "SRC=src-tauri\target\release\cleo.exe"
set "DEST=release\Tauri\Cleo_%VERSION%_x64.exe"

if exist "%SRC%" (
    copy /Y "%SRC%" "%DEST%"
    echo ✅ Copied to %DEST%
) else (
    echo ❌ ERROR: cleo.exe not found at %SRC%
    exit /b 1
)

echo 🎉 Build complete! Check the release\Tauri folder.
dir release\Tauri
