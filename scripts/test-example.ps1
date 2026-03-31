<#
.SYNOPSIS
  Start the local Mozaiks example for quick testing.

.DESCRIPTION
  Starts the example frontend in a separate PowerShell window and runs the
  local backend in the current terminal. The backend script brings up MongoDB
  and Keycloak by default so the example app can authenticate successfully.
#>

param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000,
  [switch]$NoFrontend,
  [switch]$ForceStop
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Get-ShellExecutable {
  $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
  if ($pwsh) {
    return $pwsh.Source
  }

  $powershell = Get-Command powershell -ErrorAction SilentlyContinue
  if ($powershell) {
    return $powershell.Source
  }

  throw "No PowerShell executable found to launch the frontend window."
}

$envFile = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envFile)) {
  Write-Host "[test-example] .env not found. Copying .env.example -> .env" -ForegroundColor Yellow
  Copy-Item (Join-Path $RepoRoot ".env.example") $envFile
  Write-Host "[test-example] Set OPENAI_API_KEY in .env before running a real AI workflow." -ForegroundColor Yellow
}

Write-Host "[test-example] Starting Mozaiks example..." -ForegroundColor Cyan
Write-Host "[test-example] Backend: http://localhost:$BackendPort" -ForegroundColor DarkGray
Write-Host "[test-example] Frontend: http://localhost:$FrontendPort" -ForegroundColor DarkGray
Write-Host "[test-example] Login: dev / dev" -ForegroundColor DarkGray

if (-not $NoFrontend) {
  $shell = Get-ShellExecutable
  $frontendScript = Join-Path $PSScriptRoot "run-frontend.ps1"
  Write-Host "[test-example] Launching frontend in a separate window..." -ForegroundColor Cyan
  Start-Process -FilePath $shell -ArgumentList @(
    "-NoExit",
    "-File",
    $frontendScript,
    "-Port",
    $FrontendPort
  ) -WorkingDirectory $RepoRoot
}

& "$PSScriptRoot/run-backend.ps1" -Port $BackendPort -InfraProfile example -ForceStop:$ForceStop