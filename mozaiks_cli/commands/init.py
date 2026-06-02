"""mozaiks init - Initialize an app workspace from the Dev/CLI layer."""

import ast
import json
import re
import shutil
import sys
from copy import deepcopy
from pathlib import Path

import yaml

from mozaiks_cli.agent_guidance import build_agent_guidance_files
from mozaiks_cli.workspace import is_framework_repo_root
from mozaiksai.resources import resolve_factory_app_root, resolve_factory_brand_root

# Tier definitions
TIER_PRESETS = {
    "engine": {
        "ai_runtime": True,
        "modules": False,
        "event_bus": False,
        "auth": False,
        "admin": False,
        "chat_ui": False,
    },
    "chat": {
        "ai_runtime": True,
        "modules": False,
        "event_bus": False,
        "auth": False,
        "admin": False,
        "chat_ui": True,
    },
    "integrated": {
        "ai_runtime": True,
        "modules": True,
        "event_bus": True,
        "auth": True,
        "admin": False,
        "chat_ui": True,
    },
    "full": {
        "ai_runtime": True,
        "modules": True,
        "event_bus": True,
        "auth": True,
        "admin": True,
        "chat_ui": True,
    },
}

def run(args):
    """Execute the init command."""
    preset = args.preset
    starter = bool(getattr(args, "starter", False))

    # Validate preset
    if preset not in TIER_PRESETS:
        print(f"Error: Unknown preset '{preset}'")
        print(f"Available: {', '.join(TIER_PRESETS.keys())}")
        return

    app_name = _resolve_app_name(args.name, args.directory)
    target_dir = _resolve_target_dir(args.directory, app_name)
    if is_framework_repo_root(target_dir.resolve()):
      print(f"Error: refusing to scaffold inside framework repo root: {target_dir.resolve()}")
      print("Use --dir <workspace> to target an app workspace directory.")
      return

    app_root = target_dir / "app"
    existing_surfaces = _existing_scaffold_surfaces(
        app_root,
        target_dir / "platform",
        target_dir / "brand",
        target_dir / "ui",
    )
    if existing_surfaces:
        print(f"Error: scaffold already exists in {target_dir}")
        print(f"Found: {', '.join(existing_surfaces)}")
        print("Choose a new target directory or remove the existing scaffold first.")
        return

    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Initializing Mozaiks project: {app_name}")
    print(f"Preset: {preset}")
    print(f"Scaffold: {'starter' if starter else 'blank'}")
    print(f"Target: {target_dir}\n")

    # Collect admin email when the preset includes admin portal
    admin_email = None
    if TIER_PRESETS[preset].get("admin"):
        admin_email = _prompt_admin_email()

    create_scaffold(
        target_dir=target_dir,
        preset=preset,
        app_name=app_name,
        admin_email=admin_email,
        starter=starter,
    )

    print("\nProject initialized successfully.")
    _show_next_steps(target_dir, preset, starter)


def create_scaffold(
    *,
    target_dir: Path,
    preset: str,
    app_name: str,
    admin_email: str | None = None,
    starter: bool = False,
) -> Path:
    """Create a fresh scaffold in target_dir and return the workspace root."""
    if preset not in TIER_PRESETS:
        raise ValueError(f"Unknown preset '{preset}'")

    app_root = target_dir / "app"
    existing_surfaces = _existing_scaffold_surfaces(
        app_root,
        target_dir / "platform",
        target_dir / "brand",
        target_dir / "ui",
    )
    if existing_surfaces:
        raise ValueError(
            f"scaffold already exists in {target_dir} (found: {', '.join(existing_surfaces)})"
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir = target_dir / "scripts"
    _create_bundle_scaffold(
        target_dir=target_dir,
        preset=preset,
        app_name=app_name,
        admin_email=admin_email,
        starter=starter,
    )
    _create_standard_consumer_files(
        target_dir=target_dir,
        scripts_dir=scripts_dir,
        app_name=app_name,
        preset=preset,
    )
    return target_dir


def _existing_scaffold_surfaces(*paths: Path) -> list[str]:
    existing = []
    for path in paths:
        if path.exists():
            existing.append(path.name + "/")
    return existing


def _resolve_app_name(explicit_name: str | None, explicit_directory: str | None) -> str:
    """Resolve the app name from args or an interactive prompt."""
    if explicit_name:
        return explicit_name

    default_name = _default_app_name(explicit_directory)
    if sys.stdin is None or sys.stdin.closed:
        return default_name

    print("Enter the app name for this project.")
    print(f"(Press Enter to use '{default_name}')\n")
    try:
        response = input("App name: ").strip()
    except (EOFError, KeyboardInterrupt):
        response = ""
    app_name = response or default_name
    print(f"App name set to: {app_name}\n")
    return app_name


def _default_app_name(explicit_directory: str | None) -> str:
    if explicit_directory:
        directory_name = Path(explicit_directory).name
        if directory_name not in {"", "."}:
            return directory_name
    return "my-app"


def _resolve_target_dir(explicit_directory: str | None, app_name: str) -> Path:
    if explicit_directory:
        return Path(explicit_directory)
    return Path(_slugify_app_name(app_name))


def _slugify_app_name(app_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (app_name or "").strip().lower()).strip("-")
    return slug or "my-app"


def _prompt_admin_email() -> str:
    """Prompt for the admin email address. Falls back to a placeholder if skipped."""
    print("Admin portal is included in this preset.")
    print("Enter the email address for the admin account.")
    print("This is the email you will use to log in to the /admin portal.")
    print("(Press Enter to skip and set it later in app/app.json admins)\n")
    try:
        email = input("Admin email: ").strip()
    except (EOFError, KeyboardInterrupt):
        email = ""
    if not email:
        print("Skipped — update admins in app/app.json before deploying.\n")
        return "admin@example.com"
    print(f"Admin email set to: {email}\n")
    return email


def _create_bundle_scaffold(
  *,
  target_dir: Path,
  preset: str,
  app_name: str,
  admin_email: str | None,
  starter: bool,
) -> None:
  app_root = target_dir / "app"
  config_dir = app_root / "config"
  control_plane_config_dir = target_dir / "control_plane" / "config"
  services_dir = app_root / "services"
  service_integrations_dir = services_dir / "integrations"
  service_adapters_dir = services_dir / "adapters"
  service_security_dir = services_dir / "security"
  service_routes_dir = services_dir / "routes"
  service_data_dir = services_dir / "data"
  modules_dir = app_root / "modules"
  workflows_dir = target_dir / "workflows"
  brand_dir = app_root / "brand"
  assets_dir = brand_dir / "assets"
  fonts_dir = brand_dir / "fonts"
  ui_dir = app_root / "ui"
  ui_components_dir = ui_dir / "components"
  ui_pages_dir = ui_dir / "pages"
  ui_routes_dir = ui_dir / "routes"

  for directory in (
    app_root,
    config_dir,
    control_plane_config_dir,
    services_dir,
    service_integrations_dir,
    service_adapters_dir,
    service_security_dir,
    service_routes_dir,
    service_data_dir,
    *(service_adapters_dir / area for area in (
      "auth",
      "source_control",
      "deployment",
      "dns",
      "registrar",
      "cloud",
      "storage",
      "secrets",
      "payments",
    )),
    modules_dir,
    workflows_dir,
    brand_dir,
    assets_dir,
    fonts_dir,
    ui_dir,
    ui_components_dir,
    ui_pages_dir,
    ui_routes_dir,
  ):
    directory.mkdir(parents=True, exist_ok=True)

  print("Created scaffold directories: app/config, control_plane/config, app/services, app/modules, workflows, app/ui, app/brand")

  features = TIER_PRESETS[preset]
  resolved_admin = admin_email.strip().lower() if isinstance(admin_email, str) and admin_email.strip() else None
  admins = [resolved_admin] if resolved_admin else []
  app_json = {
    "appName": app_name,
    "preset": preset,
    "startup": {
      "landing_spot": "/",
    },
    "targets": {
      "web": True,
      "mobile": False,
    },
    "authRequired": features.get("auth", False),
    "admins": admins,
  }
  _write_json(app_root / "app.json", app_json)
  print(f"Created app/app.json (preset={preset})")

  _write_json(config_dir / "ai.json", _build_ai_config(app_name, starter=starter))
  print("Created app/config/ai.json")

  _write_text(
    control_plane_config_dir / "runtime.yaml",
    yaml.safe_dump(build_default_control_plane_config(), sort_keys=False, allow_unicode=False),
  )
  print("Created control_plane/config/runtime.yaml")

  _write_json(config_dir / "shell.json", _build_shell_config(app_name))
  print("Created app/config/shell.json")

  _write_text(config_dir / "secrets.yaml", _secrets_yaml_placeholder())
  print("Created app/config/secrets.yaml")

  _write_json(config_dir / "data.json", _data_contract_placeholder())
  print("Created app/config/data.json")

  _write_service_support_stubs(services_dir)
  print("Created app/services support stubs")

  _write_text(service_data_dir / "__init__.py", '"""Data contract helpers declared by app/config/data.json."""\n')
  print("Created app/services/data/")

  _copy_default_brand_bundle(brand_dir, app_name)
  print("Created app/brand from factory_app default brand")

  _write_json(ui_dir / "route_manifest.json", {"pages": []})
  print("Created app/ui/route_manifest.json")

  _write_text(ui_dir / "index.js", _ui_component_registry_index())
  print("Created app/ui/index.js")

  _write_text(modules_dir / "README.md", _modules_stub_readme())
  _write_text(workflows_dir / "README.md", _workflows_stub_readme())
  _write_text(ui_pages_dir / "README.md", _pages_stub_readme())

  if starter:
    _create_starter_workflow(workflows_dir)


def _create_standard_consumer_files(
    *,
    target_dir: Path,
    scripts_dir: Path,
    app_name: str,
    preset: str,
) -> None:
    """Create root-level files that make a generated app runnable on the PyPI package."""
    scripts_dir.mkdir(parents=True, exist_ok=True)

    _write_text(target_dir / "requirements.txt", _requirements_txt())
    _write_text(target_dir / ".env.example", _env_example())
    _write_text(target_dir / ".gitignore", _generated_app_gitignore())
    _write_text(target_dir / "README.md", _generated_app_readme(app_name, preset))
    _write_text(scripts_dir / "run-backend.ps1", _run_backend_ps1())
    _write_text(scripts_dir / "run-frontend.ps1", _run_frontend_ps1())
    _write_text(scripts_dir / "run-studio.ps1", _run_studio_ps1())
    _create_agent_guidance_scaffold(target_dir=target_dir, app_name=app_name, preset=preset)

    print("Created root consumer files: requirements.txt, .env.example, README.md, AGENTS.md, CLAUDE.md, scripts/, .claude/")


def _create_agent_guidance_scaffold(*, target_dir: Path, app_name: str, preset: str) -> None:
    """Create app-local coding-agent guidance without copying framework-internal rules."""
    for relative_path, content in build_agent_guidance_files(app_name, preset).items():
        path = target_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_text(path, content)


def _requirements_txt() -> str:
    return f"""\
# Mozaiks framework — version pin for standalone installs and deploys.
# If you already have mozaiks installed, pip will skip this line.
mozaiks=={_current_mozaiks_version()}

# App-specific dependencies — add packages your modules and integrations need.
# Examples:
#   stripe>=8.0.0
#   boto3>=1.34.0
#   azure-identity>=1.16.0
#   azure-keyvault-secrets>=4.8.0
#   sendgrid>=6.11.0
#   twilio>=9.0.0
#   PyNaCl>=1.5.0
"""


def _current_mozaiks_version() -> str:
    version_file = Path(__file__).resolve().parents[2] / "mozaiksai" / "version.py"
    module = ast.parse(version_file.read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    raise RuntimeError(f"Unable to resolve Mozaiks package version from {version_file}")


def _env_example() -> str:
    return """# Mozaiks runtime
OPENAI_API_KEY=
MONGO_URI=mongodb://localhost:27017/mozaiks
ENV=development
AUTH_ENABLED=false
"""


def _generated_app_gitignore() -> str:
    return """.venv/
.env
__pycache__/
*.py[cod]
node_modules/
dist/
build/
!app/services/
!app/services/**
app/services/**/__pycache__/
app/services/**/*.py[cod]
logs/
generated/
.pytest_cache/
"""


def _generated_app_readme(app_name: str, preset: str) -> str:
    return f"""# {app_name}

This app was created with Mozaiks using the `{preset}` preset.

## Standalone Workspace Setup

Use this setup when this app workspace is being developed as its own repo.
The `.venv` belongs inside this workspace.

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `MONGO_URI` before running Studio. Set `OPENAI_API_KEY` before running real workflows.

## Run Studio

```powershell
.\\scripts\\run-studio.ps1 -ForceStop
```

Equivalent package command:

```powershell
python -m mozaiks studio --dir . --open
```

## Two-Terminal Mode

```powershell
# Terminal 1
.\\scripts\\run-backend.ps1 -ForceStop

# Terminal 2
.\\scripts\\run-frontend.ps1 -ForceStop
```

The local scripts run this app against the installed `mozaiks` package. They do
not require a sibling checkout of the framework repository.

## Coding Agent Guidance

This workspace includes app-local guidance for coding agents:

- `AGENTS.md`
- `CLAUDE.md`
- `.claude/rules/`
- `.claude/skills/`

Those files describe this generated app boundary. They intentionally do not copy
the full Mozaiks framework repository rules.

Mozaiks refreshes managed guidance blocks automatically when workspace commands
run after an installed package upgrade. To inspect guidance status manually:

```powershell
mozaiks sync-agent-guidance --dir . --check
```

Manual repair options:

```powershell
mozaiks sync-agent-guidance --dir . --write-missing
mozaiks sync-agent-guidance --dir . --update
```

`--update` refreshes Mozaiks managed blocks only. Use `--force` only when you
explicitly want to overwrite app-local guidance files.
"""


def _run_backend_ps1() -> str:
    return r"""<#
.SYNOPSIS
  Start the Mozaiks backend for this app workspace.

.DESCRIPTION
  Runs the backend from the installed mozaiks package and points it at this
  workspace.
#>

param(
  [int]$Port = 8000,
  [string]$BindHost = "0.0.0.0",
  [string]$WorkspacePath = "",
  [switch]$ForceStop,
  [switch]$Reload,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if ($WorkspacePath) {
  $Workspace = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $WorkspacePath))
} else {
  $Workspace = $RepoRoot
}

function Resolve-Python {
  $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }
  return "python"
}

function Get-ListeningProcessInfo {
  param([int]$LocalPort)

  $procIds = @()
  try {
    $procIds = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    $lines = netstat -ano | Select-String ":$LocalPort\s+.*LISTENING"
    $procIds = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
  }

  $results = @()
  foreach ($procId in $procIds) {
    if (-not $procId -or $procId -eq 0) { continue }
    try {
      $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId"
      $results += [PSCustomObject]@{
        ProcessId = [int]$procId
        Name = $proc.Name
        CommandLine = $proc.CommandLine
      }
    } catch {
    }
  }
  return $results
}

function Confirm-PortAvailable {
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
      Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
      Write-Host ("  Stopped PID {0}" -f $_.ProcessId) -ForegroundColor Green
    }
    Start-Sleep -Milliseconds 350
    return
  }

  throw "Port $LocalPort is busy. Rerun with -ForceStop or choose another -Port."
}

if (-not (Test-Path -LiteralPath (Join-Path $Workspace "app\app.json"))) {
  throw "No Mozaiks app bundle found at $Workspace. Expected app\app.json."
}

$pythonCmd = Resolve-Python
Set-Location $Workspace

# Keep generated apps on packaged resources by default.
Remove-Item Env:MOZAIKS_FACTORY_APP_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_WEB_SHELL_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_CHAT_UI_PATH -ErrorAction SilentlyContinue

$env:MOZAIKS_APP_WORKSPACE_PATH = $Workspace
$env:PLATFORM_PATH = $Workspace
$env:MOZAIKS_HOST = "platform"
$env:MOZAIKS_GENERATED_ARTIFACTS_PATH = (Join-Path $Workspace "generated")

$uvicornArgs = @(
  "-m",
  "uvicorn",
  "mozaiksai.hosts.platform:app",
  "--host",
  $BindHost,
  "--port",
  [string]$Port
)
if ($Reload) {
  $uvicornArgs += "--reload"
}

Write-Host "[backend] Workspace: $Workspace" -ForegroundColor DarkCyan
Write-Host "[backend] Command: $pythonCmd $($uvicornArgs -join ' ')" -ForegroundColor Cyan

if ($DryRun) {
  return
}

Confirm-PortAvailable -LocalPort $Port -KillExisting:$ForceStop
& $pythonCmd @uvicornArgs
"""


def _run_frontend_ps1() -> str:
    return r"""<#
.SYNOPSIS
  Start the packaged Mozaiks web shell for this app workspace.

.DESCRIPTION
  Resolves web_shell from the installed mozaiks package, then runs the Vite dev
  server with PLATFORM_PATH pointed at this workspace.
#>

param(
  [int]$Port = 3000,
  [string]$BindHost = "0.0.0.0",
  [string]$BackendUrl = "http://localhost:8000",
  [string]$WorkspacePath = "",
  [switch]$ForceStop,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if ($WorkspacePath) {
  $Workspace = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $WorkspacePath))
} else {
  $Workspace = $RepoRoot
}

function Resolve-Python {
  $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }
  return "python"
}

function Get-ListeningProcessInfo {
  param([int]$LocalPort)

  $procIds = @()
  try {
    $procIds = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction Stop |
      Select-Object -ExpandProperty OwningProcess -Unique
  } catch {
    $lines = netstat -ano | Select-String ":$LocalPort\s+.*LISTENING"
    $procIds = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
  }

  $results = @()
  foreach ($procId in $procIds) {
    if (-not $procId -or $procId -eq 0) { continue }
    try {
      $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId"
      $results += [PSCustomObject]@{
        ProcessId = [int]$procId
        Name = $proc.Name
        CommandLine = $proc.CommandLine
      }
    } catch {
    }
  }
  return $results
}

function Confirm-PortAvailable {
  param(
    [int]$LocalPort,
    [switch]$KillExisting
  )

  $listeners = Get-ListeningProcessInfo -LocalPort $LocalPort
  if (-not $listeners -or $listeners.Count -eq 0) {
    return
  }

  Write-Host "[frontend] Port $LocalPort is already in use by:" -ForegroundColor Yellow
  $listeners | ForEach-Object {
    Write-Host ("  PID {0} [{1}] {2}" -f $_.ProcessId, $_.Name, $_.CommandLine) -ForegroundColor DarkYellow
  }

  if ($KillExisting) {
    Write-Host "[frontend] ForceStop enabled - terminating existing listeners on port $LocalPort..." -ForegroundColor Yellow
    $listeners | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
      Write-Host ("  Stopped PID {0}" -f $_.ProcessId) -ForegroundColor Green
    }
    Start-Sleep -Milliseconds 350
    return
  }

  throw "Port $LocalPort is busy. Rerun with -ForceStop or choose another -Port."
}

if (-not (Test-Path -LiteralPath (Join-Path $Workspace "app\app.json"))) {
  throw "No Mozaiks app bundle found at $Workspace. Expected app\app.json."
}

$pythonCmd = Resolve-Python

# Keep generated apps on packaged resources by default.
Remove-Item Env:MOZAIKS_FACTORY_APP_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_WEB_SHELL_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_CHAT_UI_PATH -ErrorAction SilentlyContinue

$webShellRoot = & $pythonCmd -c "from mozaiksai.resources import resolve_web_shell_root; p = resolve_web_shell_root(); print(p or '')"
$webShellRoot = ($webShellRoot | Select-Object -Last 1).Trim()
if (-not $webShellRoot -or -not (Test-Path -LiteralPath (Join-Path $webShellRoot "package.json"))) {
  throw "Could not resolve packaged web_shell. Run: python -m pip install -r requirements.txt"
}

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) {
  throw "npm is required to start the frontend."
}

$env:MOZAIKS_APP_WORKSPACE_PATH = $Workspace
$env:PLATFORM_PATH = $Workspace
$env:MOZAIKS_HOST = "platform"
$env:VITE_API_URL = $BackendUrl
$env:MOZAIKS_GENERATED_ARTIFACTS_PATH = (Join-Path $Workspace "generated")

Write-Host "[frontend] Workspace: $Workspace" -ForegroundColor DarkCyan
Write-Host "[frontend] Web shell: $webShellRoot" -ForegroundColor DarkCyan
Write-Host "[frontend] Backend URL: $BackendUrl" -ForegroundColor DarkCyan
Write-Host "[frontend] Command: npm --prefix `"$webShellRoot`" run dev -- --host $BindHost --port $Port --strictPort" -ForegroundColor Cyan
Write-Host "[frontend] Open: http://localhost:$Port" -ForegroundColor Yellow

if ($DryRun) {
  return
}

Confirm-PortAvailable -LocalPort $Port -KillExisting:$ForceStop

if (-not (Test-Path -LiteralPath (Join-Path $webShellRoot "node_modules"))) {
  Write-Host "[frontend] Installing packaged web_shell dependencies..." -ForegroundColor Cyan
  npm --prefix $webShellRoot install
}

npm --prefix $webShellRoot run dev -- --host $BindHost --port $Port --strictPort
"""


def _run_studio_ps1() -> str:
    return r"""<#
.SYNOPSIS
  Start the full Mozaiks Studio stack for this app workspace.
#>

param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000,
  [string]$WorkspacePath = "",
  [switch]$ForceStop,
  [switch]$NoBrowser,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
if ($WorkspacePath) {
  $Workspace = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $WorkspacePath))
} else {
  $Workspace = $RepoRoot
}

function Resolve-Mozaiks {
  $venvMozaiks = Join-Path $RepoRoot ".venv\Scripts\mozaiks.exe"
  if (Test-Path -LiteralPath $venvMozaiks) {
    return $venvMozaiks
  }
  return "mozaiks"
}

function Stop-Listeners {
  param([int[]]$Ports)

  foreach ($port in $Ports) {
    $procIds = @()
    try {
      $procIds = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction Stop |
        Select-Object -ExpandProperty OwningProcess -Unique
    } catch {
      $lines = netstat -ano | Select-String ":$port\s+.*LISTENING"
      $procIds = $lines | ForEach-Object { ($_ -split '\s+')[-1] } | Sort-Object -Unique
    }

    foreach ($procId in $procIds) {
      if (-not $procId -or $procId -eq 0) { continue }
      Write-Host "[studio] ForceStop: stopping PID $procId on port $port" -ForegroundColor Yellow
      Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop
    }
  }
}

if (-not (Test-Path -LiteralPath (Join-Path $Workspace "app\app.json"))) {
  throw "No Mozaiks app bundle found at $Workspace. Expected app\app.json."
}

# Keep generated apps on packaged resources by default.
Remove-Item Env:MOZAIKS_FACTORY_APP_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_WEB_SHELL_PATH -ErrorAction SilentlyContinue
Remove-Item Env:MOZAIKS_CHAT_UI_PATH -ErrorAction SilentlyContinue

$mozaiksCmd = Resolve-Mozaiks
$argsList = @(
  "studio",
  "--dir",
  $Workspace,
  "--backend-port",
  [string]$BackendPort,
  "--frontend-port",
  [string]$FrontendPort
)
if ($NoBrowser) {
  $argsList += "--no-browser"
} else {
  $argsList += "--open"
}

Write-Host "[studio] Workspace: $Workspace" -ForegroundColor DarkCyan
Write-Host "[studio] Command: $mozaiksCmd $($argsList -join ' ')" -ForegroundColor Cyan

if ($DryRun) {
  return
}

if ($ForceStop) {
  Stop-Listeners -Ports @($BackendPort, $FrontendPort)
}

& $mozaiksCmd @argsList
"""


def _load_factory_ai_config() -> dict:
    factory_root = resolve_factory_app_root()
    if factory_root is None:
        raise FileNotFoundError("Unable to resolve the packaged factory_app root.")

    config_path = factory_root / "app" / "config" / "ai.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Factory AI config not found: {config_path}")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Factory AI config must be a JSON object: {config_path}")
    return payload


def _load_factory_control_plane_runtime_config() -> dict:
    factory_root = resolve_factory_app_root()
    if factory_root is None:
        raise FileNotFoundError("Unable to resolve the packaged factory_app root.")

    config_path = factory_root / "control_plane" / "config" / "runtime.yaml"
    if not config_path.exists():
        return {}

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Factory control-plane runtime config must be a YAML object: {config_path}")
    return payload


def _load_factory_shell_config() -> dict:
    factory_root = resolve_factory_app_root()
    if factory_root is None:
        raise FileNotFoundError("Unable to resolve the packaged factory_app root.")

    config_path = factory_root / "app" / "config" / "shell.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Factory shell config not found: {config_path}")

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Factory shell config must be a JSON object: {config_path}")
    return payload


def build_default_ai_config(app_name: str, *, starter: bool = False) -> dict:
  """Build the canonical app-level AI config from the first-party Studio contract."""
  factory_ai = _load_factory_ai_config()
  ask = deepcopy(factory_ai.get("ask") or {})
  chat = deepcopy(factory_ai.get("chat") or {})
  workflows = deepcopy(factory_ai.get("workflows") or {})

  ask.setdefault(
    "ask_mode_prompt",
    (
      f"You are the Mozaiks assistant for {app_name}. Help users shape, generate, "
      "connect, and refine apps in Mozaiks Studio using the shared builder workflows."
    ),
  )
  ask.setdefault("ask_context_variables", None)

  chat["chat_startup_mode"] = "workflow" if starter else (chat.get("chat_startup_mode") or "ask")
  workflows["entry_point"] = "HelloWorkflow" if starter else (workflows.get("entry_point") or "ValueEngine")
  workflows.setdefault("resume_policy", "last_active_then_oldest_then_entry_point")

  return {
    "ask": ask,
    "chat": chat,
    "workflows": workflows,
  }


def _build_ai_config(app_name: str, *, starter: bool) -> dict:
    return build_default_ai_config(app_name, starter=starter)


def build_default_control_plane_config() -> dict:
    return deepcopy(_load_factory_control_plane_runtime_config())


def build_default_shell_config(app_name: str) -> dict:
    """Build the canonical app-level shell config from the first-party Studio contract."""
    factory_shell = _load_factory_shell_config()
    shell_config = deepcopy(factory_shell)
    logo = shell_config.get("header", {}).get("logo")
    if isinstance(logo, dict):
        logo["alt"] = f"{app_name} logo"
    return shell_config


def _build_shell_config(app_name: str) -> dict:
    return build_default_shell_config(app_name)


def _resolve_default_brand_template_dir() -> Path:
    resolved = resolve_factory_brand_root()
    if resolved is not None:
        return resolved
    return (Path(__file__).resolve().parents[2] / "factory_app" / "app" / "brand").resolve()


def _load_default_brand_theme_config(app_name: str) -> dict:
    template_path = _resolve_default_brand_template_dir() / "theme_config.json"
    if not template_path.exists():
        raise FileNotFoundError(f"Default brand template not found: {template_path}")

    payload = json.loads(template_path.read_text(encoding="utf-8"))

    identity = payload.get("identity")
    if isinstance(identity, dict):
        identity["name"] = app_name
        identity["app_name"] = app_name

    theme = payload.get("theme")
    if isinstance(theme, dict):
        branding = theme.get("branding")
        if isinstance(branding, dict):
            branding["app_name"] = app_name

    return payload


def _copy_default_brand_bundle(brand_dir: Path, app_name: str) -> None:
    template_dir = _resolve_default_brand_template_dir()
    if not template_dir.exists():
        raise FileNotFoundError(f"Default brand bundle not found: {template_dir}")

    for child in template_dir.iterdir():
        destination = brand_dir / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)

    _write_json(brand_dir / "theme_config.json", _load_default_brand_theme_config(app_name))


def _secrets_yaml_placeholder() -> str:
    return """\
# app/config/secrets.yaml — names-only secret management contract.
# Declare secret names, env handles, and provider policy here.
# NEVER store raw API keys, tokens, passwords, connection strings, or
# private keys in this file. Use environment variables or a vault provider.
#
# provider: env   # env | vault | aws_secrets_manager | azure_key_vault | gcp_secret_manager
# secrets:
#   - name: OPENAI_API_KEY
#     env_handle: OPENAI_API_KEY
#     description: "OpenAI API key for AI workflows"
#   - name: MONGO_URI
#     env_handle: MONGO_URI
#     description: "MongoDB connection URI"
provider: env
secrets: []
"""


def _data_contract_placeholder() -> dict:
    return {
        "enabled": False,
        "description": (
            "Data contract for durable module collections, cross-module aggregate ownership, "
            "or explicit existing database mappings."
        ),
        "version": "1",
        "surfaces": [],
        "shared_collections": [],
    }


def _write_service_support_stubs(services_dir: Path) -> None:
    _write_text(services_dir / "__init__.py", '"""App-owned service support code."""')
    _write_text(services_dir / "config.py", '"""App-owned service support configuration."""')
    for package in (
        services_dir / "integrations",
        services_dir / "adapters",
        services_dir / "security",
        services_dir / "routes",
        services_dir / "data",
    ):
        _write_text(package / "__init__.py", "")
    for area in (
        "auth",
        "source_control",
        "deployment",
        "dns",
        "registrar",
        "cloud",
        "storage",
        "secrets",
        "payments",
    ):
        _write_text(services_dir / "adapters" / area / "__init__.py", "")


def _create_starter_workflow(workflows_dir: Path) -> None:
    """Create an explicit starter workflow for users who ask for example content."""
    workflow_dir = workflows_dir / "HelloWorkflow"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "tools").mkdir(exist_ok=True)
    (workflow_dir / "ui").mkdir(exist_ok=True)

    orchestrator = """workflow_name: HelloWorkflow
max_turns: 10
human_in_the_loop: true
workflow_startup_mode: UserDriven
orchestration_pattern: ag2_network
initial_message_to_user: "Hello. I am a starter workflow. Replace me when you know the real product behavior."
initial_message: null
initial_agent: GreeterAgent
triggers:
  - type: chat
    description: Start via chat transport
"""

    agents_yaml = """agents:
  - name: GreeterAgent
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: "You are a starter assistant for a newly initialized Mozaiks app bundle."
      - id: instructions
        heading: "[INSTRUCTIONS]"
        content: |
          Greet the user.
          Explain that this workflow is only starter content.
          Ask what real workflow should replace it.
    max_consecutive_auto_reply: 5
    structured_outputs_required: false
"""

    handoffs_yaml = """handoff_rules:
  - source_agent: user
    target_agent: GreeterAgent
    handoff_type: condition
    condition_type: string_llm
    condition: "When the user starts the conversation or asks what this app can do."
    transition_target: AgentTarget
  - source_agent: GreeterAgent
    target_agent: user
    handoff_type: after_work
    transition_target: RevertToUserTarget
"""

    context_variables_yaml = """definitions: {}
agents:
  GreeterAgent:
    variables: []
"""

    structured_outputs_yaml = """registry: {}
models: {}
"""

    tools_yaml = """tools: []
lifecycle_tools: []
"""

    ui_config_yaml = """visual_agents:
  - GreeterAgent
chat_pane_agents:
  - GreeterAgent
artifact_agents: []
"""

    hooks_yaml = """hooks: []
"""

    workflow_files = {
        "orchestrator.yaml": orchestrator,
        "agents.yaml": agents_yaml,
        "handoffs.yaml": handoffs_yaml,
        "context_variables.yaml": context_variables_yaml,
        "structured_outputs.yaml": structured_outputs_yaml,
        "tools.yaml": tools_yaml,
        "ui_config.yaml": ui_config_yaml,
        "hooks.yaml": hooks_yaml,
    }
    for filename, content in workflow_files.items():
        _write_text(workflow_dir / filename, content)

    print("Created workflows/HelloWorkflow/")


def _show_next_steps(target_dir: Path, preset: str, starter: bool) -> None:
    features = TIER_PRESETS[preset]
    print("\nNext Steps:")
    print("Standalone workspace setup:")
    print(f"  1. cd {target_dir}")
    print("  2. python -m venv .venv")
    print("  3. .\\.venv\\Scripts\\Activate.ps1")
    print("  4. python -m pip install -r requirements.txt")
    print("  5. Copy-Item .env.example .env, then set OPENAI_API_KEY and MONGO_URI")
    print("  6. Open Studio: .\\scripts\\run-studio.ps1 -ForceStop")
    if starter:
        print("  7. Replace workflows/HelloWorkflow only after you confirm the real product behavior")
    else:
        print("  7. Use Studio to generate the first real workflows/modules instead of hand-populating the scaffold")
    print("  8. Optional package command: python -m mozaiks studio --dir . --open")
    if features.get("admin"):
        print("  9. Confirm admin access in app/app.json admins")

    print("\nTo add more features later: mozaiks add <feature>")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _modules_stub_readme() -> str:
    return """# Modules Stub

Add deterministic capabilities here when the app actually needs them.

Each module lives under `app/modules/<name>/` and typically includes:

- `module.yaml`
- `contracts/events.yaml`
- `contracts/reactions.yaml`
- `contracts/settings.yaml`
- `contracts/notifications.yaml`
- `contracts/admin.yaml`
- `backend/handler.py`

Do not create modules until you know the real CRUD or action surface.
"""


def _workflows_stub_readme() -> str:
    return """# Workflows Stub

Add AI workflows here once you have real product context.

Each workflow lives under `workflows/<WorkflowName>/` and usually includes:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`

Use `mozaiks gen workflow` only after you know what the workflow is supposed to do.
"""


def _pages_stub_readme() -> str:
    return """# Pages Stub

Persistent product pages belong here when the app needs them.

Prefer declarative page schemas such as `ui/pages/<name>.yaml` or `ui/pages/<name>/page.yaml`.
Custom full-page React routes belong in `ui/pages/custom/` and are mounted only through `ui/route_manifest.json`.
"""


def _ui_component_registry_index() -> str:
    return """import { registerAdminComponents } from '@mozaiks/factory-admin'

export function register(registerComponent) {
  // Studio management components — always registered so the /apps dashboard
  // and admin portal are available when running in Studio mode.
  registerAdminComponents(registerComponent)

  // Register app-specific custom React surfaces here when declarative config
  // is not enough. Use app/ui/pages/custom/ for full-page custom routes.
}
"""


