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

function Assert-DockerAvailable {
  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if (-not $docker) {
    Write-Host "[infra] Docker CLI was not found on PATH." -ForegroundColor Red
    Write-Host "[infra] Install Docker Desktop, or rerun backend startup with -SkipInfra when MongoDB is already running." -ForegroundColor Yellow
    throw "Docker CLI is required for run-infra.ps1."
  }

  & docker info *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[infra] Docker Desktop is not reachable." -ForegroundColor Red
    Write-Host "[infra] Start Docker Desktop and wait until it says the engine is running, then rerun this command." -ForegroundColor Yellow
    Write-Host "[infra] Use -SkipInfra only when MongoDB is already running on localhost:27017 or MONGO_URI points elsewhere." -ForegroundColor Yellow
    throw "Docker daemon is not reachable."
  }

  & docker compose version *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[infra] Docker Compose v2 is not available through 'docker compose'." -ForegroundColor Red
    Write-Host "[infra] Update Docker Desktop or install the Compose v2 plugin." -ForegroundColor Yellow
    throw "Docker Compose v2 is required."
  }
}

if (-not (Test-Path $ComposeFile)) {
  throw "Compose file not found: $ComposeFile"
}

$services = switch ($Profile) {
  "example" { @("mongo", "keycloak-db", "keycloak") }
  "mongo" { @("mongo") }
}

Assert-DockerAvailable

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
