# ==============================================================================
# mozaiksai - Deep Cleanse Script
# Removes all cache, build artifacts, logs, and optionally runtime state
# ==============================================================================

param(
    [switch]$KeepLogs,
    [switch]$KeepDB,
    [switch]$AllowNonLocalMongo,
    [switch]$AllowStandaloneMongoFallback,
    [switch]$Full
)

# SAFETY: Only ever touch mozaiksai database - hardcoded to prevent accidents
$DatabaseName = "mozaiksai"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Stop-MozaiksDevProcesses {
    param(
        [string]$RepoPath
    )

    # Stop only python/node processes that are running from this repo context.
    # This is used by full cleanse to free locked log files.
    $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ProcessId -ne $PID -and
            ($_.Name -match '^(python|python3|node)\.exe$') -and
            $_.CommandLine -and
            $_.CommandLine -like "*$RepoPath*"
        }

    if (-not $candidates) {
        return 0
    }

    $stopped = 0
    foreach ($proc in $candidates) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
            $stopped++
        } catch {
            # best effort
        }
    }

    return $stopped
}

Write-Host " Starting mozaiksai deep cleanse..." -ForegroundColor Cyan

# Python bytecode and __pycache__
Write-Host "`n Cleaning Python bytecode..." -ForegroundColor Yellow
Get-ChildItem -Path "." -Include "*.pyc" -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path "." -Include "__pycache__" -Recurse -Directory -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
Write-Host "    Removed *.pyc and __pycache__ directories" -ForegroundColor Green

# React/Webpack build artifacts and cache
Write-Host "`n  Cleaning React/Webpack artifacts..." -ForegroundColor Yellow
if (Test-Path "ChatUI") {
    Push-Location ChatUI
    Remove-Item -Recurse -Force .cache, node_modules/.cache, build -ErrorAction SilentlyContinue
    Pop-Location
    Write-Host "    Removed ChatUI/.cache, node_modules/.cache, build/" -ForegroundColor Green
}

# Logs (optional)
if (-not $KeepLogs) {
    Write-Host "`n Cleaning logs..." -ForegroundColor Yellow
    if (Test-Path "logs/logs") {
        $logFiles = Get-ChildItem -Path "logs/logs" -Filter "*.log" -ErrorAction SilentlyContinue
        if (-not $logFiles) {
            Write-Host "    No log files found in logs/logs/" -ForegroundColor Gray
        } else {
            $lockedFiles = @()
            $deletedCount = 0

            foreach ($logFile in $logFiles) {
                try {
                    Remove-Item -Path $logFile.FullName -Force -ErrorAction Stop
                    $deletedCount++
                } catch {
                    $lockedFiles += $logFile
                }
            }

            # For full cleanses, stop repo-scoped dev processes and retry locked logs.
            if ($Full -and $lockedFiles.Count -gt 0) {
                Write-Host "    Some logs are locked, stopping repo dev processes and retrying..." -ForegroundColor Yellow
                $stopped = Stop-MozaiksDevProcesses -RepoPath $RepoRoot
                if ($stopped -gt 0) {
                    Write-Host "    Stopped $stopped process(es) holding log handles" -ForegroundColor Green
                    Start-Sleep -Seconds 1
                } else {
                    Write-Host "    No repo python/node processes found to stop" -ForegroundColor Gray
                }

                $remainingLocked = @()
                foreach ($locked in $lockedFiles) {
                    try {
                        Remove-Item -Path $locked.FullName -Force -ErrorAction Stop
                        $deletedCount++
                    } catch {
                        $remainingLocked += $locked
                    }
                }
                $lockedFiles = $remainingLocked
            }

            if ($lockedFiles.Count -gt 0) {
                $names = ($lockedFiles | ForEach-Object { $_.Name }) -join ", "
                Write-Host "    ⚠️  Could not delete locked logs: $names" -ForegroundColor Yellow
                Write-Host "       Close remaining log writers and rerun cleanse to remove them." -ForegroundColor Gray
            } else {
                Write-Host "    Removed logs/logs/*.log ($deletedCount file(s))" -ForegroundColor Green
            }
        }
    }
} else {
    Write-Host "`n Keeping logs (-KeepLogs flag set)" -ForegroundColor Gray
}

# MongoDB collections cleanup (clears documents only, preserves collections/indexes)
if (-not $KeepDB) {
    Write-Host "`n🗄️  Clearing MongoDB collections (documents only)..." -ForegroundColor Yellow
    $mongoStartupAttempted = $false
    $mongoCleanupExitCode = $null
    
    # Helper function to check if MongoDB is reachable
    function Test-MongoConnection {
        $pythonCmd = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
        $checkScript = @"
import sys
try:
    from pymongo import MongoClient
    import os
    from pathlib import Path
    from dotenv import load_dotenv
    # Load .env from repo root (scripts/ is one level down). This prevents false negatives
    # when the script is invoked from a different working directory.
    repo_root = Path(r"$PSScriptRoot").resolve().parent
    env_path = repo_root / '.env'
    if env_path.exists():
        load_dotenv(dotenv_path=str(env_path))
    else:
        load_dotenv()
    uri = os.getenv('MONGO_URI') or os.getenv('MONGODB_URI') or os.getenv('MONGO_URL') or 'mongodb://localhost:27017'
    client = MongoClient(uri, serverSelectionTimeoutMS=2000)
    client.admin.command('ping')
    sys.exit(0)
except:
    sys.exit(1)
"@
        $checkScript | & $pythonCmd - 2>$null
        return $LASTEXITCODE -eq 0
    }

    function Wait-ForMongoConnection {
        param(
            [int]$TimeoutSeconds = 20,
            [int]$PollIntervalSeconds = 2
        )

        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            if (Test-MongoConnection) {
                return $true
            }
            Start-Sleep -Seconds $PollIntervalSeconds
        }

        return Test-MongoConnection
    }
    
    # Try to ensure MongoDB is running
    $mongoRunning = Test-MongoConnection
    if (-not $mongoRunning) {
        $mongoStartupAttempted = $true
        Write-Host "   MongoDB not reachable, attempting to start..." -ForegroundColor Yellow
        
        # Try 1: Docker container named 'mongodb' or 'mongo'
        $dockerAvailable = $null -ne (Get-Command docker -ErrorAction SilentlyContinue)
        if ($dockerAvailable) {
            # Check for existing stopped container
            $existingContainer = docker ps -a --filter "name=^mongodb$" --filter "name=^mongo$" --filter "name=^mozaiksai-mongo$" --format "{{.Names}}" 2>$null | Select-Object -First 1
            if ($existingContainer) {
                Write-Host "   Starting existing Docker container '$existingContainer'..." -ForegroundColor Gray
                $startOut = & docker start $existingContainer 2>&1
                $dockerStartExit = $LASTEXITCODE
                $mongoRunning = Wait-ForMongoConnection
                if (-not $mongoRunning -and $dockerStartExit -ne 0) {
                    Write-Host "   Docker start error: $startOut" -ForegroundColor Gray
                }
            }
            
            # Try 1b: docker compose (handles image-missing after Full nuke)
            if (-not $mongoRunning) {
                 $composeFile = Join-Path -Path $RepoRoot -ChildPath "infra/compose/docker-compose.yml"
                if (Test-Path $composeFile) {
                    Write-Host "   Starting MongoDB via docker compose..." -ForegroundColor Gray
                    $composeOut = & docker compose -f $composeFile up -d mongo 2>&1
                    $composeExit = $LASTEXITCODE
                    $mongoRunning = Wait-ForMongoConnection -TimeoutSeconds 60
                    if (-not $mongoRunning) {
                        if ($composeOut) {
                            Write-Host "   Docker compose output:" -ForegroundColor Gray
                            Write-Host "      $($composeOut -join [Environment]::NewLine + '      ')" -ForegroundColor Gray
                        }
                        if ($composeExit -ne 0) {
                            Write-Host "   Docker compose exited with code $composeExit" -ForegroundColor Gray
                        }
                    }
                }
            }

            # If still not running, optionally create a standalone container.
            # Disabled by default to avoid pulling extra images/creating artifacts
            # during routine cleanses.
            if (-not $mongoRunning -and $AllowStandaloneMongoFallback) {
                Write-Host "   Creating standalone MongoDB Docker container (fallback enabled)..." -ForegroundColor Gray
                $runOut = & docker run -d --name mongodb -p 27017:27017 mongo:latest 2>&1
                $dockerRunExit = $LASTEXITCODE
                $mongoRunning = Wait-ForMongoConnection -TimeoutSeconds 60
                if (-not $mongoRunning -and $dockerRunExit -ne 0) {
                    Write-Host "   Docker run error: $runOut" -ForegroundColor Gray
                }
            } elseif (-not $mongoRunning) {
                Write-Host "   Skipping standalone 'docker run mongo:latest' fallback (default safe mode)." -ForegroundColor Gray
                Write-Host "   Use -AllowStandaloneMongoFallback to enable that behavior." -ForegroundColor Gray
            }
        }
        
        # Try 2: Windows service
        if (-not $mongoRunning) {
            $mongoService = Get-Service -Name "MongoDB" -ErrorAction SilentlyContinue
            if ($mongoService) {
                if ($mongoService.Status -ne 'Running') {
                    Write-Host "   Starting MongoDB Windows service..." -ForegroundColor Gray
                    Start-Service -Name "MongoDB" -ErrorAction SilentlyContinue
                    $mongoRunning = Wait-ForMongoConnection
                }
            }
        }
        
    }
    
    # Now attempt the cleanup - ONLY mozaiksai database
    $clearScript = Join-Path -Path $PSScriptRoot -ChildPath "clear_collections.py"
    if (Test-Path $clearScript) {
        # Run with .venv Python to ensure pymongo is available
        $pythonCmd = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
        $allowNonLocal = $AllowNonLocalMongo -or ($env:CLEAR_COLLECTIONS_ALLOW_NONLOCAL -and $env:CLEAR_COLLECTIONS_ALLOW_NONLOCAL.ToLower() -eq 'true')
        
        $clearArgs = @($clearScript, '--action', 'delete', '--yes', '--database', $DatabaseName)
        if ($allowNonLocal) { $clearArgs += '--allow-nonlocal' }
        $output = & $pythonCmd @clearArgs 2>&1
        $mongoCleanupExitCode = $LASTEXITCODE
        if ($mongoCleanupExitCode -eq 0) {
            Write-Host "   ✅ Cleared MongoDB documents in '$DatabaseName' (collections/indexes preserved)" -ForegroundColor Green
        } else {
            Write-Host "   ⚠️  Skipped/failed Mongo cleanup:" -ForegroundColor Yellow
            Write-Host "      $output" -ForegroundColor Gray
        }
    } else {
        Write-Host "   ⚠️  clear_collections.py not found, skipping collection cleanup" -ForegroundColor Gray
    }

    if ($mongoStartupAttempted) {
        $mongoRunning = Test-MongoConnection
        if ($mongoRunning -or $mongoCleanupExitCode -eq 0) {
            Write-Host "   ✅ MongoDB started successfully" -ForegroundColor Green
        } else {
            Write-Host "   ⚠️  Could not start MongoDB automatically" -ForegroundColor Yellow
            Write-Host "      If you use mozaiksai docker compose, start it with: docker compose -f infra/compose/docker-compose.yml up -d mongo" -ForegroundColor Gray
            Write-Host "      Or run standalone Mongo: docker run -d --name mongodb -p 27017:27017 mongo:latest" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "`n🗄️  Keeping MongoDB data (-KeepDB flag set)" -ForegroundColor Gray
}

# Docker cleanup (optional - VERY DESTRUCTIVE)
if ($Full) {
    Write-Host "`n Cleaning Docker containers and images..." -ForegroundColor Yellow
    
    # Stop and remove ALL containers (running or stopped)
    $allContainers = docker ps -aq 2>$null
    if ($allContainers) {
        docker stop $allContainers 2>$null | Out-Null
        docker rm $allContainers 2>$null | Out-Null
        Write-Host "    Stopped and removed all Docker containers" -ForegroundColor Green
    } else {
        Write-Host "    No containers to remove" -ForegroundColor Gray
    }
    
    # Remove ALL images
    $allImages = docker images -q 2>$null
    if ($allImages) {
        docker rmi -f $allImages 2>$null | Out-Null
        Write-Host "    Removed all Docker images" -ForegroundColor Green
    } else {
        Write-Host "    No images to remove" -ForegroundColor Gray
    }
    
    # Prune everything (containers, networks, images, build cache)
    docker system prune -af --volumes 2>$null | Out-Null
    Write-Host "    Docker system pruned (build cache, networks, volumes)" -ForegroundColor Green
}

# Optional: .venv cache (Python pip cache)
if ($Full) {
    Write-Host "`n Cleaning Python pip cache..." -ForegroundColor Yellow
    if (Test-Path ".venv") {
        python -m pip cache purge 2>$null | Out-Null
        Write-Host "    Python pip cache purged" -ForegroundColor Green
    }
}

Write-Host "`n Cleanse complete!`n" -ForegroundColor Cyan

# Show usage if not running with Full flag
if (-not $Full) {
    Write-Host " Tip: Run with -Full flag for deeper cleaning (DB, Docker cache, pip cache)" -ForegroundColor Gray
    Write-Host "   Example: .\scripts\cleanse.ps1 -Full" -ForegroundColor Gray
    Write-Host "   Flags: -KeepLogs, -KeepDB, -AllowNonLocalMongo, -AllowStandaloneMongoFallback, -Full`n" -ForegroundColor Gray
}
