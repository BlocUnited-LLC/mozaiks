<#
.SYNOPSIS
  Start Mozaiks frontend (Vite dev server).
#>

param(
  [int]$Port = 3000,
  [string]$BindHost = "0.0.0.0",
  [string]$PlatformPath = "",
  [string]$AppWorkspacePath = "",
  [switch]$ForceStop
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

. "$PSScriptRoot/dev-path-selection.ps1"

$appSelection = Set-DevAppSelection -RepoRoot $RepoRoot -SurfaceName "frontend" -PlatformPath $PlatformPath -AppWorkspacePath $AppWorkspacePath
Write-Host $appSelection.Message -ForegroundColor DarkCyan
if ($appSelection.Kind -eq "default") {
  Write-Host "[frontend] No active app workspace is selected. @platform/extensions will resolve to factory_app/app/ui." -ForegroundColor Yellow
  Write-Host "[frontend] If the backend is serving a generated app, restart with -AppWorkspacePath <workspace> to avoid missing route components." -ForegroundColor Yellow
} else {
  Write-Host "[frontend] @platform/extensions workspace: $($appSelection.Value)" -ForegroundColor DarkCyan
}

function Get-ListeningProcessInfo {
  param([int]$LocalPort)

  $procIds = @()
  try {
    $procIds = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    $lines = netstat -ano | Select-String ":$LocalPort\s+.*LISTENING"
    $procIds = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
  }

  $results = @()
  foreach ($procId in $procIds) {
    if (-not $procId -or $procId -eq 0) { continue }
    try {
      $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId"
      $results += [PSCustomObject]@{
        ProcessId   = [int]$procId
        Name        = $proc.Name
        CommandLine = $proc.CommandLine
      }
    } catch {
    }
  }
  return $results
}

function Confirm-PortAvailable {
  param(
    [int]$LocalPort,
    [switch]$KillExisting
  )

  $listeners = Get-ListeningProcessInfo -LocalPort $LocalPort
  if (-not $listeners -or $listeners.Count -eq 0) {
    return
  }

  Write-Host "[frontend] Port $LocalPort is already in use by:" -ForegroundColor Yellow
  $listeners | ForEach-Object {
    Write-Host ("  PID {0} [{1}] {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) -ForegroundColor DarkYellow
  }

  if ($KillExisting) {
    Write-Host "[frontend] ForceStop enabled - terminating existing listeners on port $LocalPort..." -ForegroundColor Yellow
    $listeners | ForEach-Object {
      try {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
        Write-Host ("  Stopped PID {0}" -f $_.ProcessId) -ForegroundColor Green
      } catch {
        Write-Host ("  Failed to stop PID {0}: {1}" -f $_.ProcessId, $_.Exception.Message) -ForegroundColor Red
        throw
      }
    }
    Start-Sleep -Milliseconds 350
    return
  }

  Write-Host "[frontend] Aborting startup to avoid mixed services on the same port." -ForegroundColor Red
  Write-Host "[frontend] Tip: rerun with -ForceStop to auto-kill listeners, or stop them manually." -ForegroundColor Yellow
  throw "Port $LocalPort is busy."
}

Confirm-PortAvailable -LocalPort $Port -KillExisting:$ForceStop

$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) {
  Write-Host "[frontend] npm was not found on PATH." -ForegroundColor Red
  Write-Host "[frontend] Install Node.js 18+ and rerun this command." -ForegroundColor Yellow
  throw "npm is required to start the frontend."
}

$webShellRoot = Join-Path $RepoRoot "web_shell"
$packageJson = Join-Path $webShellRoot "package.json"
$nodeModules = Join-Path $webShellRoot "node_modules"
if (-not (Test-Path $packageJson)) {
  throw "web_shell/package.json was not found."
}
if (-not (Test-Path $nodeModules)) {
  Write-Host "[frontend] Frontend dependencies are not installed." -ForegroundColor Red
  Write-Host "[frontend] Run: npm --prefix web_shell ci" -ForegroundColor Yellow
  throw "web_shell/node_modules is missing."
}

Write-Host "[frontend] Starting Vite on $BindHost`:$Port (strict port)..." -ForegroundColor Cyan
Write-Host "[frontend] Command: npm --prefix web_shell run dev -- --host $BindHost --port $Port --strictPort" -ForegroundColor DarkGray
Write-Host "[frontend] Open: http://localhost:$Port" -ForegroundColor Yellow

npm --prefix web_shell run dev -- --host $BindHost --port $Port --strictPort
