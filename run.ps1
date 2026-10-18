# ==============================================================================
# Cleo Launcher for Windows PowerShell
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# 1. Auto-pull latest updates from GitHub (skip if CLEO_NO_UPDATE=1 or --no-update)
if ($env:CLEO_NO_UPDATE -ne "1" -and $args -notcontains "--no-update") {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd -and (Test-Path "$ScriptDir\.git")) {
        Write-Host "[Cleo] Checking for updates from GitHub..." -ForegroundColor Cyan
        & git fetch --tags --force origin
        if ($LASTEXITCODE -eq 0) {
            $currentBranch = (& git symbolic-ref --short -q HEAD 2>$null)
            if ($currentBranch) {
                & git pull --ff-only origin $currentBranch
                if ($LASTEXITCODE -ne 0) {
                    $status = (& git status --porcelain 2>$null)
                    if (-not $status) {
                        Write-Host "[Cleo] Fast-forward unavailable; syncing to origin/$currentBranch..." -ForegroundColor Yellow
                        & git reset --hard "origin/$currentBranch"
                    }
                }
            }
            $latestTag = (& git tag -l "v*" --sort=-v:refname 2>$null | Select-Object -First 1)
            if ($latestTag) {
                & git merge-base --is-ancestor $latestTag HEAD 2>$null
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "[Cleo] Updating to release $latestTag..." -ForegroundColor Cyan
                    & git checkout $latestTag
                    if ($LASTEXITCODE -ne 0) {
                        & git merge --ff-only $latestTag
                    }
                }
            }
            Write-Host "[Cleo] Up to date." -ForegroundColor Green
        } else {
            Write-Host "[Cleo] Could not reach GitHub; continuing with local version." -ForegroundColor Yellow
        }
        Write-Host ""
    }
}

# 2. Find working Python interpreter
$PyCmd = $null
$pyCheck = Get-Command python -ErrorAction SilentlyContinue
if ($pyCheck) {
    & python -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $PyCmd = "python" }
}

if (-not $PyCmd) {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & py -3 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $PyCmd = "py" }
    }
}

if (-not $PyCmd) {
    $python3Check = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3Check) {
        & python3 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $PyCmd = "python3" }
    }
}

if (-not $PyCmd) {
    $venvPy = Join-Path $ScriptDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) {
        $PyCmd = $venvPy
    }
}

if (-not $PyCmd) {
    Write-Host "----------------------------------------------------------------------" -ForegroundColor Red
    Write-Host "[Error] Python 3 was not found in your system PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "(Important: Check 'Add Python to PATH' during installation)" -ForegroundColor Yellow
    Write-Host "----------------------------------------------------------------------" -ForegroundColor Red
    Read-Host "Press Enter to exit..."
    exit 1
}

# 3. Launch Cleo server
if ($PyCmd -eq "py") {
    & py -3 cleo_server.py @args
} else {
    & $PyCmd cleo_server.py @args
}
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Cleo server exited with error code $LASTEXITCODE."
    Read-Host "Press Enter to exit..."
}
