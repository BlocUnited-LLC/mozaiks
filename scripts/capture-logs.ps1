<#
capture-logs.ps1

Capture recent docker logs for the `mozaiksai-app` container, filter for key transport and UI event lines,
and write both a raw capture and a filtered report into `scripts/logs/`.

Usage examples:
  # capture logs from the last 60 minutes and include infra logs
  .\scripts\capture-logs.ps1 -SinceMinutes 60 -IncludeInfra

  # capture only last 500 lines
  .\scripts\capture-logs.ps1 -TailLines 500

Output files (created under scripts/logs):
  - backend-raw-<timestamp>.log
  - backend-filtered-<timestamp>.log

#>

param(
    [int]$SinceMinutes = 60,
    [int]$TailLines = 1000,
    [switch]$IncludeInfra
)

function Initialize-Directory($p) {
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

# Resolve script root robustly (works when called from other contexts)
$ScriptRoot = $PSScriptRoot
if (-not $ScriptRoot) { try { $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path } catch { $ScriptRoot = (Get-Location).ProviderPath } }
$outDir = Join-Path -Path $ScriptRoot -ChildPath "..\logs\logs" | Resolve-Path -Relative
if (-not $outDir) { $outDir = Join-Path -Path $ScriptRoot -ChildPath "..\logs\logs" }
Initialize-Directory $outDir

$ts = (Get-Date).ToString('yyyyMMdd-HHmmss')
$rawPath = Join-Path $outDir "backend-raw-$ts.log"
$filteredPath = Join-Path $outDir "backend-filtered-$ts.log"

Write-Host "Capturing logs to:`n  $rawPath`n  $filteredPath" -ForegroundColor Cyan

# Try docker --since (available on newer docker). If it fails, fallback to --tail
$useSince = $false
try {
    docker logs --since "1m" mozaiksai-app > $null 2>&1
    $useSince = $true
} catch {
    $useSince = $false
}

if ($useSince) {
    $sinceArg = (Get-Date).AddMinutes(-$SinceMinutes).ToString('yyyy-MM-ddTHH:mm:ss')
    Write-Host "Using docker logs --since $sinceArg" -ForegroundColor Yellow
    docker logs --since $sinceArg mozaiksai-app 2>&1 | Tee-Object -FilePath $rawPath | Out-Null
} else {
    Write-Host "docker logs --since not available; using --tail $TailLines" -ForegroundColor Yellow
    docker logs --tail $TailLines mozaiksai-app 2>&1 | Tee-Object -FilePath $rawPath | Out-Null
}

if ($IncludeInfra) {
    Write-Host "Appending infra logs (compose logs) to raw file..." -ForegroundColor Yellow
    docker compose -f infra/compose/docker-compose.yml logs --no-color 2>&1 | Tee-Object -FilePath $rawPath -Append | Out-Null
}

# Filter the raw file for useful event lines
$patterns = 'chat.text','chat.tool_call','chat_meta','TRANSPORT','Processing event','Sending envelope'
Get-Content $rawPath | Select-String -Pattern ($patterns -join '|') -CaseSensitive:$false | ForEach-Object { $_.Line } | Set-Content $filteredPath

# Print a short summary
$rawLines = (Get-Content $rawPath).Count
$filteredLines = (Get-Content $filteredPath).Count
Write-Host "Capture complete: raw lines=$rawLines, filtered lines=$filteredLines" -ForegroundColor Green
Write-Host "Filtered file: $filteredPath" -ForegroundColor Cyan

if ($filteredLines -eq 0) {
    Write-Host "No transport/UI event lines found in the captured logs. Try increasing -SinceMinutes or -TailLines." -ForegroundColor Yellow
}

Write-Host "Done." -ForegroundColor Green
