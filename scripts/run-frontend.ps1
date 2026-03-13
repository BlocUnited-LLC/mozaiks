<#
.SYNOPSIS
  Start Mozaiks frontend (Vite dev server).
#>

param(
  [int]$Port = 3000,
  [string]$BindHost = "0.0.0.0"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[frontend] Starting Vite on $BindHost`:$Port (strict port)..." -ForegroundColor Cyan
Write-Host "[frontend] Command: npm --prefix app run dev -- --host $BindHost --port $Port --strictPort" -ForegroundColor DarkGray
Write-Host "[frontend] Open: http://localhost:$Port" -ForegroundColor Yellow

npm --prefix app run dev -- --host $BindHost --port $Port --strictPort
