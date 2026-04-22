"""
mozaiks init - Initialize a Mozaiks app bundle scaffold.
"""

import json
import re
import sys
from pathlib import Path

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
    platform_dir = target_dir / "platform"
    brand_dir = target_dir / "brand"
    ui_dir = target_dir / "ui"

    existing_surfaces = _existing_scaffold_surfaces(platform_dir, brand_dir, ui_dir)
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

    _create_bundle_scaffold(
        target_dir=target_dir,
        preset=preset,
        app_name=app_name,
        admin_email=admin_email,
        starter=starter,
    )

    print("\nProject initialized successfully.")
    _show_next_steps(target_dir, preset, starter)


def _existing_scaffold_surfaces(platform_dir: Path, brand_dir: Path, ui_dir: Path) -> list[str]:
    existing = []
    for path in (platform_dir, brand_dir, ui_dir):
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
    print("(Press Enter to skip and set it later in platform/config/admin.json)\n")
    try:
        email = input("Admin email: ").strip()
    except (EOFError, KeyboardInterrupt):
        email = ""
    if not email:
        print("Skipped — update admin_emails in platform/config/admin.json before deploying.\n")
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
    platform_dir = target_dir / "platform"
    config_dir = platform_dir / "config"
    modules_dir = platform_dir / "modules"
    workflows_dir = platform_dir / "workflows"
    pages_dir = platform_dir / "pages"
    brand_dir = target_dir / "brand"
    assets_dir = brand_dir / "assets"
    fonts_dir = brand_dir / "fonts"
    ui_dir = target_dir / "ui"
    ui_components_dir = ui_dir / "components"
    ui_pages_dir = ui_dir / "pages"

    for directory in (
        platform_dir,
        config_dir,
        modules_dir,
        workflows_dir,
        pages_dir,
        brand_dir,
        assets_dir,
        fonts_dir,
        ui_dir,
        ui_components_dir,
        ui_pages_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    print("Created scaffold directories: platform/, brand/, ui/")

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
    _write_json(platform_dir / "app.json", app_json)
    print(f"Created platform/app.json (preset={preset})")

    _write_json(config_dir / "ai.json", _build_ai_config(app_name, starter=starter))
    print("Created platform/config/ai.json")

    _write_json(config_dir / "shell.json", _build_shell_config(app_name))
    print("Created platform/config/shell.json")

    if features.get("admin"):
        resolved_email = admin_email or "admin@example.com"
        admin_config = {
            "_comment": "Admin portal config. Set admin_emails to the email(s) that should have admin access.",
            "enabled": True,
            "admin_emails": [resolved_email],
            "panels": ["stats", "runs", "sessions"],
            "roles": ["admin"],
            "features": {
                "user_management": False,
                "billing": False,
                "audit_log": False,
            },
        }
        admin_json_path = config_dir / "admin.json"
        _write_json(admin_json_path, admin_config)
        print(f"Created platform/config/admin.json (admin: {resolved_email})")

    _write_json(brand_dir / "theme_config.json", _build_theme_config(app_name))
    print("Created brand/theme_config.json")

    _write_text(assets_dir / "logo.svg", _default_logo_svg())
    print("Created brand/assets/logo.svg")

    _write_json(ui_dir / "extension.json", {"pages": []})
    print("Created ui/extension.json")

    _write_text(ui_dir / "index.js", _ui_extension_index())
    print("Created ui/index.js")

    _write_text(modules_dir / "README.md", _modules_stub_readme())
    _write_text(workflows_dir / "README.md", _workflows_stub_readme())
    _write_text(pages_dir / "README.md", _pages_stub_readme())

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
                "src": "/assets/logo.svg",
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


def _build_theme_config(app_name: str) -> dict:
    return {
        "_comment": (
            "Blank visual identity scaffold. Replace colors and assets when the product direction is clear."
        ),
        "theme": {
            "primary": "teal",
            "variant": "modern",
            "radius": "medium",
            "font": "system",
            "font_heading": "system",
            "appearance": "light",
            "density": "comfortable",
            "branding": {
                "app_name": app_name,
                "logo_url": "/assets/logo.svg",
            },
        },
        "identity": {
            "name": app_name,
            "tagline": "Mozaiks app bundle",
            "app_name": app_name,
        },
        "assets": {
            "logo": "logo.svg",
        },
        "colors": {
            "primary": {
                "main": "#0f766e",
                "light": "#14b8a6",
                "dark": "#115e59",
                "name": "teal",
            },
            "secondary": {
                "main": "#1d4ed8",
                "light": "#60a5fa",
                "dark": "#1e40af",
                "name": "blue",
            },
            "background": {
                "base": "#f8fafc",
                "surface": "#ffffff",
                "elevated": "#ffffff",
                "overlay": "rgba(15, 23, 42, 0.24)",
            },
            "border": {
                "subtle": "#dbeafe",
                "strong": "#93c5fd",
                "accent": "#0f766e",
            },
            "text": {
                "primary": "#0f172a",
                "secondary": "#334155",
                "muted": "#64748b",
                "onAccent": "#f8fafc",
            },
        },
        "shadows": {
            "primary": "0 20px 45px rgba(15, 118, 110, 0.18)",
            "elevated": "0 24px 60px rgba(15, 23, 42, 0.12)",
        },
        "ui": {
            "chat": {
                "modes": {
                    "ask": {
                        "tint": "#0f766e",
                        "label": "Ask",
                    },
                    "workflow": {
                        "tint": "#1d4ed8",
                        "label": "Workflow",
                    },
                },
            },
        },
    }


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

    print("Created platform/workflows/HelloWorkflow/")


def _show_next_steps(target_dir: Path, preset: str, starter: bool) -> None:
    features = TIER_PRESETS[preset]
    platform_path = target_dir / "platform"

    print("\nNext Steps:")
    print(f"  1. Point PLATFORM_PATH at {platform_path}")
    print("  2. Review platform/app.json, platform/config/ai.json, and brand/theme_config.json")
    if starter:
        print("  3. Replace platform/workflows/HelloWorkflow with real product workflows")
    else:
        print("  3. Add workflows in platform/workflows/ and modules in platform/modules/")
    print("  4. Optional: use mozaiks gen once you have real product context")
    if features.get("admin"):
        print("  5. Confirm admin access in platform/config/admin.json and app.json admins")

    print("\nTo add more features later: mozaiks add <feature>")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _modules_stub_readme() -> str:
    return """# Modules Stub

Add deterministic capabilities here when the app actually needs them.

Each module lives under `platform/modules/<name>/` and typically includes:

- `module.yaml`
- `handler.py`

Do not create modules until you know the real CRUD or action surface.
"""


def _workflows_stub_readme() -> str:
    return """# Workflows Stub

Add AI workflows here once you have real product context.

Each workflow lives under `platform/workflows/<WorkflowName>/` and usually includes:

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

Prefer declarative page schemas such as `pages/<name>.yaml` or `pages/<name>/page.yaml`.
Do not start with custom React pages unless the page contract actually requires it.
"""


def _ui_extension_index() -> str:
    return """export function register() {
  // Register custom React surfaces here only when declarative config is not enough.
}
"""


def _default_logo_svg() -> str:
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="Mozaiks logo placeholder">
  <defs>
    <linearGradient id="mozaiksGradient" x1="0%" x2="100%" y1="0%" y2="100%">
      <stop offset="0%" stop-color="#0f766e" />
      <stop offset="100%" stop-color="#1d4ed8" />
    </linearGradient>
  </defs>
  <rect width="128" height="128" rx="28" fill="url(#mozaiksGradient)" />
  <path d="M28 92V36h18l18 30 18-30h18v56H84V63L69 88H59L44 63v29Z" fill="#f8fafc" />
</svg>
"""
