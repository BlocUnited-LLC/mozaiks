"""
mozaiks add - Add feature to existing project.
"""

import json
from pathlib import Path

from mozaiks_cli.workspace import resolve_active_app_root

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
    """Execute the add command."""
    app_root = resolve_active_app_root(Path(".").resolve())
    app_root_label = app_root.name if app_root.name in {"app", "platform"} else "."
    app_json_path = app_root / "app.json"

    if not app_json_path.exists():
        print(f"Error: No {app_root_label}/app.json found.")
        print("Run 'mozaiks init <preset>' to create a new project first.")
        return

    try:
        with open(app_json_path, encoding="utf-8") as f:
            app_config = json.load(f)
    except Exception as e:
        print(f"Error reading {app_json_path}: {e}")
        return

    # Upgrade to preset
    if args.preset:
        if args.preset not in TIER_PRESETS:
            print(f"Error: Unknown preset '{args.preset}'")
            print(f"Available: {', '.join(TIER_PRESETS.keys())}")
            return

        app_config["preset"] = args.preset
        # Clear feature overrides when upgrading preset
        if "features" in app_config:
            del app_config["features"]

        print(f"Upgraded to preset: {args.preset}")
    else:
        # Enable individual feature
        feature = args.feature
        if "features" not in app_config:
            app_config["features"] = {}

        app_config["features"][feature] = True
        print(f"Enabled feature: {feature}")

    # Write back
    try:
        with open(app_json_path, "w", encoding="utf-8") as f:
            json.dump(app_config, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"\nUpdated {app_json_path}")
    except Exception as e:
        print(f"Error writing {app_json_path}: {e}")
        return

    # Show next steps
    _show_next_steps(args.preset or feature, app_root_label)


def _show_next_steps(feature_or_preset, app_root_label: str):
    """Show next steps after adding a feature."""
    print("\nNext Steps:")

    if feature_or_preset == "modules" or feature_or_preset in ["integrated", "full"]:
        print(f"  - Create modules in {app_root_label}/modules/<name>/")
        print("  - Each module needs: module.yaml, backend/handler.py, and any needed contracts/events.yaml, contracts/reactions.yaml, contracts/settings.yaml, contracts/notifications.yaml, and contracts/admin.yaml")

    if feature_or_preset == "event_bus" or feature_or_preset in ["integrated", "full"]:
        print("  - Use event_bus.publish() to emit app events")
        print("  - Add workflow triggers in orchestrator.yaml")

    if feature_or_preset == "auth" or feature_or_preset in ["integrated", "full"]:
        print("  - Add or review app/config/auth.yaml (schema_version: mozaiks.auth.v1)")
        print("  - Configure provider-neutral OIDC/JWT env handles in .env")
        print(f"  - Update authRequired in {app_root_label}/app.json")

    if feature_or_preset == "admin" or feature_or_preset == "full":
        print("  - Access admin portal at /admin (requires auth)")
        print(f"  - Configure {app_root_label}/app.json admins")

    if feature_or_preset == "chat_ui" or feature_or_preset in ["chat", "integrated", "full"]:
        print("  - Chat UI will be available at root path")
        print(f"  - Configure branding in {app_root_label}/brand/ and shell behavior in {app_root_label}/config/shell.json")

    print(
        "\nRestart the active host to apply changes, such as "
        "`python -m mozaiks serve . --host platform` or `python -m mozaiks studio --open`."
    )
