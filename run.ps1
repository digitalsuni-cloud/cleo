# ==============================================================================
# Cleo Launcher for Windows PowerShell
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# 1. Import Cleo release signing key (once, silently — enables verified auto-updates)
$_CleoKeyFp = "3DB30B754E26A1D0C4B0308AD8C920CBEA46CBF6"
$_CleoAsc   = Join-Path $ScriptDir "cleo-release.asc"
if ((Get-Command gpg -ErrorAction SilentlyContinue) -and (Test-Path $_CleoAsc)) {
    $alreadyImported = (& gpg --list-keys $_CleoKeyFp 2>$null; $LASTEXITCODE -eq 0)
    if (-not $alreadyImported) {
        & gpg --import $_CleoAsc 2>$null
        if ($LASTEXITCODE -eq 0) { Write-Host "✓ Cleo release signing key imported (enables verified updates)." }
    }
}

# 2. Auto-pull latest verified signed release (skip if CLEO_NO_UPDATE=1 or --no-update)
if ($env:CLEO_NO_UPDATE -ne "1" -and $args -notcontains "--no-update") {
    if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path "$ScriptDir\.git")) {
        & git fetch --tags --quiet origin 2>$null
        $latestTag = (& git tag -l "v*" --sort=-v:refname 2>$null | Select-Object -First 1)
        if ($latestTag) {
            & git merge-base --is-ancestor $latestTag HEAD 2>$null
            if ($LASTEXITCODE -ne 0) {
                if (Get-Command gpg -ErrorAction SilentlyContinue) {
                    & git verify-tag $latestTag 2>$null
                    if ($LASTEXITCODE -eq 0) {
                        Write-Host "🔄 Updating Cleo to verified release $latestTag..."
                        & git checkout $latestTag --quiet 2>$null
                    } else {
                        Write-Host "⚠️  New release $latestTag found, but signature could not be verified. Skipping update." -ForegroundColor Yellow
                    }
                }
            }
        }
    }
}

# 3. Find Python
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
