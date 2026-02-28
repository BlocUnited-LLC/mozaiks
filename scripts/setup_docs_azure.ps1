param(
  [string]$Repo = "",                       # e.g. BlocUnited-LLC/MozaiksAI
  [string]$ResourceGroup = "mozaiksai-docs-rg",
  [string]$Location = "eastus2",
  [string]$StaticWebAppName = "",
  [string]$Domain = "docs.mozaiks.ai",
  [switch]$SkipCreate,
  [switch]$SkipSecret,
  [switch]$SkipDomain
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
  Write-Host "\n==> $Message" -ForegroundColor Cyan
}

function Get-RepoFromOrigin {
  $origin = (git config --get remote.origin.url 2>$null)
  if (-not $origin) { return "" }
  if ($origin -match "github\.com[:/](?<owner>[^/]+)/(?<repo>[^/.]+)(\.git)?$") {
    return "$($Matches.owner)/$($Matches.repo)"
  }
  return ""
}

function Ensure-AzWorks {
  # Work around broken global Azure CLI extensions by using an isolated extension directory under the repo.
  $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
  $extDir = Join-Path $repoRoot ".azextensions"
  New-Item -ItemType Directory -Force -Path $extDir | Out-Null
  $env:AZURE_EXTENSION_DIR = $extDir

  Write-Step "Azure CLI context"
  $acct = & az account show --query "{name:name, id:id, tenantId:tenantId}" -o json
  $acct | Write-Host
}

function Ensure-StaticWebAppExtension {
  Write-Step "Ensuring az staticwebapp extension"
  $existing = & az extension show --name staticwebapp -o none 2>$null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "staticwebapp extension already installed" -ForegroundColor Green
    return
  }

  # This extension is preview-only.
  & az extension add --name staticwebapp --allow-preview true
}

function Ensure-ResourceGroup([string]$Rg, [string]$Loc) {
  Write-Step "Ensuring resource group: $Rg ($Loc)"
  $exists = & az group exists --name $Rg
  if ($exists -eq "true") {
    Write-Host "Resource group exists" -ForegroundColor Green
    return
  }
  & az group create --name $Rg --location $Loc -o none
}

function Ensure-StaticWebApp([string]$Name, [string]$Rg, [string]$Loc) {
  Write-Step "Ensuring Static Web App: $Name"

  $show = & az staticwebapp show --name $Name --resource-group $Rg -o json 2>$null
  if ($LASTEXITCODE -eq 0 -and $show) {
    Write-Host "Static Web App already exists" -ForegroundColor Green
    return
  }

  Write-Host "Creating Static Web App (Free tier)" -ForegroundColor Yellow
  & az staticwebapp create --name $Name --resource-group $Rg --location $Loc --sku Free -o none
}

function Set-GitHubSecret([string]$RepoSlug, [string]$Name, [string]$Value) {
  Write-Step "Setting GitHub secret $Name for $RepoSlug"
  & gh secret set $Name -R $RepoSlug -b $Value
}

function Configure-CustomDomain([string]$Name, [string]$Rg, [string]$HostName) {
  Write-Step "Configuring custom domain: $HostName"

  # This typically returns validation instructions if DNS isn't set yet.
  $out = & az staticwebapp hostname set --name $Name --resource-group $Rg --hostname $HostName 2>&1
  $text = ($out | Out-String)
  Write-Host $text

  Write-Host "If Azure asks for validation, add the required DNS record at your registrar and re-run this script." -ForegroundColor Yellow
}

# ---- Main ----
Write-Step "Azure docs hosting setup (Static Web Apps)"

if (-not (git rev-parse --is-inside-work-tree 2>$null)) {
  throw "Not a git repository. Run this from the repo root."
}

if (-not $Repo) {
  $Repo = Get-RepoFromOrigin
}
if (-not $Repo) {
  throw "Could not infer repo from origin. Re-run with -Repo OWNER/REPO."
}

if (-not $StaticWebAppName) {
  $suffix = (Get-Random -Minimum 1000 -Maximum 9999)
  $StaticWebAppName = "mozaiksai-docs-$suffix"
}

Ensure-AzWorks
Ensure-StaticWebAppExtension

if (-not $SkipCreate) {
  Ensure-ResourceGroup -Rg $ResourceGroup -Loc $Location
  Ensure-StaticWebApp -Name $StaticWebAppName -Rg $ResourceGroup -Loc $Location
}

Write-Step "Fetching Static Web App details"
$defaultHostname = & az staticwebapp show --name $StaticWebAppName --resource-group $ResourceGroup --query defaultHostname -o tsv
Write-Host "Default hostname: https://$defaultHostname" -ForegroundColor Green

if (-not $SkipSecret) {
  Write-Step "Fetching deployment token"
  $token = & az staticwebapp secrets list --name $StaticWebAppName --resource-group $ResourceGroup --query properties.apiKey -o tsv
  if (-not $token) {
    throw "Could not retrieve deployment token."
  }
  Set-GitHubSecret -RepoSlug $Repo -Name "AZURE_STATIC_WEB_APPS_API_TOKEN" -Value $token
}

if (-not $SkipDomain) {
  Configure-CustomDomain -Name $StaticWebAppName -Rg $ResourceGroup -HostName $Domain
}

Write-Step "Next steps"
Write-Host "1) Commit and push the Azure docs workflow: .github/workflows/docs-azure.yml" -ForegroundColor Yellow
Write-Host "2) After push, GitHub Actions will deploy MkDocs on every docs change." -ForegroundColor Yellow
Write-Host "3) Point DNS for $Domain to the SWA hostname or follow Azure validation output above." -ForegroundColor Yellow
Write-Host "   SWA default hostname: $defaultHostname" -ForegroundColor Yellow
Write-Host "\nRe-run anytime:" -ForegroundColor Yellow
Write-Host "  .\\scripts\\setup_docs_azure.ps1 -StaticWebAppName $StaticWebAppName" -ForegroundColor Yellow
