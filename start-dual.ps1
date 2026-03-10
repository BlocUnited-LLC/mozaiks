<#
.SYNOPSIS
    Start both mozaiksai and mozaikscore substrates + frontend in local dev mode.

.DESCRIPTION
    Launches three processes:
      1. mozaiksai (port 8000) — AI runtime
      2. mozaikscore (port 8001) — Application services
      3. Frontend dev server (port 5173, optional)

    Requires .venv or system Python with project dependencies installed.
    MongoDB must be running (Docker or local).

.PARAMETER StartFrontend
    Also start the Vite dev server for chat-ui.

.PARAMETER CoreOnly
    Start only mozaikscore (port 8001), skip mozaiksai.

.PARAMETER AiOnly
    Start only mozaiksai (port 8000), skip mozaikscore.

.EXAMPLE
    .\start-dual.ps1 -StartFrontend
    .\start-dual.ps1 -CoreOnly
#>
param(
    [switch]$StartFrontend,
    [switch]$CoreOnly,
    [switch]$AiOnly,
    [int]$AiPort = 8000,
    [int]$CorePort = 8001
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Get-PythonExe {
    $venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) { return $venvPython }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    Write-Host "Python not found. Activate your .venv or install Python 3.11+." -ForegroundColor Red
    exit 1
}

$python = Get-PythonExe

# ── Environment defaults ─────────────────────────────────────────────────
$envFile = Join-Path $RepoRoot '.env'
if (Test-Path $envFile) {
    Write-Host "Loading .env..." -ForegroundColor Gray
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#]\S+?)\s*=\s*(.+)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
}

if (-not $env:MOZAIKS_APP_ID) { $env:MOZAIKS_APP_ID = "dev_app" }
$env:ENV = "development"
$env:CORE_PORT = $CorePort

Write-Host ""
Write-Host "=== Mozaiks Dual-Substrate Dev Launcher ===" -ForegroundColor Cyan
Write-Host "  AI Runtime (mozaiksai):   http://localhost:$AiPort" -ForegroundColor Green
Write-Host "  App Services (mozaikscore): http://localhost:$CorePort" -ForegroundColor Green
if ($StartFrontend) {
    Write-Host "  Frontend (Vite):          http://localhost:5173" -ForegroundColor Green
}
Write-Host ""

$jobs = @()

# ── mozaiksai (port 8000) ────────────────────────────────────────────────
if (-not $CoreOnly) {
    Write-Host "Starting mozaiksai on port $AiPort..." -ForegroundColor Yellow
    $aiJob = Start-Job -ScriptBlock {
        param($py, $root, $port)
        Set-Location $root
        & $py run_server.py
    } -ArgumentList $python, $RepoRoot, $AiPort
    $jobs += $aiJob
    Write-Host "  mozaiksai PID: $($aiJob.Id)" -ForegroundColor Gray
}

# ── mozaikscore (port 8001) ──────────────────────────────────────────────
if (-not $AiOnly) {
    Write-Host "Starting mozaikscore on port $CorePort..." -ForegroundColor Yellow
    $coreJob = Start-Job -ScriptBlock {
        param($py, $root, $port)
        Set-Location $root
        & $py run_core.py
    } -ArgumentList $python, $RepoRoot, $CorePort
    $jobs += $coreJob
    Write-Host "  mozaikscore PID: $($coreJob.Id)" -ForegroundColor Gray
}

# ── Frontend (port 5173) ─────────────────────────────────────────────────
if ($StartFrontend) {
    Write-Host "Starting frontend dev server..." -ForegroundColor Yellow
    $feJob = Start-Job -ScriptBlock {
        param($root)
        Set-Location (Join-Path $root 'chat-ui')
        npm run dev
    } -ArgumentList $RepoRoot
    $jobs += $feJob
    Write-Host "  Frontend PID: $($feJob.Id)" -ForegroundColor Gray
}

Write-Host ""
Write-Host "All processes started. Press Ctrl+C to stop." -ForegroundColor Cyan
Write-Host "Use 'Get-Job | Receive-Job' to see output." -ForegroundColor Gray
Write-Host ""

# ── Wait and forward output ──────────────────────────────────────────────
try {
    while ($true) {
        foreach ($j in $jobs) {
            $output = Receive-Job $j -ErrorAction SilentlyContinue
            if ($output) { Write-Host $output }
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host "`nStopping all processes..." -ForegroundColor Yellow
    $jobs | ForEach-Object { Stop-Job $_; Remove-Job $_ -Force }
    Write-Host "All stopped." -ForegroundColor Green
}
