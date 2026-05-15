<#
.SYNOPSIS
  Start Mozaiks backend (uvicorn), optionally ensuring local infra is running first.

.DESCRIPTION
  Local development runs clear previous-run files on startup by default:
    - clears prior files under logs/logs/*
    - clears prior files under logs/agent_outputs/*
    - clears prior files under logs/workflow_converter/*

  After startup, the current run writes fresh logs and artifacts normally.
  Use -KeepLogsBetweenRuns and/or -KeepRuntimeArtifactsBetweenRuns to preserve
  previous-run files when starting the next run.
#>

param(
  [int]$Port = 8000,
  [switch]$SkipInfra,
  [ValidateSet("example", "mongo")]
  [string]$InfraProfile = "example",
  [string]$PlatformPath = "",
  [string]$AppWorkspacePath = "",
  [switch]$ForceStop,
  [switch]$CleanRuntimeArtifactsOnStart,
  [switch]$CleanRuntimeArtifactsOnExit,
  [switch]$KeepLogsBetweenRuns,
  [switch]$KeepRuntimeArtifactsBetweenRuns
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

. "$PSScriptRoot/dev-path-selection.ps1"

$appSelection = Set-DevAppSelection -RepoRoot $RepoRoot -SurfaceName "backend" -PlatformPath $PlatformPath -AppWorkspacePath $AppWorkspacePath
Write-Host $appSelection.Message -ForegroundColor DarkCyan

function Get-ListeningProcessInfo {
  param([int]$LocalPort)

  $procIds = @()
  try {
    $procIds = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    # Fallback for environments where Get-NetTCPConnection may be unavailable
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
      # Ignore races where process exits between detection and lookup
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

  Write-Host "[backend] Port $LocalPort is already in use by:" -ForegroundColor Yellow
  $listeners | ForEach-Object {
    Write-Host ("  PID {0} [{1}] {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) -ForegroundColor DarkYellow
  }

  if ($KillExisting) {
    Write-Host "[backend] ForceStop enabled - terminating existing listeners on port $LocalPort..." -ForegroundColor Yellow
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

  Write-Host "[backend] Aborting startup to avoid mixed services on the same port." -ForegroundColor Red
  Write-Host "[backend] Tip: rerun with -ForceStop to auto-kill listeners, or stop them manually." -ForegroundColor Yellow
  throw "Port $LocalPort is busy."
}

if (-not $SkipInfra) {
  Write-Host ("[backend] Ensuring '{0}' infra is running..." -f $InfraProfile) -ForegroundColor Cyan
  & "$PSScriptRoot/run-infra.ps1" -Profile $InfraProfile
}

Confirm-PortAvailable -LocalPort $Port -KillExisting:$ForceStop

$cleanLogsOnStart = -not $KeepLogsBetweenRuns
$cleanRuntimeArtifactsNow = $CleanRuntimeArtifactsOnStart -or (-not $KeepRuntimeArtifactsBetweenRuns)

if ($cleanLogsOnStart -or $cleanRuntimeArtifactsNow) {
  $cleanupArgs = @("-Quiet")
  $cleanupTargets = @()

  if ($cleanLogsOnStart) {
    $cleanupArgs += "-IncludeMainLogs"
    $cleanupTargets += "main logs"
  }
  if ($cleanRuntimeArtifactsNow) {
    $cleanupTargets += "runtime artifacts"
  }

  Write-Host ("[backend] Cleaning {0} before startup..." -f ($cleanupTargets -join " + ")) -ForegroundColor Cyan
  & "$PSScriptRoot/clean-runtime-artifacts.ps1" @cleanupArgs
}

$venvPython = Join-Path $RepoRoot ".venv/Scripts/python.exe"
$pythonCmd = if (Test-Path $venvPython) { $venvPython } else { "python" }

Write-Host "[backend] Starting uvicorn on port $Port..." -ForegroundColor Cyan
Write-Host "[backend] Command: $pythonCmd -m uvicorn mozaiksai.hosts.studio:app --host 0.0.0.0 --port $Port" -ForegroundColor DarkGray

$backendExitCode = 0
try {
  & $pythonCmd -m uvicorn mozaiksai.hosts.studio:app --host 0.0.0.0 --port $Port
  $backendExitCode = $LASTEXITCODE
}
finally {
  if ($CleanRuntimeArtifactsOnExit) {
    Write-Host "[backend] Cleaning runtime artifacts after shutdown..." -ForegroundColor Cyan
    & "$PSScriptRoot/clean-runtime-artifacts.ps1" -Quiet
  }
}

if ($backendExitCode -ne 0) {
  exit $backendExitCode
}
