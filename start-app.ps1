param(
  [switch]$NoBuild = $false,
  [switch]$CleanBuild = $false,
  [switch]$PullBase = $false
)

$ErrorActionPreference = "Stop"

# Resolve repository root regardless of current working directory
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PreviousLocation = Get-Location
Set-Location $ScriptRoot

# Ensure UTF-8 output (compatible with PowerShell 5.1)
chcp 65001 | Out-Null
$utf8 = [System.Text.Encoding]::UTF8
$OutputEncoding = [Console]::OutputEncoding = $utf8

# Enable BuildKit (force on if unset or set to 0)
if (-not $env:DOCKER_BUILDKIT -or $env:DOCKER_BUILDKIT -ne "1") { $env:DOCKER_BUILDKIT = "1" }

$composeFile = Join-Path $ScriptRoot "infra/compose/docker-compose.yml"
if (-not (Test-Path $composeFile)) {
  Write-Host "Compose file not found at $composeFile" -ForegroundColor Red
  Set-Location $PreviousLocation
  exit 1
}

# Emoji/icons (constructed safely so the file avoids direct surrogate encoding issues)
$rocket = "$( [char]0xD83D )$( [char]0xDE80 )"   # 🚀
$cross = [char]0x274C                              # ❌
$check = [char]0x2705                              # ✅
$skip = [char]0x23ED                               # ⏭
$globe = "$( [char]0xD83C )$( [char]0xDF10 )"     # 🌐
$clip = "$( [char]0xD83D )$( [char]0xDCCB )"      # 📋

# Azure credential validation (avoid literal emojis for PS 5.1 safety)
Write-Host "Checking Azure credentials..." -ForegroundColor Yellow
if ($env:AZURE_CLIENT_SECRET -and ($env:AZURE_CLIENT_SECRET -like 'https://*')) {
  Write-Host ("$cross AZURE_CLIENT_SECRET looks like a URL - use the secret VALUE") -ForegroundColor Red
  exit 1
}

if ($env:AZURE_CLIENT_ID -and $env:AZURE_TENANT_ID -and $env:AZURE_CLIENT_SECRET) {
  Write-Host ("$check Complete Azure credentials detected") -ForegroundColor Green
} elseif ($env:AZURE_CLIENT_ID -or $env:AZURE_TENANT_ID -or $env:AZURE_CLIENT_SECRET) {
  Write-Host ("$cross Partial Azure credentials - may cause issues") -ForegroundColor Yellow
} else {
  Write-Host "Using .env file for Azure credentials" -ForegroundColor Gray
}

Write-Host ("$rocket Starting MozaiksAI...") -ForegroundColor Green

if (-not $NoBuild) {
  if ($CleanBuild) {
    Write-Host "Clean build requested: pruning Docker build cache..." -ForegroundColor Yellow
    docker builder prune -af
    if ($LASTEXITCODE -ne 0) { Write-Host ("$cross Failed to prune build cache") -ForegroundColor Red; Set-Location $PreviousLocation; exit 1 }
  }
  Write-Host "Building app image..." -ForegroundColor Yellow
  $dockerfile = Join-Path $ScriptRoot "infra/docker/Dockerfile"
  if (-not (Test-Path $dockerfile)) {
    Write-Host ("$cross Dockerfile not found at $dockerfile") -ForegroundColor Red
    Set-Location $PreviousLocation
    exit 1
  }
  $pullArg = if ($PullBase) { "--pull" } else { "" }
  if ($PullBase) { Write-Host "Refreshing base images (--pull) for docker build" -ForegroundColor Yellow }
  docker build $pullArg -f $dockerfile -t mozaiksai-app:latest $ScriptRoot
  if ($LASTEXITCODE -ne 0) { Write-Host ("$cross Failed to build image") -ForegroundColor Red; exit 1 }
  Write-Host ("$check Image built successfully") -ForegroundColor Green
} else {
  Write-Host ("$skip Skipping build (NoBuild)") -ForegroundColor Yellow
}

Write-Host "Starting services..." -ForegroundColor Yellow
docker compose -f $composeFile up -d
if ($LASTEXITCODE -ne 0) {
  Write-Host "Failed to start services" -ForegroundColor Red
  docker compose -f $composeFile logs --no-color --tail=200
  exit 1
}

Write-Host "Services started successfully!" -ForegroundColor Green
Write-Host ("$globe App available at: http://localhost:8000") -ForegroundColor Cyan

Write-Host ("`n$clip Service Status:") -ForegroundColor Yellow
docker compose -f $composeFile ps

# Return to previous directory before exit
Set-Location $PreviousLocation
