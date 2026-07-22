<#
.SYNOPSIS
  Verify a local Mozaiks Studio run is actually usable.

.DESCRIPTION
  Checks the running Studio backend, the running frontend, and the create-app
  transition contract. By default it also runs the targeted Playwright smoke
  that proves the Create App transition overlay can close back to Apps.

  Start Studio first:
    .\scripts\run-studio.ps1 -ForceStop

  Then run:
    .\scripts\smoke-studio-local.ps1
#>

param(
  [string]$BackendUrl = "http://localhost:8000",
  [string]$FrontendUrl = "http://localhost:3000",
  [int]$TimeoutSeconds = 30,
  [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Normalize-BaseUrl {
  param([string]$Url)
  return $Url.Trim().TrimEnd("/")
}

function Assert-Smoke {
  param(
    [bool]$Condition,
    [string]$Message
  )

  if (-not $Condition) {
    Write-Host ("[smoke] FAIL: {0}" -f $Message) -ForegroundColor Red
    throw $Message
  }
}

function Wait-HttpOk {
  param(
    [string]$Name,
    [string]$Uri,
    [int]$TimeoutSeconds
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $lastError = $null

  while ((Get-Date) -lt $deadline) {
    try {
      $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
        Write-Host ("[smoke] OK: {0} ({1})" -f $Name, $Uri) -ForegroundColor Green
        return $response
      }
      $lastError = "HTTP $($response.StatusCode)"
    } catch {
      $lastError = $_.Exception.Message
    }
    Start-Sleep -Seconds 2
  }

  Write-Host ("[smoke] FAIL: {0} did not become ready within {1}s" -f $Name, $TimeoutSeconds) -ForegroundColor Red
  if ($lastError) {
    Write-Host ("[smoke] Last error: {0}" -f $lastError) -ForegroundColor DarkYellow
  }
  throw "$Name readiness timed out."
}

function Wait-Json {
  param(
    [string]$Name,
    [string]$Uri,
    [int]$TimeoutSeconds
  )

  $response = Wait-HttpOk -Name $Name -Uri $Uri -TimeoutSeconds $TimeoutSeconds
  try {
    return $response.Content | ConvertFrom-Json -ErrorAction Stop
  } catch {
    Write-Host ("[smoke] FAIL: {0} did not return valid JSON" -f $Name) -ForegroundColor Red
    throw
  }
}

$BackendUrl = Normalize-BaseUrl $BackendUrl
$FrontendUrl = Normalize-BaseUrl $FrontendUrl

Write-Host "[smoke] Mozaiks Studio local smoke" -ForegroundColor Cyan
Write-Host ("[smoke] Backend:  {0}" -f $BackendUrl) -ForegroundColor DarkGray
Write-Host ("[smoke] Frontend: {0}" -f $FrontendUrl) -ForegroundColor DarkGray

$health = Wait-Json -Name "backend health" -Uri "$BackendUrl/health" -TimeoutSeconds $TimeoutSeconds
Assert-Smoke -Condition ($health.status -eq "ok") -Message "Backend /health did not report status=ok."

$shell = Wait-Json -Name "backend shell config" -Uri "$BackendUrl/api/shell-config" -TimeoutSeconds $TimeoutSeconds
$pagePaths = @($shell.pages | ForEach-Object { $_.path })
Assert-Smoke -Condition ($pagePaths -contains "/apps") -Message "Shell config is missing /apps."
Assert-Smoke -Condition ($pagePaths -contains "/create") -Message "Shell config is missing /create transition entrypoint."

$createAction = @($shell.header.actions | Where-Object { $_.id -eq "create-app" } | Select-Object -First 1)
Assert-Smoke -Condition ($null -ne $createAction) -Message "Shell config is missing the create-app header action."
Assert-Smoke -Condition ($createAction.path -eq "/create") -Message "create-app header action must route to /create."

$transition = Wait-Json -Name "create transition" -Uri "$BackendUrl/api/transitions/app_type_selector" -TimeoutSeconds $TimeoutSeconds
Assert-Smoke -Condition ($transition.transition_type -eq "user_choice_context") -Message "app_type_selector must be a user_choice_context transition."
Assert-Smoke -Condition ($transition.ui.props.dismissible -eq $true) -Message "app_type_selector must be dismissible."
Assert-Smoke -Condition ($transition.ui.props.dismiss_to -eq "/apps") -Message "app_type_selector dismiss_to must be /apps."
Assert-Smoke -Condition ($transition.ui.props.close_label -eq "Back to Apps") -Message "app_type_selector close label must be Back to Apps."

Wait-HttpOk -Name "frontend /apps" -Uri "$FrontendUrl/apps" -TimeoutSeconds $TimeoutSeconds | Out-Null
Wait-HttpOk -Name "frontend /create" -Uri "$FrontendUrl/create" -TimeoutSeconds $TimeoutSeconds | Out-Null

if (-not $SkipBrowser) {
  $nodeModules = Join-Path $RepoRoot "web_shell\node_modules"
  if (-not (Test-Path $nodeModules)) {
    Write-Host "[smoke] FAIL: web_shell dependencies are missing." -ForegroundColor Red
    Write-Host "[smoke] Run: npm --prefix web_shell ci" -ForegroundColor Yellow
    throw "web_shell/node_modules is missing."
  }

  Write-Host "[smoke] Running browser overlay smoke..." -ForegroundColor Cyan
  npm --prefix web_shell run test:responsive-smoke -- --grep "create app transition overlay can return to Apps"
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

Write-Host "[smoke] PASS: local Studio backend, frontend, and create transition are ready." -ForegroundColor Green
