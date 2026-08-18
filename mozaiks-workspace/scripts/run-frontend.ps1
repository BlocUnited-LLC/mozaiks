<#
.SYNOPSIS
  Start the packaged Mozaiks web shell for this app workspace.

.DESCRIPTION
  Resolves web_shell from the installed mozaiks package, then runs the Vite dev
  server with PLATFORM_PATH pointed at this workspace.
#>

param(
  [int]$Port = 3000,
  [string]$BindHost = "0.0.0.0",
  [string]$BackendUrl = "http://localhost:8000",
  [string]$WorkspacePath = "",
  [switch]$ForceStop,
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

  Write-Host "[frontend] Port $LocalPort is already in use by:" -ForegroundColor Yellow
  $listeners | ForEach-Object {
    Write-Host ("  PID {0} [{1}] {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) -ForegroundColor DarkYellow
  }

  if ($KillExisting) {
    Write-Host "[frontend] ForceStop enabled - terminating existing listeners on port $LocalPort..." -ForegroundColor Yellow
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

# Keep generated apps on packaged resources by default.
Remove-Item Env:MOZAIKS_FACTORY_APP_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_WEB_SHELL_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_CHAT_UI_PATH -ErrorAction SilentlyContinue

$webShellRoot = & $pythonCmd -c "from mozaiksai.resources import resolve_web_shell_root; p = resolve_web_shell_root(); print(p or '')"
$webShellRoot = ($webShellRoot | Select-Object -Last 1).Trim()
if (-not $webShellRoot -or -not (Test-Path -LiteralPath (Join-Path $webShellRoot "package.json"))) {
  throw "Could not resolve packaged web_shell. Run: python -m pip install -r requirements.txt"
}

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
  throw "npm is required to start the frontend."
}

$env:MOZAIKS_APP_WORKSPACE_PATH = $Workspace
$env:PLATFORM_PATH = $Workspace
$env:MOZAIKS_HOST = "platform"
$env:VITE_API_URL = $BackendUrl
$env:MOZAIKS_GENERATED_ARTIFACTS_PATH = (Join-Path $Workspace "generated")

Write-Host "[frontend] Workspace: $Workspace" -ForegroundColor DarkCyan
Write-Host "[frontend] Web shell: $webShellRoot" -ForegroundColor DarkCyan
Write-Host "[frontend] Backend URL: $BackendUrl" -ForegroundColor DarkCyan
Write-Host "[frontend] Command: npm --prefix `"$webShellRoot`" run dev -- --host $BindHost --port $Port --strictPort" -ForegroundColor Cyan
Write-Host "[frontend] Open: http://localhost:$Port" -ForegroundColor Yellow

if ($DryRun) {
  return
}

Confirm-PortAvailable -LocalPort $Port -KillExisting:$ForceStop

if (-not (Test-Path -LiteralPath (Join-Path $webShellRoot "node_modules"))) {
  Write-Host "[frontend] Installing packaged web_shell dependencies..." -ForegroundColor Cyan
  npm --prefix $webShellRoot install
}

npm --prefix $webShellRoot run dev -- --host $BindHost --port $Port --strictPort
