"""
mozaiks info - Show current configuration.
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
    """Execute the info command."""
    if args.available:
        _show_available_tiers()
    else:
        _show_current_config()


def _show_available_tiers():
    """Show all available tier presets."""
    print("Available Tier Presets:\n")

    descriptions = {
        "engine": "AI workflow runtime only (headless API)",
        "chat": "AI workflows + chat UI (chatbot builders)",
        "integrated": "AI + chat + modules + event bus + auth (SaaS builders)",
        "full": "Everything including admin and subscriptions (full product builders)",
    }

    for preset, features in TIER_PRESETS.items():
        print(f"  {preset:<12} - {descriptions[preset]}")
        enabled = [k for k, v in features.items() if v]
        print(f"               Features: {', '.join(enabled)}")
        print()


def _show_current_config():
    """Show current project configuration."""
    app_json_path = Path("platform") / "app.json"

    if not app_json_path.exists():
        print("No platform/app.json found.")
        print("Run 'mozaiks init <preset>' to create a new project.")
        return

    try:
        with open(app_json_path, "r", encoding="utf-8") as f:
            app_config = json.load(f)
    except Exception as e:
        print(f"Error reading platform/app.json: {e}")
        return

    # Extract current preset
    preset = app_config.get("preset", "full")
    app_name = app_config.get("appName", "Unknown")
    auth_required = app_config.get("authRequired", True)

    # Resolve features
    features = TIER_PRESETS.get(preset, TIER_PRESETS["full"]).copy()
    feature_overrides = app_config.get("features", {})
    features.update(feature_overrides)

    # Display
    print(f"Current Configuration:\n")
    print(f"  App Name:      {app_name}")
    print(f"  Preset:        {preset}")
    print(f"  Auth Required: {auth_required}")
    print(f"\nEnabled Features:")
    for feature, enabled in sorted(features.items()):
        status = "✓" if enabled else "✗"
        print(f"    {status} {feature}")

    # Show upgrade path if not full
    if preset != "full":
        print(f"\nTo enable more features, run:")
        print(f"  mozaiks add <feature>")
        print(f"  mozaiks add --preset <higher-tier>")
