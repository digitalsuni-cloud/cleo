@echo off
setlocal enabledelayedexpansion
:: Set code page to UTF-8 to support emojis in the terminal
chcp 65001 > nul

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
if exist "build-backend.bat" (
    call build-backend.bat --force
) else (
    bash build-backend.sh --force 2>nul
)

:: 3. Build Tauri App
echo 🏗️ Building Tauri application (portable, no installer)...
call npx @tauri-apps/cli build --no-bundle

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
