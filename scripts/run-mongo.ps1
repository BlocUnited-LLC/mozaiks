<#
.SYNOPSIS
  Start local MongoDB for Mozaiks via docker compose.
#>

param(
  [string]$ComposeFile = "infra/compose/docker-compose.yml"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[mongo] Starting MongoDB via docker compose..." -ForegroundColor Cyan
& "$PSScriptRoot/run-infra.ps1" -Profile mongo -ComposeFile $ComposeFile

Write-Host "[mongo] Done. Mongo should be available on localhost:27017" -ForegroundColor Green
