<#
start-dev.ps1

Helper to start the project in two modes:
 - docker (default): bring up docker compose (app + infra), tail app logs, and optionally start frontend.
 - local: start infra services in docker (mongo, etc.), ensure no app container is occupying port 8000, then run the local app from .venv and optionally start frontend.

Usage examples (PowerShell):
  # Docker-managed (recommended)
  .\start-dev.ps1 -Mode docker -StartFrontend

  # Local backend with Docker infra
  .\start-dev.ps1 -Mode local -StartFrontend

This script is intentionally small and conservative: it will not overwrite existing files and will try to avoid port collisions.
#>

param(
    [ValidateSet('docker','local')]
    [string]$Mode = 'docker',
    [switch]$StartFrontend,
    [switch]$TailLogs,
    [switch]$TailInPlace,
    [switch]$FreshRun,
    [switch]$AutoCapture,
    [switch]$StopOnly,
    [switch]$CleanLogsBefore,
    [switch]$Stop,
    [switch]$AutoCaptureAfter,
    [int]$AppPort = 8000
)

function Test-PortInUse {
    param([int]$Port)
    $netstat = netstat -aon | Select-String ":$Port\s"
    return -not [string]::IsNullOrEmpty($netstat)
}

function Get-ContainerByName($name) {
    $c = docker ps --filter "name=$name" --format "{{.ID}} {{.Names}}" 2>$null
    return $c
}

# Resolve script and repo paths early so functions can rely on them
$ScriptRoot = $PSScriptRoot
if (-not $ScriptRoot) {
    try {
        $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    } catch {
        # fallback to current working directory when invoked from other contexts
        $ScriptRoot = (Get-Location).ProviderPath
    }
}
$RepoRoot = $ScriptRoot

# Find a shell executable to spawn background terminals (prefer pwsh, fall back to powershell)
function Get-ShellExe {
    $pwsh = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwsh) { return $pwsh.Source }
    $ps = Get-Command powershell -ErrorAction SilentlyContinue
    if ($ps) { return $ps.Source }
    return $null
}

# Detect if we're running inside VS Code integrated terminal by checking environment vars
function Is-Running-In-VSCode {
    return -not [string]::IsNullOrEmpty($env:TERM_PROGRAM) -and $env:TERM_PROGRAM -eq 'vscode'
}

# Start frontend helper: when inside VS Code try to run in the same terminal or -NoNewWindow;
# otherwise fall back to starting a shell process (legacy behavior).
function Start-FrontendProcess {
    param(
        [switch]$Background
    )
    $cwd = Join-Path -Path $ScriptRoot -ChildPath 'ChatUI'
    if (Is-Running-In-VSCode) {
        if ($Background) {
            Write-Host "VSCode detected: starting frontend in background using Start-Process -NoNewWindow..." -ForegroundColor Green
            $shellExe = Get-ShellExe
            if ($shellExe) {
                # Use -NoNewWindow when possible to avoid opening an external window; start in VS Code terminal.
                Start-Process -FilePath $shellExe -ArgumentList "-NoExit","-Command","npm start" -WorkingDirectory $cwd -NoNewWindow
                return
            }
        }
        # If not backgrounding, run in current terminal (blocking)
        Write-Host "VSCode detected: running frontend in this terminal (blocking)" -ForegroundColor Green
        Push-Location $cwd
        npm start
        Pop-Location
        return
    } else {
        # Not running in VS Code: preserve legacy behavior (spawn external shell window)
        $shellExe = Get-ShellExe
        if ($shellExe) {
            if ($Background) {
                Start-Process -FilePath $shellExe -ArgumentList "-NoExit","-Command","npm start" -WorkingDirectory $cwd
            } else {
                # Run in current shell if possible
                Push-Location $cwd
                npm start
                Pop-Location
            }
        } else {
            Write-Host "No suitable shell found to start frontend. Please run manually: cd ChatUI; npm start" -ForegroundColor Red
        }
    }
}

Write-Host "start-dev.ps1: mode=$Mode startFrontend=$StartFrontend appPort=$AppPort" -ForegroundColor Cyan

# ...existing code...

if ($Mode -eq 'docker') {
    Write-Host "Preparing docker compose (app + infra)..." -ForegroundColor Green
        if ($StopOnly) {
            Write-Host "StopOnly requested: bringing docker compose down without cleaning logs or running captures..." -ForegroundColor Yellow
            docker compose -f infra/compose/docker-compose.yml down
            exit 0
        }
    # FreshRun: clean logs before starting
    if ($FreshRun) {
        Write-Host "FreshRun requested: cleaning saved logs..." -ForegroundColor Yellow
        Write-Host "💡 Tip: Use '.\scripts\cleanse.ps1 -Full' for complete cleanup (DB + Docker + caches)" -ForegroundColor Cyan
        # Remove saved capture logs (now stored under logs\logs)
        if ($CleanLogsBefore -or $true) {
            $logsDir = Join-Path -Path $RepoRoot -ChildPath 'logs\logs'
            if (Test-Path $logsDir) {
                Write-Host "Cleaning old saved logs in $logsDir..." -ForegroundColor Yellow
                Get-ChildItem -Path $logsDir -File -Force | Remove-Item -Force -ErrorAction SilentlyContinue
            }
        }
    }
    # Optional: clean old saved logs before starting fresh
    if ($CleanLogsBefore) {
        $logsDir = Join-Path -Path $RepoRoot -ChildPath 'logs\logs'
        if (Test-Path $logsDir) {
            Write-Host "Cleaning old saved logs in $logsDir..." -ForegroundColor Yellow
            Get-ChildItem -Path $logsDir -File -Force | Remove-Item -Force -ErrorAction SilentlyContinue
        }
    }

    if ($Stop) {
        # Stop mode: optionally capture the run that just happened, then bring containers down
        if ($AutoCaptureAfter) {
            $capScript = Join-Path -Path $ScriptRoot -ChildPath 'scripts\capture-logs.ps1'
            if (Test-Path $capScript) {
                Write-Host "Auto-capturing logs for current run before shutdown..." -ForegroundColor Yellow
                & $capScript -SinceMinutes 120 -IncludeInfra
            } else {
                Write-Host "AutoCaptureAfter requested but scripts\capture-logs.ps1 not found; skipping capture." -ForegroundColor Yellow
            }
        }
        Write-Host "Bringing down docker compose (stop) ..." -ForegroundColor Green
        docker compose -f infra/compose/docker-compose.yml down
        exit 0
    }

    # Normal start: bring down any existing compose then up
    docker compose -f infra/compose/docker-compose.yml down
    docker compose -f infra/compose/docker-compose.yml up -d --remove-orphans
    Write-Host "Docker compose is up." -ForegroundColor Yellow
    if ($TailInPlace) {
        Write-Host "Tailing mozaiksai-app logs in this terminal (press Ctrl+C to stop)..." -ForegroundColor Yellow
        # If StartFrontend was requested alongside TailInPlace, start frontend in the background
        if ($StartFrontend) {
            Write-Host "Starting frontend (ChatUI) in the background so logs keep streaming here..." -ForegroundColor Green
            Start-FrontendProcess -Background
        }
        # Run the tail in-place (this will block until stopped)
        docker logs -f --timestamps mozaiksai-app
    } elseif ($TailLogs) {
        Write-Host "Spawning a background terminal to tail mozaiksai-app logs..." -ForegroundColor Yellow
        try {
            $shellExe = Get-ShellExe
            if ($shellExe) {
                Start-Process -FilePath $shellExe -ArgumentList "-NoExit","-Command","docker logs -f --timestamps mozaiksai-app"
            } else {
                throw "No shell executable found"
            }
        } catch {
            Write-Host "Could not spawn pwsh; falling back to plain docker logs in background window." -ForegroundColor Yellow
            $shellExe = Get-ShellExe
            if ($shellExe) {
                Start-Process -FilePath $shellExe -ArgumentList "-NoExit","-Command","docker logs -f mozaiksai-app"
            } else {
                Write-Host "No shell available to spawn background logs. Please run 'docker logs -f --timestamps mozaiksai-app' in another terminal." -ForegroundColor Red
            }
        }
    } else {
        Write-Host "Tail logs in another terminal with: docker logs -f mozaiksai-app" -ForegroundColor Yellow
    }
    if ($StartFrontend -and -not $TailInPlace) {
            Write-Host "Starting frontend (ChatUI)..." -ForegroundColor Green
            # Use helper to start frontend; when running inside VS Code this will avoid creating an external window.
            Start-FrontendProcess
        }
    exit 0
}

# Local mode: ensure infra running, stop app container if present, run local app using .venv
Write-Host "Local-dev mode: starting infra-only and running app locally" -ForegroundColor Green

# Start infra only (mongo). This avoids the app container binding port 8000.
docker compose -f infra/compose/docker-compose.yml up -d mongo
# If an app container is running, stop it to free the port
$appContainer = Get-ContainerByName mozaiksai-app
if ($appContainer) {
    Write-Host "Stopping app container to free local port..." -ForegroundColor Yellow
    docker compose -f infra/compose/docker-compose.yml stop mozaiksai-app
}

# Check port
if (Test-PortInUse -Port $AppPort) {
    Write-Host "Port $AppPort appears in use. Please stop the process or choose a different port." -ForegroundColor Red
    Write-Host "To find PID: netstat -aon | findstr ":$AppPort"" -ForegroundColor Yellow
    exit 1
}

Write-Host "Activating virtualenv and starting local backend..." -ForegroundColor Green
if (Test-Path ".venv\Scripts\Activate.ps1") {
    # Use the venv activation script in the current shell
    . .\.venv\Scripts\Activate.ps1
} elseif (Test-Path ".venv\Scripts\activate") {
    # fallback (cmd style)
    . .\.venv\Scripts\activate
} else {
    Write-Host "No .venv found. Please create one or run the app manually." -ForegroundColor Red
    exit 1
}

if (-not $env:AUTH_ENABLED) {
    # Local dev default: disable auth so ChatUI can call APIs without tokens.
    $env:AUTH_ENABLED = "false"
}

Write-Host "Starting local backend on http://localhost:$AppPort (AUTH_ENABLED=$($env:AUTH_ENABLED))" -ForegroundColor Yellow
python run_server.py

if ($StartFrontend) {
    Write-Host "Starting frontend (ChatUI) in a new process..." -ForegroundColor Green
    Start-FrontendProcess -Background
}
