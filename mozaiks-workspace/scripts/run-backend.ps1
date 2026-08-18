<#
.SYNOPSIS
  Start the Mozaiks backend for this app workspace.

.DESCRIPTION
  Runs the backend from the installed mozaiks package and points it at this
  workspace.
#>

param(
  [int]$Port = 8000,
  [string]$BindHost = "0.0.0.0",
  [string]$WorkspacePath = "",
  [switch]$ForceStop,
  [switch]$Reload,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if ($WorkspacePath) {
  $Workspace = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $WorkspacePath))
} else {
  $Workspace = $RepoRoot
}

function Resolve-Python {
  $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }
  return "python"
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
        ProcessId = [int]$procId
        Name = $proc.Name
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

  Write-Host "[backend] Port $LocalPort is already in use by:" -ForegroundColor Yellow
  $listeners | ForEach-Object {
    Write-Host ("  PID {0} [{1}] {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) -ForegroundColor DarkYellow
  }

  if ($KillExisting) {
    Write-Host "[backend] ForceStop enabled - terminating existing listeners on port $LocalPort..." -ForegroundColor Yellow
    $listeners | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
      Write-Host ("  Stopped PID {0}" -f $_.ProcessId) -ForegroundColor Green
    }
    Start-Sleep -Milliseconds 350
    return
  }

  throw "Port $LocalPort is busy. Rerun with -ForceStop or choose another -Port."
}

if (-not (Test-Path -LiteralPath (Join-Path $Workspace "app\app.json"))) {
  throw "No Mozaiks app bundle found at $Workspace. Expected app\app.json."
}

$pythonCmd = Resolve-Python
Set-Location $Workspace

# Keep generated apps on packaged resources by default.
Remove-Item Env:MOZAIKS_FACTORY_APP_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_WEB_SHELL_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_CHAT_UI_PATH -ErrorAction SilentlyContinue

$env:MOZAIKS_APP_WORKSPACE_PATH = $Workspace
$env:PLATFORM_PATH = $Workspace
$env:MOZAIKS_HOST = "platform"
$env:MOZAIKS_GENERATED_ARTIFACTS_PATH = (Join-Path $Workspace "generated")

$uvicornArgs = @(
  "-m",
  "uvicorn",
  "mozaiksai.hosts.platform:app",
  "--host",
  $BindHost,
  "--port",
  [string]$Port
)
if ($Reload) {
  $uvicornArgs += "--reload"
}

Write-Host "[backend] Workspace: $Workspace" -ForegroundColor DarkCyan
Write-Host "[backend] Command: $pythonCmd $($uvicornArgs -join ' ')" -ForegroundColor Cyan

if ($DryRun) {
  return
}

Confirm-PortAvailable -LocalPort $Port -KillExisting:$ForceStop
& $pythonCmd @uvicornArgs
