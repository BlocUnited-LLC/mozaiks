<#
.SYNOPSIS
  Start the full Mozaiks Studio stack for this app workspace.
#>

param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000,
  [string]$WorkspacePath = "",
  [switch]$ForceStop,
  [switch]$NoBrowser,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if ($WorkspacePath) {
  $Workspace = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $WorkspacePath))
} else {
  $Workspace = $RepoRoot
}

function Resolve-Mozaiks {
  $venvMozaiks = Join-Path $RepoRoot ".venv\Scripts\mozaiks.exe"
  if (Test-Path -LiteralPath $venvMozaiks) {
    return $venvMozaiks
  }
  return "mozaiks"
}

function Stop-Listeners {
  param([int[]]$Ports)

  foreach ($port in $Ports) {
    $procIds = @()
    try {
      $procIds = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique
    } catch {
      $lines = netstat -ano | Select-String ":$port\s+.*LISTENING"
      $procIds = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
    }

    foreach ($procId in $procIds) {
      if (-not $procId -or $procId -eq 0) { continue }
      Write-Host "[studio] ForceStop: stopping PID $procId on port $port" -ForegroundColor Yellow
      Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop
    }
  }
}

if (-not (Test-Path -LiteralPath (Join-Path $Workspace "app\app.json"))) {
  throw "No Mozaiks app bundle found at $Workspace. Expected app\app.json."
}

# Keep generated apps on packaged resources by default.
Remove-Item Env:MOZAIKS_FACTORY_APP_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_WEB_SHELL_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_CHAT_UI_PATH -ErrorAction SilentlyContinue

$mozaiksCmd = Resolve-Mozaiks
$argsList = @(
  "studio",
  "--dir",
  $Workspace,
  "--backend-port",
  [string]$BackendPort,
  "--frontend-port",
  [string]$FrontendPort
)
if ($NoBrowser) {
  $argsList += "--no-browser"
} else {
  $argsList += "--open"
}

Write-Host "[studio] Workspace: $Workspace" -ForegroundColor DarkCyan
Write-Host "[studio] Command: $mozaiksCmd $($argsList -join ' ')" -ForegroundColor Cyan

if ($DryRun) {
  return
}

if ($ForceStop) {
  Stop-Listeners -Ports @($BackendPort, $FrontendPort)
}

& $mozaiksCmd @argsList
