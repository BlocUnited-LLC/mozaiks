<#
.SYNOPSIS
  Start Mozaiks backend (uvicorn), optionally ensuring Mongo and NATS are running first.
#>

param(
  [int]$Port = 8000,
  [switch]$SkipMongo,
  [switch]$SkipNats,
  [switch]$ForceStop
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Get-RepoEnvValue {
  param([string]$Name)

  $envFile = Join-Path $RepoRoot ".env"
  if (-not (Test-Path $envFile)) {
    return $null
  }

  foreach ($line in Get-Content $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }

    if ($trimmed -notmatch '^([^=]+)=(.*)$') {
      continue
    }

    $key = $matches[1].Trim()
    if ($key -ne $Name) {
      continue
    }

    $value = $matches[2].Trim()
    if ($value.Length -ge 2) {
      $first = $value[0]
      $last = $value[$value.Length - 1]
      if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
        $value = $value.Substring(1, $value.Length - 2)
      }
    }
    return $value
  }

  return $null
}

function Get-ConfigValue {
  param(
    [string]$Name,
    [string]$Default = ""
  )

  $envValue = [Environment]::GetEnvironmentVariable($Name)
  if (-not [string]::IsNullOrWhiteSpace($envValue)) {
    return $envValue
  }

  $repoValue = Get-RepoEnvValue -Name $Name
  if (-not [string]::IsNullOrWhiteSpace($repoValue)) {
    return $repoValue
  }

  return $Default
}

function Use-NatsAutomationTransport {
  $transportMode = (Get-ConfigValue -Name "MOZAIKS_AUTOMATION_TRANSPORT" -Default "http").Trim().ToLowerInvariant()
  return $transportMode -in @("nats", "dual")
}

function Get-ListeningProcessInfo {
  param([int]$LocalPort)

  $procIds = @()
  try {
    $procIds = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    # Fallback for environments where Get-NetTCPConnection may be unavailable
    $lines = netstat -ano | Select-String ":$LocalPort\s+.*LISTENING"
    $procIds = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
  }

  $results = @()
  foreach ($procId in $procIds) {
    if (-not $procId -or $procId -eq 0) { continue }
    try {
      $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId"
      $results += [PSCustomObject]@{
        ProcessId   = [int]$procId
        Name        = $proc.Name
        CommandLine = $proc.CommandLine
      }
    } catch {
      # Ignore races where process exits between detection and lookup
    }
  }
  return $results
}

function Ensure-PortAvailable {
  param(
    [int]$LocalPort,
    [switch]$KillExisting
  )

  $listeners = Get-ListeningProcessInfo -LocalPort $LocalPort
  if (-not $listeners -or $listeners.Count -eq 0) {
    return
  }

  Write-Host "[backend] Port $LocalPort is already in use by:" -ForegroundColor Yellow
  $listeners | ForEach-Object {
    Write-Host ("  PID {0} [{1}] {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) -ForegroundColor DarkYellow
  }

  if ($KillExisting) {
    Write-Host "[backend] ForceStop enabled - terminating existing listeners on port $LocalPort..." -ForegroundColor Yellow
    $listeners | ForEach-Object {
      try {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
        Write-Host ("  Stopped PID {0}" -f $_.ProcessId) -ForegroundColor Green
      } catch {
        Write-Host ("  Failed to stop PID {0}: {1}" -f $_.ProcessId, $_.Exception.Message) -ForegroundColor Red
        throw
      }
    }
    Start-Sleep -Milliseconds 350
    return
  }

  Write-Host "[backend] Aborting startup to avoid mixed services on the same port." -ForegroundColor Red
  Write-Host "[backend] Tip: rerun with -ForceStop to auto-kill listeners, or stop them manually." -ForegroundColor Yellow
  throw "Port $LocalPort is busy."
}

if (-not $SkipMongo) {
  Write-Host "[backend] Ensuring Mongo is running..." -ForegroundColor Cyan
  & "$PSScriptRoot/run-mongo.ps1"
}

if (-not $SkipNats -and (Use-NatsAutomationTransport)) {
  Write-Host "[backend] Ensuring NATS is running..." -ForegroundColor Cyan
  & "$PSScriptRoot/run-nats.ps1"
}

Ensure-PortAvailable -LocalPort $Port -KillExisting:$ForceStop

$venvPython = Join-Path $RepoRoot ".venv/Scripts/python.exe"
$pythonCmd = if (Test-Path $venvPython) { $venvPython } else { "python" }

Write-Host "[backend] Starting uvicorn on port $Port..." -ForegroundColor Cyan
Write-Host "[backend] Command: $pythonCmd -m uvicorn shared_app:app --host 0.0.0.0 --port $Port" -ForegroundColor DarkGray

& $pythonCmd -m uvicorn shared_app:app --host 0.0.0.0 --port $Port
