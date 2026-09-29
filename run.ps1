# ==============================================================================
# Cleo Launcher for Windows PowerShell
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PyCmd = $null
if ((Get-Command python -ErrorAction SilentlyContinue) -and (& python -c "import sys" 2>$null; $LASTEXITCODE -eq 0)) {
    $PyCmd = "python"
} elseif ((Get-Command py -ErrorAction SilentlyContinue) -and (& py -3 -c "import sys" 2>$null; $LASTEXITCODE -eq 0)) {
    $PyCmd = "py"
} elseif ((Get-Command python3 -ErrorAction SilentlyContinue) -and (& python3 -c "import sys" 2>$null; $LASTEXITCODE -eq 0)) {
    $PyCmd = "python3"
} elseif (Test-Path "$ScriptDir\.venv\Scripts\python.exe") {
    $PyCmd = "$ScriptDir\.venv\Scripts\python.exe"
} else {
    Write-Host "----------------------------------------------------------------------" -ForegroundColor Red
    Write-Host "[Error] Python 3 was not found in system PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "----------------------------------------------------------------------" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

if ($PyCmd -eq "py") {
    & py -3 cleo_server.py $args
} else {
    & $PyCmd cleo_server.py $args
}
