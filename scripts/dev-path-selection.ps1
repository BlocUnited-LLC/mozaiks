<#
.SYNOPSIS
  Shared active-app selection helpers for local development scripts.
#>

function Get-RepoEnvValue {
  param(
    [string]$RepoRoot,
    [string]$Name
  )

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
    [string]$RepoRoot,
    [string]$Name,
    [string]$Default = ""
  )

  $envValue = [Environment]::GetEnvironmentVariable($Name)
  if (-not [string]::IsNullOrWhiteSpace($envValue)) {
    return $envValue
  }

  $repoValue = Get-RepoEnvValue -RepoRoot $RepoRoot -Name $Name
  if (-not [string]::IsNullOrWhiteSpace($repoValue)) {
    return $repoValue
  }

  return $Default
}

function Set-DevAppSelection {
  param(
    [string]$RepoRoot,
    [string]$SurfaceName,
    [string]$PlatformPath = "",
    [string]$AppWorkspacePath = ""
  )

  if (-not [string]::IsNullOrWhiteSpace($PlatformPath) -and -not [string]::IsNullOrWhiteSpace($AppWorkspacePath)) {
    throw "Specify either -PlatformPath or -AppWorkspacePath, not both."
  }

  if (-not [string]::IsNullOrWhiteSpace($PlatformPath)) {
    $env:PLATFORM_PATH = $PlatformPath
    Remove-Item Env:MOZAIKS_APP_WORKSPACE_PATH -ErrorAction SilentlyContinue
    return [PSCustomObject]@{
      Kind = "platform"
      Value = $PlatformPath
      Message = "[$SurfaceName] Active app override via PLATFORM_PATH: $PlatformPath"
    }
  }

  if (-not [string]::IsNullOrWhiteSpace($AppWorkspacePath)) {
    $env:MOZAIKS_APP_WORKSPACE_PATH = $AppWorkspacePath
    # Also set PLATFORM_PATH so child processes override any repo-local .env value.
    $env:PLATFORM_PATH = $AppWorkspacePath
    return [PSCustomObject]@{
      Kind = "workspace"
      Value = $AppWorkspacePath
      Message = "[$SurfaceName] Active app workspace override: $AppWorkspacePath"
    }
  }

  $configuredPlatformPath = Get-ConfigValue -RepoRoot $RepoRoot -Name "PLATFORM_PATH"
  if (-not [string]::IsNullOrWhiteSpace($configuredPlatformPath)) {
    return [PSCustomObject]@{
      Kind = "platform"
      Value = $configuredPlatformPath
      Message = "[$SurfaceName] Active app path from PLATFORM_PATH: $configuredPlatformPath"
    }
  }

  $configuredWorkspacePath = Get-ConfigValue -RepoRoot $RepoRoot -Name "MOZAIKS_APP_WORKSPACE_PATH"
  if (-not [string]::IsNullOrWhiteSpace($configuredWorkspacePath)) {
    return [PSCustomObject]@{
      Kind = "workspace"
      Value = $configuredWorkspacePath
      Message = "[$SurfaceName] Active app workspace from MOZAIKS_APP_WORKSPACE_PATH: $configuredWorkspacePath"
    }
  }

  return [PSCustomObject]@{
    Kind = "default"
    Value = $null
    Message = "[$SurfaceName] No active app configured. Set MOZAIKS_APP_WORKSPACE_PATH or PLATFORM_PATH."
  }
}