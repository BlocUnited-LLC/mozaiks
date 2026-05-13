param(
  [ValidateSet("portfolio", "single")]
  [string]$Preset = "portfolio",
  [string]$OwnerUserId = "",
  [string]$Name = "Placeholder App",
  [string]$Description = "Draft placeholder app record for local Console review.",
  [string]$AppId = "placeholder-app"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$venvPython = Join-Path $RepoRoot ".venv/Scripts/python.exe"
$pythonCmd = if (Test-Path $venvPython) { $venvPython } else { "python" }

$args = @("$PSScriptRoot/seed-placeholder-apps.py", "--preset", $Preset, "--name", $Name, "--description", $Description, "--app-id", $AppId)
if ($OwnerUserId) {
  $args += @("--owner-user-id", $OwnerUserId)
}

& $pythonCmd @args
exit $LASTEXITCODE
