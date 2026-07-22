<#
.SYNOPSIS
  Start local infrastructure for Mozaiks via docker compose.

.DESCRIPTION
  The example app expects MongoDB plus Keycloak auth services.
  Use the default "example" profile for first-run testing.
#>

param(
  [ValidateSet("example", "mongo")]
  [string]$Profile = "example",
  [string]$ComposeFile = "infra/compose/docker-compose.yml"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$services = switch ($Profile) {
  "example" { @("mongo", "keycloak-db", "keycloak") }
  "mongo" { @("mongo") }
}

Write-Host ("[infra] Starting profile '{0}' via docker compose..." -f $Profile) -ForegroundColor Cyan
Write-Host ("[infra] Services: {0}" -f ($services -join ", ")) -ForegroundColor DarkGray

docker compose -f $ComposeFile up -d @services
$composeExitCode = $LASTEXITCODE
if ($composeExitCode -ne 0) {
  Write-Host ("[infra] Docker Compose failed with exit code {0}." -f $composeExitCode) -ForegroundColor Red
  Write-Host "[infra] Start Docker Desktop and rerun this command." -ForegroundColor Yellow
  Write-Host "[infra] Use -SkipInfra only when MongoDB is already running on localhost:27017." -ForegroundColor Yellow
  exit $composeExitCode
}

if ($Profile -eq "example") {
  Write-Host "[infra] Example infra started. Mongo: localhost:27017, Keycloak: http://localhost:8080" -ForegroundColor Green
} else {
  Write-Host "[infra] Mongo should be available on localhost:27017" -ForegroundColor Green
}
