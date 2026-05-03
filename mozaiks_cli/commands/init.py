"""mozaiks init - Initialize an app workspace from the Dev/CLI layer."""

import json
import re
import sys
import shutil
from pathlib import Path

from mozaiksai.core.admin.contract import build_default_host_admin_config
from mozaiksai.resources import resolve_factory_brand_root

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
    _create_bundle_scaffold(
        target_dir=target_dir,
        preset=preset,
        app_name=app_name,
        admin_email=admin_email,
        starter=starter,
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
    print("(Press Enter to skip and set it later in app/config/admin.json)\n")
    try:
        email = input("Admin email: ").strip()
    except (EOFError, KeyboardInterrupt):
        email = ""
    if not email:
        print("Skipped — update admin_emails in app/config/admin.json before deploying.\n")
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
    modules_dir = app_root / "modules"
    workflows_dir = app_root / "workflows"
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

    print("Created scaffold directories: app/config, app/modules, app/workflows, app/ui, app/brand")

    features = TIER_PRESETS[preset]
    admins = [admin_email] if admin_email else []
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

    _write_json(config_dir / "shell.json", _build_shell_config(app_name))
    print("Created app/config/shell.json")

    if features.get("admin"):
        resolved_email = admin_email or "admin@example.com"
        admin_config = build_default_host_admin_config()
        admin_config["_comment"] = "Host-owned admin shell config. Feature admin panels belong in modules/{module}/admin.yaml."
        admin_config["admin_emails"] = [resolved_email]
        admin_json_path = config_dir / "admin.json"
        _write_json(admin_json_path, admin_config)
        print(f"Created app/config/admin.json (admin: {resolved_email})")

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


def _build_ai_config(app_name: str, *, starter: bool) -> dict:
    return {
        "ask": {
            "ask_mode_prompt": (
                f"You are the assistant for {app_name}. Help the app builder clarify what "
                "workflows, modules, and pages they actually need before generating them."
            ),
            "ask_context_variables": None,
        },
        "chat": {
            "chat_startup_mode": "workflow" if starter else "ask",
        },
        "workflows": {
            "entry_point": "HelloWorkflow" if starter else None,
            "resume_policy": "last_active_then_oldest_then_entry_point",
        },
    }


def _build_shell_config(app_name: str) -> dict:
    return {
        "header": {
            "logo": {
                "src": None,
                "wordmark": None,
                "alt": f"{app_name} logo",
                "href": "/",
            },
            "pages": [],
            "actions": [],
        },
        "profile": {
            "show": False,
            "menu": [],
        },
        "notifications": {
            "show": False,
            "emptyText": "No notifications yet",
        },
        "footer": {
            "links": [],
            "visible": True,
        },
    }


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
orchestration_pattern: AutoPattern
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

    print("Created app/workflows/HelloWorkflow/")


def _show_next_steps(target_dir: Path, preset: str, starter: bool) -> None:
    features = TIER_PRESETS[preset]
    app_root = target_dir / "app"
    print("\nNext Steps:")
    print(f"  1. cd {target_dir}")
    print("  2. Run onboarding: mozaiks onboard --dir .")
    print("  3. Set OPENAI_API_KEY and MONGO_URI in your environment (or a .env file)")
    print("  4. Open Studio: mozaiks studio --dir . --open")
    if starter:
        print("  5. Replace app/workflows/HelloWorkflow only after you confirm the real product behavior")
    else:
        print("  5. Use Studio to generate the first real workflows/modules instead of hand-populating the scaffold")
    print("  6. Optional: use mozaiks gen once you have real product context")
    if features.get("admin"):
        print("  7. Confirm admin access in app/config/admin.json and app/app.json admins")

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
- `events.yaml`
- `settings.yaml`
- `notifications.yaml`
- `subscriptions.yaml`
- `admin.yaml`
- `backend/handler.py`

Do not create modules until you know the real CRUD or action surface.
"""


def _workflows_stub_readme() -> str:
    return """# Workflows Stub

Add AI workflows here once you have real product context.

Each workflow lives under `app/workflows/<WorkflowName>/` and usually includes:

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
    return """export function register() {
  // Register custom React surfaces here only when declarative config is not enough.
}
"""


