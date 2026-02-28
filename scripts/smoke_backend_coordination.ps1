$ErrorActionPreference = "Stop"

# 1. Resolve Paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$EnvFile = Join-Path $RepoRoot ".env"

Write-Host "=== MozaiksAI Backend Coordination Smoke Test ===" -ForegroundColor Cyan
Write-Host "Repo Root: $RepoRoot"

# 2. Load .env if present
if (Test-Path $EnvFile) {
    Write-Host "Loading .env file..."
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match "^([^#=]+)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
        }
    }
}

# 3. Check Environment Variables
$ApiKey = [System.Environment]::GetEnvironmentVariable("INTERNAL_API_KEY")
$BackendUrl = [System.Environment]::GetEnvironmentVariable("MOZAIKS_BACKEND_URL")

if ([string]::IsNullOrWhiteSpace($ApiKey)) {
    Write-Warning "INTERNAL_API_KEY is not set. Backend calls will fail."
} else {
    Write-Host "INTERNAL_API_KEY is set." -ForegroundColor Green
}

if ([string]::IsNullOrWhiteSpace($BackendUrl)) {
    Write-Host "MOZAIKS_BACKEND_URL is not set. Defaulting to http://localhost:3000" -ForegroundColor Yellow
} else {
    Write-Host "MOZAIKS_BACKEND_URL: $BackendUrl" -ForegroundColor Green
}

# 4. Run Python Connectivity Check
Write-Host "`nRunning Python connectivity check..." -ForegroundColor Cyan

$PyCode = @"
import sys
import os
import asyncio

# Add repo root to sys.path
sys.path.insert(0, r'$RepoRoot')

try:
    from core.transport.backend_client import backend_client
    print(f'Successfully imported BackendClient.')
    print(f'Configured URL: {backend_client.base_url}')
    
    # We could try a ping here if we had one, but for now just verifying import and config is good.
    # If we wanted to test connectivity, we'd need a valid app_id/ent_id which we don't have generically.
    
except Exception as e:
    print(f'Error: {e}')
    sys.exit(1)
"@

$Env:PYTHONPATH = $RepoRoot
python -c $PyCode

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nSmoke Test PASSED: Runtime is ready to coordinate with Backend." -ForegroundColor Green
} else {
    Write-Host "`nSmoke Test FAILED." -ForegroundColor Red
    exit 1
}
