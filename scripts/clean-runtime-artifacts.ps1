<#
.SYNOPSIS
  Remove ephemeral runtime artifact files produced by workflow generation/debug flows.

.DESCRIPTION
  Clears:
    - logs/agent_outputs/*
    - logs/workflow_converter/*
  Optionally also clears:
    - logs/logs/*
#>

param(
  [switch]$IncludeMainLogs,
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Remove-DirectoryFiles {
  param(
    [Parameter(Mandatory = $true)][string]$RelativePath
  )

  $target = Join-Path $RepoRoot $RelativePath
  if (-not (Test-Path $target)) {
    return 0
  }

  $removed = 0
  Get-ChildItem -Path $target -Recurse -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      Remove-Item -Path $_.FullName -Force -ErrorAction Stop
      $removed++
    } catch {
      # best effort
    }
  }
  return $removed
}

$agentOutputsRemoved = Remove-DirectoryFiles -RelativePath "logs/agent_outputs"
$workflowConverterRemoved = Remove-DirectoryFiles -RelativePath "logs/workflow_converter"
$mainLogsRemoved = 0
if ($IncludeMainLogs) {
  $mainLogsRemoved = Remove-DirectoryFiles -RelativePath "logs/logs"
}

if (-not $Quiet) {
  Write-Host "[clean-runtime-artifacts] Removed logs/agent_outputs/*: $agentOutputsRemoved file(s)" -ForegroundColor Green
  Write-Host "[clean-runtime-artifacts] Removed logs/workflow_converter/*: $workflowConverterRemoved file(s)" -ForegroundColor Green
  if ($IncludeMainLogs) {
    Write-Host "[clean-runtime-artifacts] Removed logs/logs/*: $mainLogsRemoved file(s)" -ForegroundColor Green
  }
}
