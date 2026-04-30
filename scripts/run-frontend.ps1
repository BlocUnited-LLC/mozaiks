<#
.SYNOPSIS
  Start Mozaiks frontend (Vite dev server).
#>

param(
  [int]$Port = 3000,
  [string]$BindHost = "0.0.0.0",
  [string]$PlatformPath = "",
  [string]$AppWorkspacePath = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

. "$PSScriptRoot/dev-path-selection.ps1"

$appSelection = Set-DevAppSelection -RepoRoot $RepoRoot -SurfaceName "frontend" -PlatformPath $PlatformPath -AppWorkspacePath $AppWorkspacePath
Write-Host $appSelection.Message -ForegroundColor DarkCyan

Write-Host "[frontend] Starting Vite on $BindHost`:$Port (strict port)..." -ForegroundColor Cyan
Write-Host "[frontend] Command: npm --prefix web_shell run dev -- --host $BindHost --port $Port --strictPort" -ForegroundColor DarkGray
Write-Host "[frontend] Open: http://localhost:$Port" -ForegroundColor Yellow

npm --prefix web_shell run dev -- --host $BindHost --port $Port --strictPort
