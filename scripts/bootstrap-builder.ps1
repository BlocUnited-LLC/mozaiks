<#
.SYNOPSIS
    Bootstrap the Mozaiks builder path from a fresh repo checkout.

.DESCRIPTION
    Creates .venv when missing, installs the local editable package, and then
    launches the minimal Studio-first builder flow through:

      python -m mozaiks_cli.main quickstart

    This is the recommended first-run path for users who want to build apps
    through Studio and the shared factory_app workflows.
#>

param(
    [string]$Workspace = ".\my-first-mozaiks-app",
    [ValidateSet("engine", "chat", "integrated", "full")]
    [string]$Preset = "chat",
    [string]$Name,
    [ValidateSet("greenfield_app", "brownfield_app")]
    [string]$Journey,
    [string]$Goal,
    [ValidateSet("anthropic", "openai", "local", "other")]
    [string]$Provider,
    [string]$Model,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 3000,
    [switch]$NoBrowser,
    [switch]$NoLaunch,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Get-BootstrapPython {
    if (Test-Path $VenvPython) {
        return $VenvPython
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $python3 = Get-Command python3 -ErrorAction SilentlyContinue
    if ($python3) {
        return $python3.Source
    }

    throw "Python 3.11+ is required. Install Python and rerun this script."
}

Push-Location $RepoRoot
try {
    Write-Host "Mozaiks builder bootstrap" -ForegroundColor Cyan
    Write-Host "Repo:      $RepoRoot" -ForegroundColor DarkGray
    Write-Host "Workspace: $Workspace" -ForegroundColor DarkGray
    Write-Host ""

    $bootstrapPython = Get-BootstrapPython

    if (-not (Test-Path $VenvPython)) {
        Write-Host "[1/3] Creating .venv..." -ForegroundColor Yellow
        & $bootstrapPython -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create .venv."
        }
    } else {
        Write-Host "[1/3] Reusing existing .venv" -ForegroundColor Yellow
    }

    if (-not $SkipInstall) {
        Write-Host "[2/3] Installing Mozaiks in editable mode..." -ForegroundColor Yellow
        & $VenvPython -m pip install -e .
        if ($LASTEXITCODE -ne 0) {
            throw "Editable install failed."
        }
    } else {
        Write-Host "[2/3] Skipping editable install" -ForegroundColor Yellow
    }

    $quickstartArgs = @(
        "-m",
        "mozaiks_cli.main",
        "quickstart",
        "--dir",
        $Workspace,
        "--preset",
        $Preset,
        "--backend-port",
        $BackendPort,
        "--frontend-port",
        $FrontendPort
    )

    if ($Name) {
        $quickstartArgs += @("--name", $Name)
    }
    if ($Journey) {
        $quickstartArgs += @("--journey", $Journey)
    }
    if ($Goal) {
        $quickstartArgs += @("--goal", $Goal)
    }
    if ($Provider) {
        $quickstartArgs += @("--provider", $Provider)
    }
    if ($Model) {
        $quickstartArgs += @("--model", $Model)
    }
    if ($NoBrowser) {
        $quickstartArgs += "--no-browser"
    }

    if ($NoLaunch) {
        Write-Host "[3/3] Bootstrap complete; Studio launch skipped" -ForegroundColor Green
        Write-Host ""
        Write-Host "Next command:" -ForegroundColor Cyan
        Write-Host "  .\.venv\Scripts\python.exe $($quickstartArgs -join ' ')" -ForegroundColor Gray
        exit 0
    }

    Write-Host "[3/3] Launching Mozaiks Studio..." -ForegroundColor Yellow
    & $VenvPython @quickstartArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Quickstart failed."
    }
}
finally {
    Pop-Location
}
