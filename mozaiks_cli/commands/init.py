"""
mozaiks init - Initialize new Mozaiks project.
"""

import json
import os
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
    app_name = args.name
    target_dir = Path(args.directory)

    # Validate preset
    if preset not in TIER_PRESETS:
        print(f"Error: Unknown preset '{preset}'")
        print(f"Available: {', '.join(TIER_PRESETS.keys())}")
        return

    # Create target directory if needed
    if not target_dir.exists():
        target_dir.mkdir(parents=True)

    platform_dir = target_dir / "platform"

    # Check if platform/ already exists
    if platform_dir.exists():
        print(f"Error: platform/ already exists in {target_dir}")
        print("Use 'mozaiks add' to modify an existing project.")
        return

    print(f"Initializing Mozaiks project: {app_name}")
    print(f"Preset: {preset}")
    print(f"Target: {target_dir}\n")

    # Collect admin email when the preset includes admin portal
    admin_email = None
    if TIER_PRESETS[preset].get("admin"):
        admin_email = _prompt_admin_email()

    # Create platform structure
    _create_platform_structure(platform_dir, preset, app_name, admin_email)

    # Create minimal workflow example
    _create_minimal_workflow(platform_dir, preset)

    # Create page example if chat_ui enabled
    features = TIER_PRESETS[preset]
    if features.get("chat_ui"):
        _create_minimal_page(platform_dir)

    features = TIER_PRESETS[preset]
    print("\nProject initialized successfully.")
    print("\nNext Steps:")
    print(f"  1. cd {target_dir}")
    print("  2. Set up environment variables (.env)")
    print("  3. Run: python run_server.py")
    if features.get("admin"):
        print(f"\n  Admin portal: http://localhost:8000/admin")
        print(f"  Log in with the email you configured in platform/config/admin.json")
    print(f"\nTo add more features: mozaiks add <feature>")


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


def _create_platform_structure(platform_dir: Path, preset: str, app_name: str, admin_email: str = None):
    """Create basic platform/ directory structure."""
    # Create directories
    platform_dir.mkdir(parents=True, exist_ok=True)
    (platform_dir / "workflows").mkdir(exist_ok=True)
    (platform_dir / "modules").mkdir(exist_ok=True)
    (platform_dir / "pages").mkdir(exist_ok=True)
    (platform_dir / "config").mkdir(exist_ok=True)

    # Create platform/app.json
    features = TIER_PRESETS[preset]
    app_json = {
        "appName": app_name,
        "preset": preset,
        "targets": {
            "web": True,
            "mobile": False,
        },
        "authRequired": features.get("auth", False),
    }

    app_json_path = platform_dir / "app.json"
    with open(app_json_path, "w", encoding="utf-8") as f:
        json.dump(app_json, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Created platform/app.json (preset={preset})")

    # Create platform/config/admin.json when preset includes admin portal
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
        admin_json_path = platform_dir / "config" / "admin.json"
        with open(admin_json_path, "w", encoding="utf-8") as f:
            json.dump(admin_config, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"Created platform/config/admin.json (admin: {resolved_email})")


def _create_minimal_workflow(platform_dir: Path, preset: str):
    """Create a minimal example workflow."""
    workflow_dir = platform_dir / "workflows" / "HelloWorkflow"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "tools").mkdir(exist_ok=True)
    (workflow_dir / "ui").mkdir(exist_ok=True)

    # orchestrator.yaml
    orchestrator = """workflow_name: HelloWorkflow
max_turns: 10
human_in_the_loop: true
workflow_startup_mode: UserDriven
orchestration_pattern: AutoPattern
initial_message_to_user: "Hello! I'm a friendly greeter. Say hello and I'll respond warmly."
initial_message: null
initial_agent: GreeterAgent
triggers:
  - type: chat
    description: Start via chat transport
"""

    with open(workflow_dir / "orchestrator.yaml", "w", encoding="utf-8") as f:
        f.write(orchestrator)

    agents_yaml = """agents:
  - name: GreeterAgent
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: "You are a friendly greeting assistant."
      - id: instructions
        heading: "[INSTRUCTIONS]"
        content: |
          Greet the user warmly.
          Ask one clarifying question before giving suggestions.
    max_consecutive_auto_reply: 5
    structured_outputs_required: false
"""

    handoffs_yaml = """handoff_rules:
  - source_agent: user
    target_agent: GreeterAgent
    handoff_type: condition
    condition_type: string_llm
    condition: "When the user asks for help or starts the conversation."
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
        "agents.yaml": agents_yaml,
        "handoffs.yaml": handoffs_yaml,
        "context_variables.yaml": context_variables_yaml,
        "structured_outputs.yaml": structured_outputs_yaml,
        "tools.yaml": tools_yaml,
        "ui_config.yaml": ui_config_yaml,
        "hooks.yaml": hooks_yaml,
    }
    for filename, content in workflow_files.items():
        (workflow_dir / filename).write_text(content, encoding="utf-8")

    sample_tool = """def greet_user(context_variables=None):
    return {"status": "ok"}
"""
    (workflow_dir / "tools" / "greet_user.py").write_text(sample_tool, encoding="utf-8")

    print(f"Created platform/workflows/HelloWorkflow/")


def _create_minimal_page(platform_dir: Path):
    """Create a minimal example page (for chat_ui tier)."""
    page_dir = platform_dir / "pages" / "home"
    page_dir.mkdir(parents=True, exist_ok=True)
    ui_dir = page_dir / "ui"
    ui_dir.mkdir(exist_ok=True)

    # page.json
    page_json = {
        "name": "Home",
        "path": "/home",
        "title": "Home",
        "icon": "home",
        "description": "Home page",
    }

    with open(page_dir / "page.json", "w", encoding="utf-8") as f:
        json.dump(page_json, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # ui/index.js
    ui_code = """import React from 'react';

export default function HomePage() {
  return (
    <div style={{ padding: '2rem' }}>
      <h1>Welcome to Your Mozaiks App</h1>
      <p>This is a minimal example page.</p>
      <p>Edit platform/pages/home/ui/index.js to customize this page.</p>
    </div>
  );
}
"""

    with open(ui_dir / "index.js", "w", encoding="utf-8") as f:
        f.write(ui_code)

    print(f"Created platform/pages/home/")
