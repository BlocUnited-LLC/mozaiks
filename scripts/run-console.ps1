<#
.SYNOPSIS
    Start the Mozaiks Console — backend + frontend in one command.

.DESCRIPTION
    Opens a new terminal window for the backend (uvicorn / Studio host) and
    starts the frontend (Vite dev server) in the current terminal.

    Use this after first-time setup with bootstrap-builder.ps1. Press Ctrl+C
    in the frontend terminal to stop the frontend; close the backend terminal
    to stop the backend.

.PARAMETER BackendPort
    Port for the Studio backend (default 8000).

.PARAMETER FrontendPort
    Port for the Vite dev server (default 3000).

.PARAMETER ForceStop
    Kill any existing process already listening on either port before starting.

.PARAMETER SkipInfra
    Skip the Docker Compose infra check (MongoDB) on backend startup.

.EXAMPLE
    .\scripts\run-console.ps1

.EXAMPLE
    .\scripts\run-console.ps1 -ForceStop

.EXAMPLE
    .\scripts\run-console.ps1 -BackendPort 8001 -FrontendPort 3001
#>

param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$ForceStop,
    [switch]$SkipInfra
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$shellExe = (Get-Command pwsh -ErrorAction SilentlyContinue)?.Source
if (-not $shellExe) {
    $shellExe = (Get-Command powershell -ErrorAction SilentlyContinue)?.Source
}
if (-not $shellExe) {
    Write-Host "[console] No PowerShell executable found on PATH." -ForegroundColor Red
    exit 1
}

# Build backend command string for the new terminal window
$backendCmd = "& '$ScriptDir\run-backend.ps1' -Port $BackendPort"
if ($ForceStop) { $backendCmd += " -ForceStop" }
if ($SkipInfra) { $backendCmd += " -SkipInfra" }

Write-Host "[console] Opening backend terminal (port $BackendPort)..." -ForegroundColor Cyan
Start-Process -FilePath $shellExe -ArgumentList "-NoExit", "-Command", $backendCmd

# Brief pause so the backend window title appears before frontend output starts
Start-Sleep -Milliseconds 500

Write-Host "[console] Starting frontend (port $FrontendPort)..." -ForegroundColor Cyan
Write-Host "[console] Console: http://localhost:$FrontendPort/apps" -ForegroundColor Yellow
Write-Host "[console] Press Ctrl+C here to stop the frontend." -ForegroundColor DarkGray
Write-Host ""

$frontendParams = @{ Port = $FrontendPort }
if ($ForceStop) { $frontendParams['ForceStop'] = $true }
& "$ScriptDir/run-frontend.ps1" @frontendParams
