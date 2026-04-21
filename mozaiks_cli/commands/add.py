"""
mozaiks add - Add feature to existing project.
"""

import json
from pathlib import Path

# Tier definitions
TIER_PRESETS = {
    "engine": {
        "ai_runtime": True,
        "operations": False,
        "event_bus": False,
        "auth": False,
        "admin": False,
        "chat_ui": False,
    },
    "chat": {
        "ai_runtime": True,
        "operations": False,
        "event_bus": False,
        "auth": False,
        "admin": False,
        "chat_ui": True,
    },
    "integrated": {
        "ai_runtime": True,
        "capabilities": True,
        "event_bus": True,
        "auth": True,
        "admin": False,
        "chat_ui": True,
    },
    "full": {
        "ai_runtime": True,
        "capabilities": True,
        "event_bus": True,
        "auth": True,
        "admin": True,
        "chat_ui": True,
    },
}


def run(args):
    """Execute the add command."""
    app_json_path = Path("platform") / "app.json"

    if not app_json_path.exists():
        print("Error: No platform/app.json found.")
        print("Run 'mozaiks init <preset>' to create a new project first.")
        return

    try:
        with open(app_json_path, "r", encoding="utf-8") as f:
            app_config = json.load(f)
    except Exception as e:
        print(f"Error reading platform/app.json: {e}")
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
        print(f"\nUpdated platform/app.json")
    except Exception as e:
        print(f"Error writing platform/app.json: {e}")
        return

    # Show next steps
    _show_next_steps(args.preset or feature)


def _show_next_steps(feature_or_preset):
    """Show next steps after adding a feature."""
    print("\nNext Steps:")

    if feature_or_preset == "operations" or feature_or_preset in ["integrated", "full"]:
        print("  - Create operations in platform/operations/<name>/")
        print("  - Each operation needs: operation.yaml, handler.py")

    if feature_or_preset == "event_bus" or feature_or_preset in ["integrated", "full"]:
        print("  - Use event_bus.publish() to emit app events")
        print("  - Add workflow triggers in orchestrator.yaml")

    if feature_or_preset == "auth" or feature_or_preset in ["integrated", "full"]:
        print("  - Configure Keycloak in .env (KEYCLOAK_* variables)")
        print("  - Update authRequired in platform/app.json")

    if feature_or_preset == "admin" or feature_or_preset == "full":
        print("  - Access admin portal at /admin (requires auth)")
        print("  - Configure admins list in platform/app.json")

    if feature_or_preset == "chat_ui" or feature_or_preset in ["chat", "integrated", "full"]:
        print("  - Chat UI will be available at root path")
        print("  - Configure branding in platform/brand/")

    print("\nRestart your dev server to apply changes.")
