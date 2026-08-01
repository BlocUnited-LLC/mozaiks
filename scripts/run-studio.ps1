<#
.SYNOPSIS
    Start Mozaiks Studio — backend + frontend in one command.

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
    Also clears prior run logs and runtime artifacts so the next run starts clean.

.PARAMETER SkipInfra
    Skip the Docker Compose infra check (MongoDB) on backend startup.

.PARAMETER BackendReadyTimeoutSeconds
    Seconds to wait for the Studio backend to answer /api/shell-config before
    starting the frontend (default 90).

.PARAMETER SkipBackendWait
    Start the frontend immediately after launching the backend terminal.

.EXAMPLE
    .\scripts\run-studio.ps1

.EXAMPLE
    .\scripts\run-studio.ps1 -ForceStop

.EXAMPLE
    .\scripts\run-studio.ps1 -BackendPort 8001 -FrontendPort 3001
#>

param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [string]$PlatformPath = "",
    [string]$AppWorkspacePath = "",
    [int]$BackendReadyTimeoutSeconds = 90,
    [switch]$ForceStop,
    [switch]$SkipInfra,
    [switch]$SkipBackendWait
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$_pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
$shellExe = if ($_pwsh) { $_pwsh.Source } else { $null }
if (-not $shellExe) {
    $_ps = Get-Command powershell -ErrorAction SilentlyContinue
    $shellExe = if ($_ps) { $_ps.Source } else { $null }
}
if (-not $shellExe) {
    Write-Host "[studio] No PowerShell executable found on PATH." -ForegroundColor Red
    exit 1
}

function Quote-PowerShellArgument {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Wait-ForHttpOk {
    param(
        [string]$Name,
        [string]$Uri,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null

    Write-Host ("[studio] Waiting for {0}: {1}" -f $Name, $Uri) -ForegroundColor Cyan
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host ("[studio] {0} ready: {1}" -f $Name, $Uri) -ForegroundColor Green
                return
            }
            $lastError = "HTTP $($response.StatusCode)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }

    Write-Host ("[studio] {0} did not become ready within {1}s." -f $Name, $TimeoutSeconds) -ForegroundColor Red
    Write-Host "[studio] Check the backend terminal for the first red error. Common causes: Docker Desktop is not running, MongoDB is unreachable, or Python dependencies are missing." -ForegroundColor Yellow
    if ($lastError) {
        Write-Host ("[studio] Last readiness error: {0}" -f $lastError) -ForegroundColor DarkYellow
    }
    throw "$Name readiness timed out."
}

function Get-ListeningProcessIds {
    param([int]$LocalPort)

    $procIds = @()
    try {
        $procIds = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction Stop |
            Select-Object -ExpandProperty OwningProcess -Unique
    } catch {
        $lines = netstat -ano | Select-String ":$LocalPort\s+.*LISTENING"
        $procIds = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
    }

    return $procIds | Where-Object { $_ -and $_ -ne 0 } | ForEach-Object { [int]$_ }
}

function Stop-ListeningPorts {
    param([int[]]$Ports)

    $stopped = @{}
    foreach ($port in $Ports) {
        foreach ($procId in (Get-ListeningProcessIds -LocalPort $port)) {
            $key = [string]$procId
            if ($stopped.ContainsKey($key)) {
                continue
            }
            try {
                Write-Host ("[studio] ForceStop: stopping PID {0} on port {1}" -f $procId, $port) -ForegroundColor Yellow
                Stop-Process -Id $procId -Force -ErrorAction Stop
                $stopped[$key] = $true
            } catch {
                Write-Host ("[studio] ForceStop: could not stop PID {0}: {1}" -f $procId, $_.Exception.Message) -ForegroundColor DarkYellow
            }
        }
    }

    if ($stopped.Count -gt 0) {
        Start-Sleep -Milliseconds 350
    }
}

function Clear-PreviousRunFiles {
    $cleanupScript = Join-Path $ScriptDir "clean-runtime-artifacts.ps1"
    if (-not (Test-Path -LiteralPath $cleanupScript)) {
        Write-Host "[studio] Clean start skipped: scripts\clean-runtime-artifacts.ps1 was not found." -ForegroundColor DarkYellow
        return
    }

    Write-Host "[studio] ForceStop clean start: clearing logs and runtime artifacts..." -ForegroundColor Cyan
    & $cleanupScript -IncludeMainLogs -Quiet
}

# Build backend command string for the new terminal window
$backendScript = Join-Path $ScriptDir "run-backend.ps1"
$backendCmd = "& $(Quote-PowerShellArgument $backendScript) -Port $BackendPort"
if ($ForceStop) { $backendCmd += " -ForceStop" }
if ($SkipInfra) { $backendCmd += " -SkipInfra" }
if ($PlatformPath) { $backendCmd += " -PlatformPath $(Quote-PowerShellArgument $PlatformPath)" }
if ($AppWorkspacePath) { $backendCmd += " -AppWorkspacePath $(Quote-PowerShellArgument $AppWorkspacePath)" }

if ($ForceStop) {
    Stop-ListeningPorts -Ports @($BackendPort, $FrontendPort)
    Clear-PreviousRunFiles
}

Write-Host "[studio] Opening backend terminal (port $BackendPort)..." -ForegroundColor Cyan
Start-Process -FilePath $shellExe -ArgumentList "-NoExit", "-Command", $backendCmd

if (-not $SkipBackendWait) {
    Wait-ForHttpOk -Name "backend shell config" -Uri "http://localhost:$BackendPort/api/shell-config" -TimeoutSeconds $BackendReadyTimeoutSeconds
} else {
    Write-Host "[studio] Backend readiness wait skipped." -ForegroundColor Yellow
}

Write-Host "[studio] Starting frontend (port $FrontendPort)..." -ForegroundColor Cyan
Write-Host "[studio] Studio: http://localhost:$FrontendPort/apps" -ForegroundColor Yellow
Write-Host "[studio] Press Ctrl+C here to stop the frontend." -ForegroundColor DarkGray
Write-Host ""

$frontendParams = @{ Port = $FrontendPort }
if ($ForceStop) { $frontendParams['ForceStop'] = $true }
if ($PlatformPath) { $frontendParams['PlatformPath'] = $PlatformPath }
if ($AppWorkspacePath) { $frontendParams['AppWorkspacePath'] = $AppWorkspacePath }
& "$ScriptDir/run-frontend.ps1" @frontendParams
