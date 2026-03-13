<#
.SYNOPSIS
  Start Mozaiks backend (uvicorn), optionally ensuring Mongo is running first.
#>

param(
  [int]$Port = 8000,
  [switch]$SkipMongo
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

if (-not $SkipMongo) {
  Write-Host "[backend] Ensuring Mongo is running..." -ForegroundColor Cyan
  & "$PSScriptRoot/run-mongo.ps1"
}

$venvPython = Join-Path $RepoRoot ".venv/Scripts/python.exe"
$pythonCmd = if (Test-Path $venvPython) { $venvPython } else { "python" }

Write-Host "[backend] Starting uvicorn on port $Port..." -ForegroundColor Cyan
Write-Host "[backend] Command: $pythonCmd -m uvicorn shared_app:app --host 0.0.0.0 --port $Port" -ForegroundColor DarkGray

& $pythonCmd -m uvicorn shared_app:app --host 0.0.0.0 --port $Port
