<#
.SYNOPSIS
  Build or serve the MkDocs site using the repo virtual environment.

.DESCRIPTION
  Suppresses the upstream mkdocs-material announcement banner about MkDocs 2.0
  via the documented `NO_MKDOCS_2_WARNING` environment variable, while keeping
  the repo pinned to MkDocs 1.x until a real generator migration happens.
#>

param(
  [switch]$Serve,
  [switch]$Strict,
  [string]$DevAddr = "127.0.0.1:8001"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  throw "Missing virtualenv interpreter at $python. Create .venv and install requirements first."
}

$previousNoMkDocsWarning = $env:NO_MKDOCS_2_WARNING
$env:NO_MKDOCS_2_WARNING = "1"

try {
  $mkdocsArgs = @("-m", "mkdocs")

  if ($Serve) {
    $mkdocsArgs += @("serve", "--dev-addr", $DevAddr)
  } else {
    $mkdocsArgs += "build"
  }

  if ($Strict) {
    $mkdocsArgs += "--strict"
  }

  & $python @mkdocsArgs
  if ($LASTEXITCODE -ne 0) {
    throw "MkDocs command failed with exit code $LASTEXITCODE."
  }
}
finally {
  if ($null -eq $previousNoMkDocsWarning) {
    Remove-Item Env:NO_MKDOCS_2_WARNING -ErrorAction SilentlyContinue
  } else {
    $env:NO_MKDOCS_2_WARNING = $previousNoMkDocsWarning
  }
}