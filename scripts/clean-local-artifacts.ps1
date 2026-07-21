<#
.SYNOPSIS
  Remove local cache, log, and build artifacts without deleting dev setup files.

.DESCRIPTION
  Safe local cleanup for files that reappear during normal development:
    - Python bytecode/cache directories
    - pytest/ruff/mypy/coverage caches
    - Vite/frontend build and test output
    - docs/build output
    - Mozaiks local logs and runtime artifact output

  The script preserves local setup state such as .env files, virtualenvs,
  .vscode, and node_modules. It runs as a dry run unless -Apply is provided.
#>

param(
  [switch]$Apply,
  [switch]$KeepLogs,
  [switch]$StopRepoProcesses,
  [switch]$List,
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
Set-Location $RepoRoot

$PreservedNames = @(
  ".env",
  ".release-venv",
  ".venv",
  ".vscode",
  "node_modules"
)

$PreservedPathPrefixes = @(
  ".git",
  ".release-venv",
  ".venv",
  ".vscode",
  "chat-ui/node_modules",
  "web_shell/node_modules",
  "node_modules"
)

function Convert-ToRelativeRepoPath {
  param([Parameter(Mandatory = $true)][string]$FullPath)

  $relative = $FullPath.Substring($RepoRoot.Length).TrimStart([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
  return $relative.Replace("\", "/")
}

function Test-UnderRepoRoot {
  param([Parameter(Mandatory = $true)][string]$FullPath)

  $separator = [IO.Path]::DirectorySeparatorChar
  return $FullPath -ne $RepoRoot -and $FullPath.StartsWith($RepoRoot + $separator, [StringComparison]::OrdinalIgnoreCase)
}

function Test-PreservedPath {
  param([Parameter(Mandatory = $true)][System.IO.FileSystemInfo]$Item)

  if ($PreservedNames -contains $Item.Name) {
    return $true
  }

  $relative = Convert-ToRelativeRepoPath -FullPath $Item.FullName
  foreach ($prefix in $PreservedPathPrefixes) {
    if ($relative -eq $prefix -or $relative.StartsWith("$prefix/", [StringComparison]::OrdinalIgnoreCase)) {
      return $true
    }
  }

  return $false
}

function Add-LiteralCleanupTarget {
  param(
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[System.IO.FileSystemInfo]]$Targets,
    [Parameter(Mandatory = $true)][string]$RelativePath,
    [string]$RequiredKind = "Any"
  )

  $path = Join-Path $RepoRoot $RelativePath
  if (-not (Test-Path -LiteralPath $path)) {
    return
  }

  $item = Get-Item -LiteralPath $path -Force
  if ($RequiredKind -eq "Directory" -and -not $item.PSIsContainer) {
    return
  }
  if ($RequiredKind -eq "File" -and $item.PSIsContainer) {
    return
  }
  if (Test-PreservedPath -Item $item) {
    return
  }

  $Targets.Add($item)
}

function Add-GlobCleanupTargets {
  param(
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][System.Collections.Generic.List[System.IO.FileSystemInfo]]$Targets,
    [Parameter(Mandatory = $true)][string]$RelativePath,
    [Parameter(Mandatory = $true)][string]$Filter,
    [switch]$Recurse,
    [switch]$FilesOnly,
    [switch]$DirectoriesOnly
  )

  $path = Join-Path $RepoRoot $RelativePath
  if (-not (Test-Path -LiteralPath $path)) {
    return
  }

  $items = Get-ChildItem -LiteralPath $path -Force -Filter $Filter -Recurse:$Recurse -ErrorAction SilentlyContinue
  foreach ($item in $items) {
    if ($FilesOnly -and $item.PSIsContainer) {
      continue
    }
    if ($DirectoriesOnly -and -not $item.PSIsContainer) {
      continue
    }
    if (Test-PreservedPath -Item $item) {
      continue
    }
    $Targets.Add($item)
  }
}

function Stop-RepoArtifactProcesses {
  $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.ProcessId -ne $PID -and
      $_.CommandLine -and
      $_.CommandLine.Contains($RepoRoot) -and
      $_.Name -match "^(python|python3|pytest|uvicorn|node|cmd)\.exe$" -and
      $_.CommandLine -match "(pytest|uvicorn|vite)"
    }

  $stopped = 0
  foreach ($proc in $candidates) {
    try {
      Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
      $stopped++
      if (-not $Quiet) {
        Write-Host ("[clean-local-artifacts] Stopped PID {0} ({1})" -f $proc.ProcessId, $proc.Name) -ForegroundColor Yellow
      }
    } catch {
      if (-not $Quiet) {
        Write-Host ("[clean-local-artifacts] Could not stop PID {0}: {1}" -f $proc.ProcessId, $_.Exception.Message) -ForegroundColor DarkYellow
      }
    }
  }
  return $stopped
}

function Get-CleanupTargets {
  $targets = New-Object System.Collections.Generic.List[System.IO.FileSystemInfo]

  $directories = @(
    ".mkdocs-tmp",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".tox",
    ".nox",
    ".pytype",
    "build",
    "chat-ui/dist",
    "dist",
    "htmlcov",
    "mozaiks.egg-info",
    "site",
    "tmp",
    "tmpdist",
    "web_shell/.tmp",
    "web_shell/dist",
    "web_shell/playwright-report",
    "web_shell/test-results"
  )

  if (-not $KeepLogs) {
    $directories += @(
      ".logs",
      "logs/agent_outputs",
      "logs/logs",
      "logs/workflow_converter"
    )
  }

  foreach ($directory in $directories) {
    Add-LiteralCleanupTarget -Targets $targets -RelativePath $directory -RequiredKind "Directory"
  }

  $files = @(
    ".coverage",
    ".tmp-build-output.txt",
    "coverage.xml",
    "web_shell/vite-dev.err.log",
    "web_shell/vite-dev.log"
  )

  foreach ($file in $files) {
    Add-LiteralCleanupTarget -Targets $targets -RelativePath $file -RequiredKind "File"
  }

  Add-GlobCleanupTargets -Targets $targets -RelativePath "." -Filter "__pycache__" -Recurse -DirectoriesOnly
  Add-GlobCleanupTargets -Targets $targets -RelativePath "." -Filter ".vite" -Recurse -DirectoriesOnly
  Add-GlobCleanupTargets -Targets $targets -RelativePath "." -Filter "*.pyc" -Recurse -FilesOnly
  Add-GlobCleanupTargets -Targets $targets -RelativePath "." -Filter "*.pyo" -Recurse -FilesOnly
  Add-GlobCleanupTargets -Targets $targets -RelativePath "." -Filter "*_debug.txt" -FilesOnly
  Add-GlobCleanupTargets -Targets $targets -RelativePath "." -Filter ".pytest_live_smoke*.txt" -FilesOnly
  Add-GlobCleanupTargets -Targets $targets -RelativePath "web_shell/playwright" -Filter "tmp-generated*.log" -FilesOnly

  $rootViteDemoCache = Join-Path $RepoRoot "vite.demo.config.js"
  if (Test-Path -LiteralPath $rootViteDemoCache) {
    $item = Get-Item -LiteralPath $rootViteDemoCache -Force
    if ($item.PSIsContainer) {
      $targets.Add($item)
    }
  }

  return $targets |
    Where-Object { Test-UnderRepoRoot -FullPath $_.FullName } |
    Sort-Object FullName -Unique |
    Sort-Object { $_.FullName.Length } -Descending
}

if ($StopRepoProcesses -and $Apply) {
  $stoppedCount = Stop-RepoArtifactProcesses
  if (-not $Quiet) {
    Write-Host "[clean-local-artifacts] Stopped $stoppedCount repo artifact process(es)." -ForegroundColor Yellow
  }
}

$cleanupTargets = @(Get-CleanupTargets)

if (-not $Quiet) {
  if ($Apply) {
    Write-Host "[clean-local-artifacts] Removing $($cleanupTargets.Count) local artifact path(s)..." -ForegroundColor Cyan
  } else {
    Write-Host "[clean-local-artifacts] Dry run. Add -Apply to remove these $($cleanupTargets.Count) path(s)." -ForegroundColor Cyan
    Write-Host "[clean-local-artifacts] Add -List to print every target." -ForegroundColor Gray
  }
}

$removed = 0
$failed = @()

foreach ($target in $cleanupTargets) {
  $relative = Convert-ToRelativeRepoPath -FullPath $target.FullName
  if ($List -and -not $Quiet) {
    Write-Host "  $relative" -ForegroundColor DarkGray
  }

  if (-not $Apply) {
    continue
  }

  try {
    Remove-Item -LiteralPath $target.FullName -Recurse -Force -ErrorAction Stop
    $removed++
  } catch {
    $failed += "$relative ($($_.Exception.Message))"
  }
}

if ($Apply -and -not $Quiet) {
  Write-Host "[clean-local-artifacts] Removed $removed path(s)." -ForegroundColor Green
}

if ($failed.Count -gt 0) {
  Write-Host "[clean-local-artifacts] Could not remove $($failed.Count) path(s):" -ForegroundColor Yellow
  $failed | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkYellow }
  Write-Host "[clean-local-artifacts] Close active log writers or rerun with -StopRepoProcesses -Apply." -ForegroundColor Yellow
}

if (-not $Quiet) {
  Write-Host "[clean-local-artifacts] Preserved .env, virtualenvs, .vscode, and node_modules." -ForegroundColor Gray
}
