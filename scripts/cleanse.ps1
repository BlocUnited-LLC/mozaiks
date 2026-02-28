# ==============================================================================
# MozaiksAI - Deep Cleanse Script
# Removes all cache, build artifacts, logs, and optionally runtime state
# ==============================================================================

param(
    [switch]$KeepLogs,
    [switch]$KeepDB,
    [switch]$AllowNonLocalMongo,
    [switch]$Full
)

# SAFETY: Only ever touch MozaiksAI database - hardcoded to prevent accidents
$DatabaseName = "MozaiksAI"

Write-Host " Starting MozaiksAI deep cleanse..." -ForegroundColor Cyan

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
        Get-ChildItem -Path "logs/logs" -Filter "*.log" -ErrorAction SilentlyContinue | Remove-Item -Force
        Write-Host "    Removed logs/logs/*.log" -ForegroundColor Green
    }
} else {
    Write-Host "`n Keeping logs (-KeepLogs flag set)" -ForegroundColor Gray
}

# MongoDB collections cleanup (clears documents only, preserves collections/indexes)
if (-not $KeepDB) {
    Write-Host "`n🗄️  Clearing MongoDB collections (documents only)..." -ForegroundColor Yellow
    
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
    
    # Try to ensure MongoDB is running
    $mongoRunning = Test-MongoConnection
    if (-not $mongoRunning) {
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
                Start-Sleep -Seconds 3
                $mongoRunning = Test-MongoConnection
                if (-not $mongoRunning -and $dockerStartExit -ne 0) {
                    Write-Host "   Docker start error: $startOut" -ForegroundColor Gray
                }
            }
            
            # If still not running, try to create a new container
            if (-not $mongoRunning) {
                Write-Host "   Creating new MongoDB Docker container..." -ForegroundColor Gray
                $runOut = & docker run -d --name mongodb -p 27017:27017 mongo:latest 2>&1
                $dockerRunExit = $LASTEXITCODE
                Start-Sleep -Seconds 5
                $mongoRunning = Test-MongoConnection
                if (-not $mongoRunning -and $dockerRunExit -ne 0) {
                    Write-Host "   Docker run error: $runOut" -ForegroundColor Gray
                }
            }
        }
        
        # Try 2: Windows service
        if (-not $mongoRunning) {
            $mongoService = Get-Service -Name "MongoDB" -ErrorAction SilentlyContinue
            if ($mongoService) {
                if ($mongoService.Status -ne 'Running') {
                    Write-Host "   Starting MongoDB Windows service..." -ForegroundColor Gray
                    Start-Service -Name "MongoDB" -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 3
                    $mongoRunning = Test-MongoConnection
                }
            }
        }
        
        if ($mongoRunning) {
            Write-Host "   ✅ MongoDB started successfully" -ForegroundColor Green
        } else {
            Write-Host "   ⚠️  Could not start MongoDB automatically" -ForegroundColor Yellow
            Write-Host "      If you use MozaiksAI docker compose, start it with: docker compose -f infra/compose/docker-compose.yml up -d mongo" -ForegroundColor Gray
            Write-Host "      Or run standalone Mongo: docker run -d --name mongodb -p 27017:27017 mongo:latest" -ForegroundColor Gray
        }
    }
    
    # Now attempt the cleanup - ONLY MozaiksAI database
    $clearScript = Join-Path -Path $PSScriptRoot -ChildPath "clear_collections.py"
    if (Test-Path $clearScript) {
        # Run with .venv Python to ensure pymongo is available
        $pythonCmd = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
        $allowNonLocal = $AllowNonLocalMongo -or ($env:CLEAR_COLLECTIONS_ALLOW_NONLOCAL -and $env:CLEAR_COLLECTIONS_ALLOW_NONLOCAL.ToLower() -eq 'true')
        
        $args = @($clearScript, '--action', 'delete', '--yes', '--database', $DatabaseName)
        if ($allowNonLocal) { $args += '--allow-nonlocal' }
        $output = & $pythonCmd @args 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   ✅ Cleared MongoDB documents in '$DatabaseName' (collections/indexes preserved)" -ForegroundColor Green
        } else {
            Write-Host "   ⚠️  Skipped/failed Mongo cleanup:" -ForegroundColor Yellow
            Write-Host "      $output" -ForegroundColor Gray
        }
    } else {
        Write-Host "   ⚠️  clear_collections.py not found, skipping collection cleanup" -ForegroundColor Gray
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
    Write-Host "   Flags: -KeepLogs, -KeepDB, -AllowNonLocalMongo, -DatabaseName, -Full`n" -ForegroundColor Gray
}
