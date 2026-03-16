<#
.SYNOPSIS
  Start local NATS for Mozaiks automation via docker compose.
#>

param(
  [string]$ComposeFile = "infra/compose/docker-compose.yml"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[nats] Starting NATS via docker compose..." -ForegroundColor Cyan
docker compose -f $ComposeFile up -d nats

Write-Host "[nats] Done. NATS should be available on localhost:4222" -ForegroundColor Green