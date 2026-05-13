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

Write-Host "[frontend] Starting Vite on $BindHost`:$Port (strict port)..." -ForegroundColor Cyan
Write-Host "[frontend] Command: npm --prefix web_shell run dev -- --host $BindHost --port $Port --strictPort" -ForegroundColor DarkGray
Write-Host "[frontend] Open: http://localhost:$Port" -ForegroundColor Yellow

npm --prefix web_shell run dev -- --host $BindHost --port $Port --strictPort
