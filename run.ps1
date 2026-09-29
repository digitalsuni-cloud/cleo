# ==============================================================================
# Cleo Launcher for Windows PowerShell
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PyCmd = $null
if (Test-Path "$ScriptDir\.venv\Scripts\python.exe") {
    $PyCmd = "$ScriptDir\.venv\Scripts\python.exe"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PyCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PyCmd = "python"
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $PyCmd = "python3"
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
