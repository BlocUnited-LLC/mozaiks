<#
.SYNOPSIS
    MozaiksAI Test Run - Full cleanse + full run with logging

.DESCRIPTION
    One-command workflow for testing:
    - Full cleanse (Python cache, Docker, logs, DB collections)
    - Ensures port 8000 is free (WSL shutdown if needed)
    - Starts dev environment with logging
    - Starts frontend

.PARAMETER Mode
    'docker' (default) or 'local' - which backend mode to use

.PARAMETER AppPort
    Port for the backend (default 8000)

.PARAMETER KeepDB
    Skip MongoDB collection cleanup during cleanse

.PARAMETER NoFrontend
    Don't start the frontend (ChatUI)

.EXAMPLE
    .\scripts\test-run.ps1
    # Full cleanse + docker mode + frontend + logs

.EXAMPLE
    .\scripts\test-run.ps1 -Mode local
    # Full cleanse + local backend + frontend + logs

.EXAMPLE
    .\scripts\test-run.ps1 -KeepDB
    # Full cleanse but preserve MongoDB data
#>

param(
    [ValidateSet('docker','local')]
    [string]$Mode = 'docker',
    [int]$AppPort = 8000,
    [switch]$KeepDB,
    [switch]$AllowNonLocalMongo,
    [switch]$NoFrontend
)

$ErrorActionPreference = 'Stop'
$ScriptRoot = Split-Path -Parent $PSScriptRoot

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  MozaiksAI Test Run" -ForegroundColor Cyan
Write-Host "  Mode: $Mode | Port: $AppPort" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Step 1: Activate venv
Write-Host "[1/5] Activating virtual environment..." -ForegroundColor Yellow
$venvActivate = Join-Path -Path $ScriptRoot -ChildPath ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    & $venvActivate
    Write-Host "  ✅ Virtual environment activated" -ForegroundColor Green
} else {
    Write-Host "  ⚠️  .venv not found at $venvActivate" -ForegroundColor Yellow
    Write-Host "  Continuing without venv activation..." -ForegroundColor Gray
}

# Step 2: Full cleanse
Write-Host "`n[2/5] Running full cleanse..." -ForegroundColor Yellow
$cleanseParams = @{
    Full = $true
}
if ($KeepDB) {
    $cleanseParams['KeepDB'] = $true
}
if ($AllowNonLocalMongo) {
    $cleanseParams['AllowNonLocalMongo'] = $true
}
$cleanseScript = Join-Path -Path $ScriptRoot -ChildPath "scripts\cleanse.ps1"
& $cleanseScript @cleanseParams
# Note: cleanse.ps1 doesn't set exit code properly, so we won't check LASTEXITCODE
Write-Host "  ✅ Cleanse complete" -ForegroundColor Green

# Step 3: Free port if needed
Write-Host "`n[3/5] Checking port $AppPort..." -ForegroundColor Yellow
$portCheck = netstat -aon | Select-String ":$AppPort\s"
if ($portCheck) {
    Write-Host "  ⚠️  Port $AppPort in use" -ForegroundColor Yellow
    
    # First try to stop Docker containers that might be using the port
    Write-Host "  Stopping any Docker containers..." -ForegroundColor Gray
    $allContainers = docker ps -q 2>$null
    if ($allContainers) {
        docker stop $allContainers 2>$null | Out-Null
        Start-Sleep -Seconds 2
    }
    
    # Check port again after Docker stop
    $portCheck = netstat -aon | Select-String ":$AppPort\s"
    if ($portCheck) {
        Write-Host "  Port still in use - running wsl --shutdown to free it..." -ForegroundColor Yellow
        wsl --shutdown 2>$null
        Start-Sleep -Seconds 3
        Write-Host "  ✅ WSL shutdown complete" -ForegroundColor Green
    } else {
        Write-Host "  ✅ Port freed by stopping Docker containers" -ForegroundColor Green
    }
} else {
    Write-Host "  ✅ Port $AppPort is free" -ForegroundColor Green
}

# Step 4: Start dev environment with logging
Write-Host "`n[4/5] Starting dev environment ($Mode mode)..." -ForegroundColor Yellow
$startDevScript = Join-Path -Path $ScriptRoot -ChildPath "start-dev.ps1"

# Build parameters as a hashtable (proper PowerShell splatting)
$startDevParams = @{
    Mode = $Mode
    AppPort = $AppPort
    TailInPlace = $true
}
if (-not $NoFrontend) {
    $startDevParams['StartFrontend'] = $true
}

Write-Host "  Starting with: Mode=$Mode AppPort=$AppPort TailInPlace=`$true StartFrontend=$(-not $NoFrontend)" -ForegroundColor Gray

# Note: -TailInPlace will block here streaming logs until Ctrl+C
# If you want non-blocking, remove TailInPlace and add TailLogs instead
& $startDevScript @startDevParams

# Step 5: Cleanup message (only shown after user stops with Ctrl+C)
Write-Host "`n[5/5] Test run stopped" -ForegroundColor Yellow
Write-Host "  To capture logs, run: .\scripts\capture-logs.ps1 -SinceMinutes 30" -ForegroundColor Gray
Write-Host "`nDone!`n" -ForegroundColor Cyan
