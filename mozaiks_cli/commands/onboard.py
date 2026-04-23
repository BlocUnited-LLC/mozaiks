"""mozaiks onboard - Guided setup from the local Dev/CLI layer."""

from __future__ import annotations

import json
import sys
from pathlib import Path


THEME_PRESETS = {
    "teal": {
        "main": "#0f766e",
        "light": "#14b8a6",
        "dark": "#115e59",
    },
    "blue": {
        "main": "#1d4ed8",
        "light": "#60a5fa",
        "dark": "#1e40af",
    },
    "emerald": {
        "main": "#059669",
        "light": "#34d399",
        "dark": "#047857",
    },
    "slate": {
        "main": "#334155",
        "light": "#94a3b8",
        "dark": "#1e293b",
    },
    "amber": {
        "main": "#d97706",
        "light": "#fbbf24",
        "dark": "#b45309",
    },
    "rose": {
        "main": "#e11d48",
        "light": "#fb7185",
        "dark": "#be123c",
    },
}


PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4.1",
    "local": "qwen3:latest",
    "other": "set-me-explicitly",
}


def run(args) -> None:
    """Execute the onboard command."""
    workspace_root = _resolve_workspace_root(getattr(args, "directory", None))
    missing_surfaces = _missing_scaffold_surfaces(workspace_root)
    if missing_surfaces:
        print(f"Error: no valid Mozaiks scaffold found in {workspace_root}")
        print("Missing required files:")
        for rel_path in missing_surfaces:
            print(f"  - {rel_path}")
        print("Run 'mozaiks init <preset>' first or point --dir at an existing scaffold.")
        return

    app_json_path = workspace_root / "platform" / "app.json"
    ai_json_path = workspace_root / "platform" / "config" / "ai.json"
    shell_json_path = workspace_root / "platform" / "config" / "shell.json"
    theme_json_path = workspace_root / "brand" / "theme_config.json"
    admin_json_path = workspace_root / "platform" / "config" / "admin.json"

    app_config = _read_json(app_json_path)
    ai_config = _read_json(ai_json_path)
    shell_config = _read_json(shell_json_path)
    theme_config = _read_json(theme_json_path)
    admin_config = _read_json(admin_json_path) if admin_json_path.exists() else None

    should_prompt = not bool(getattr(args, "non_interactive", False)) and sys.stdin is not None and not sys.stdin.closed

    print(f"Onboarding Mozaiks app bundle at: {workspace_root}\n")

    default_name = app_config.get("appName") or workspace_root.name
    app_name = _prompt_text(
        label="App name",
        explicit=getattr(args, "name", None),
        default=default_name,
        should_prompt=should_prompt,
    )

    previous_onboarding = app_config.get("onboarding") or {}
    journey = _prompt_choice(
        label="What are you doing first?",
        explicit=getattr(args, "journey", None),
        default=previous_onboarding.get("journey") or "new_app",
        options=["new_app", "existing_app"],
        should_prompt=should_prompt,
    )

    goal_label = "What should Mozaiks help with first?"
    default_goal = previous_onboarding.get("first_goal") or (
        "Define the first real product capability" if journey == "new_app" else "Bridge the first useful host capability"
    )
    first_goal = _prompt_text(
        label=goal_label,
        explicit=getattr(args, "goal", None),
        default=default_goal,
        should_prompt=should_prompt,
    )

    existing_url = None
    host_owned_summary = None
    if journey == "existing_app":
        existing_url = _prompt_text(
            label="Existing app URL (optional)",
            explicit=getattr(args, "existing_url", None),
            default=previous_onboarding.get("existing_app_url") or "",
            should_prompt=should_prompt,
            allow_empty=True,
        )
        host_owned_summary = _prompt_text(
            label="What should stay host-owned? (optional)",
            explicit=getattr(args, "host_owned_summary", None),
            default=previous_onboarding.get("host_owned_summary") or "",
            should_prompt=should_prompt,
            allow_empty=True,
        )

    existing_llm = ai_config.get("llm") or {}
    provider = _prompt_choice(
        label="Which AI provider do you want to start with?",
        explicit=getattr(args, "provider", None),
        default=existing_llm.get("provider") or "anthropic",
        options=["anthropic", "openai", "local", "other"],
        should_prompt=should_prompt,
    )
    model = _prompt_text(
        label="Default model",
        explicit=getattr(args, "model", None),
        default=existing_llm.get("model") or PROVIDER_DEFAULT_MODELS[provider],
        should_prompt=should_prompt,
    )

    identity = theme_config.get("identity") or {}
    tagline = _prompt_text(
        label="Brand tagline (optional)",
        explicit=getattr(args, "tagline", None),
        default=identity.get("tagline") or "",
        should_prompt=should_prompt,
        allow_empty=True,
    )

    theme_root = theme_config.get("theme") or {}
    theme_primary = _prompt_choice(
        label="Primary brand color",
        explicit=getattr(args, "theme_primary", None),
        default=theme_root.get("primary") or "teal",
        options=list(THEME_PRESETS.keys()),
        should_prompt=should_prompt,
    )

    default_admin_email = ""
    if isinstance(admin_config, dict):
        admin_emails = admin_config.get("admin_emails") or []
        if admin_emails:
            default_admin_email = admin_emails[0]
    if not default_admin_email:
        admins = app_config.get("admins") or []
        if admins:
            default_admin_email = admins[0]

    admin_email = _prompt_text(
        label="Admin email (optional)",
        explicit=getattr(args, "admin_email", None),
        default=default_admin_email,
        should_prompt=should_prompt,
        allow_empty=True,
    )

    _apply_app_config(
        app_config=app_config,
        app_name=app_name,
        journey=journey,
        first_goal=first_goal,
        existing_url=existing_url,
        host_owned_summary=host_owned_summary,
        admin_email=admin_email,
    )
    _apply_ai_config(
        ai_config=ai_config,
        app_name=app_name,
        journey=journey,
        first_goal=first_goal,
        provider=provider,
        model=model,
    )
    _apply_shell_config(shell_config=shell_config, app_name=app_name)
    _apply_theme_config(
        theme_config=theme_config,
        app_name=app_name,
        tagline=tagline,
        theme_primary=theme_primary,
    )

    _write_json(app_json_path, app_config)
    print("Updated platform/app.json")
    _write_json(ai_json_path, ai_config)
    print("Updated platform/config/ai.json")
    _write_json(shell_json_path, shell_config)
    print("Updated platform/config/shell.json")
    _write_json(theme_json_path, theme_config)
    print("Updated brand/theme_config.json")

    if admin_email:
        resolved_admin = _apply_admin_config(admin_config or {}, admin_email)
        _write_json(admin_json_path, resolved_admin)
        print("Updated platform/config/admin.json")

    print("\nOnboarding completed.")
    _show_next_steps(workspace_root=workspace_root, journey=journey, first_goal=first_goal, admin_email=admin_email)


def _resolve_workspace_root(explicit_directory: str | None) -> Path:
    return Path(explicit_directory or ".").resolve()


def _missing_scaffold_surfaces(workspace_root: Path) -> list[str]:
    required = [
        "platform/app.json",
        "platform/config/ai.json",
        "platform/config/shell.json",
        "brand/theme_config.json",
    ]
    return [rel_path for rel_path in required if not (workspace_root / rel_path).exists()]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _prompt_text(
    *,
    label: str,
    explicit: str | None,
    default: str,
    should_prompt: bool,
    allow_empty: bool = False,
) -> str:
    if explicit is not None:
        value = explicit.strip()
        if value or allow_empty:
            return value
        return default

    if not should_prompt:
        return default

    prompt_suffix = f" [{default}]" if default else ""
    while True:
        try:
            response = input(f"{label}{prompt_suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            response = ""

        if response:
            return response
        if allow_empty:
            return default if default else ""
        if default:
            return default
        print("Please enter a value.")


def _prompt_choice(
    *,
    label: str,
    explicit: str | None,
    default: str,
    options: list[str],
    should_prompt: bool,
) -> str:
    if explicit is not None:
        normalized = explicit.strip().lower()
        if normalized in options:
            return normalized
        raise ValueError(f"Invalid value '{explicit}' for {label}. Expected one of: {', '.join(options)}")

    if not should_prompt:
        return default

    print(label)
    for index, option in enumerate(options, start=1):
        marker = " (default)" if option == default else ""
        print(f"  {index}. {option}{marker}")

    while True:
        try:
            response = input("Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            response = ""

        if not response:
            return default
        if response in options:
            return response
        if response.isdigit():
            index = int(response) - 1
            if 0 <= index < len(options):
                return options[index]
        print(f"Enter one of: {', '.join(options)}")


def _apply_app_config(
    *,
    app_config: dict,
    app_name: str,
    journey: str,
    first_goal: str,
    existing_url: str | None,
    host_owned_summary: str | None,
    admin_email: str,
) -> None:
    app_config["appName"] = app_name
    app_config.setdefault("startup", {}).setdefault("landing_spot", "/")
    onboarding = {
        "journey": journey,
        "first_goal": first_goal,
        "existing_app_url": existing_url or None,
        "host_owned_summary": host_owned_summary or None,
    }
    app_config["onboarding"] = onboarding

    admins = app_config.get("admins")
    if not isinstance(admins, list):
        admins = []
    if admin_email and admin_email not in admins:
        admins.append(admin_email)
    app_config["admins"] = admins


def _apply_ai_config(
    *,
    ai_config: dict,
    app_name: str,
    journey: str,
    first_goal: str,
    provider: str,
    model: str,
) -> None:
    ask = ai_config.setdefault("ask", {})
    ask["ask_mode_prompt"] = _build_ask_prompt(
        app_name=app_name,
        journey=journey,
        first_goal=first_goal,
    )

    ai_config.setdefault("chat", {}).setdefault("chat_startup_mode", "ask")
    ai_config.setdefault("workflows", {}).setdefault("entry_point", None)
    ai_config.setdefault("workflows", {}).setdefault(
        "resume_policy", "last_active_then_oldest_then_entry_point"
    )
    ai_config["llm"] = {
        "provider": provider,
        "model": model,
    }
    ai_config["app_context"] = {
        "journey": journey,
        "first_goal": first_goal,
    }


def _build_ask_prompt(*, app_name: str, journey: str, first_goal: str) -> str:
    if journey == "existing_app":
        return (
            f"You are the assistant for {app_name}. Help the builder augment an existing app safely. "
            f"Prioritize bridging and scoped adoption first. Current first goal: {first_goal}."
        )
    return (
        f"You are the assistant for {app_name}. Help the builder define the first real product capability "
        f"before generating files. Current first goal: {first_goal}."
    )


def _apply_shell_config(*, shell_config: dict, app_name: str) -> None:
    header = shell_config.setdefault("header", {})
    logo = header.setdefault("logo", {})
    logo.setdefault("src", "/assets/logo.svg")
    logo["alt"] = f"{app_name} logo"
    logo.setdefault("href", "/")

    shell_config.setdefault("profile", {}).setdefault("show", False)
    shell_config.setdefault("notifications", {}).setdefault("show", False)
    shell_config.setdefault("footer", {}).setdefault("visible", True)


def _apply_theme_config(*, theme_config: dict, app_name: str, tagline: str, theme_primary: str) -> None:
    theme = theme_config.setdefault("theme", {})
    theme["primary"] = theme_primary
    branding = theme.setdefault("branding", {})
    branding["app_name"] = app_name
    branding.setdefault("logo_url", "/assets/logo.svg")

    identity = theme_config.setdefault("identity", {})
    identity["name"] = app_name
    identity["app_name"] = app_name
    identity["tagline"] = tagline or identity.get("tagline") or "Mozaiks app bundle"

    theme_config.setdefault("assets", {}).setdefault("logo", "logo.svg")

    colors = theme_config.setdefault("colors", {})
    palette = THEME_PRESETS[theme_primary]
    colors["primary"] = {
        "main": palette["main"],
        "light": palette["light"],
        "dark": palette["dark"],
        "name": theme_primary,
    }
    border = colors.setdefault("border", {})
    border["accent"] = palette["main"]

    ui = theme_config.setdefault("ui", {})
    chat = ui.setdefault("chat", {})
    modes = chat.setdefault("modes", {})
    ask_mode = modes.setdefault("ask", {})
    ask_mode.setdefault("label", "Ask")
    ask_mode["tint"] = palette["main"]


def _apply_admin_config(admin_config: dict, admin_email: str) -> dict:
    admin_config["enabled"] = True
    emails = admin_config.get("admin_emails")
    if not isinstance(emails, list):
        emails = []
    if admin_email not in emails:
        emails.append(admin_email)
    admin_config["admin_emails"] = emails
    panels = admin_config.get("panels")
    if not isinstance(panels, dict):
        panels = {
            "app": [
                {"id": "stats", "label": "App Overview", "section": "overview"},
                {"id": "users", "label": "Users", "section": "users"},
            ],
            "modules": [],
            "runtime": [
                {"id": "stats", "label": "Usage Stats", "section": "usage"},
                {"id": "runs", "label": "Active Runs", "section": "usage"},
                {"id": "sessions", "label": "Recent Sessions", "section": "activity"},
            ],
        }
    admin_config["panels"] = panels
    admin_config.setdefault("roles", ["admin"])
    admin_config.setdefault(
        "features",
        {
            "user_management": False,
            "billing": False,
            "audit_log": False,
        },
    )
    return admin_config


def _show_next_steps(*, workspace_root: Path, journey: str, first_goal: str, admin_email: str) -> None:
    print("\nNext Steps:")
    print(f"  1. Review {workspace_root / 'platform' / 'app.json'} and confirm the onboarding summary")
    print("  2. Confirm your default AI provider and model in platform/config/ai.json")
    print("  3. Use the first goal below as your next build request:")
    print(f"     {first_goal}")
    print("  4. Start the local/private builder host with python run_studio.py")
    if journey == "existing_app":
        print("  5. Bridge the first host-owned surface before attempting broader generation")
    else:
        print("  5. Add the first real workflow or module only after you confirm the product surface")
    if admin_email:
        print("  6. Verify admin access in platform/config/admin.json")